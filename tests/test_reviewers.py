import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'square_table_review'))
from examples.generate_demo import generate
import geometry


def load_server(name, folder):
    spec = importlib.util.spec_from_file_location(name, ROOT / folder / 'server.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SERVERS = {
    'round': load_server('round_server', 'round_table_review'),
    'square': load_server('square_server', 'square_table_review'),
    'parts': load_server('parts_server', 'annotation_quality_review'),
}


class ReviewerHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        generate(self.root)
        self.running = []

    def tearDown(self):
        for server, thread in self.running:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.temporary.cleanup()

    def start(self, name):
        server = SERVERS[name].make_server(0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.running.append((server, thread))
        base = f'http://127.0.0.1:{server.server_port}'
        token = self.request(base + '/api/config')['token']
        opened = self.request(base + '/api/open', {'path': str(self.root / name)}, token)
        self.assertEqual(len(opened['images']), 1)
        return base, token, dict(dataset=opened['dataset'], id=opened['images'][0]['id'])

    @staticmethod
    def request(url, body=None, token=None, headers=None):
        headers = dict(headers or {})
        if token:
            headers['X-Review-Token'] = token
        if body is not None:
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                         headers=headers)
        with urllib.request.urlopen(request, timeout=5) as response:
            data = response.read()
            return json.loads(data) if 'application/json' in response.headers.get('Content-Type', '') else data

    def test_load_save_and_preserve_unrelated_metadata(self):
        for name in SERVERS:
            with self.subTest(tool=name):
                base, token, pair = self.start(name)
                original = self.request(base + '/api/annotation?' + urllib.parse.urlencode(pair))
                doc = copy.deepcopy(original['annotation'])
                shapes = copy.deepcopy(doc['shapes'])
                doc['description'] = 'Synthetic metadata remains editable'
                doc['checked'] = False
                field = {'round': 'round_table_review', 'square': 'square_table_review', 'parts': 'quality_review'}[name]
                doc[field] = dict(completed=False, shape_signature='draft', confirmed=[], deferred=[])
                result = self.request(base + '/api/save', dict(**pair, revision=original['revision'], annotation=doc), token)
                self.assertTrue(result['ok'])
                saved = json.loads((self.root / name / 'synthetic.json').read_text(encoding='utf-8'))
                self.assertEqual(saved['shapes'], shapes)
                self.assertEqual(saved['description'], doc['description'])

    def test_stale_revision_cannot_overwrite_external_change(self):
        for name in SERVERS:
            with self.subTest(tool=name):
                base, token, pair = self.start(name)
                original = self.request(base + '/api/annotation?' + urllib.parse.urlencode(pair))
                path = self.root / name / 'synthetic.json'
                external = dict(original['annotation'], description='Changed externally')
                path.write_text(json.dumps(external), encoding='utf-8')
                with self.assertRaises(urllib.error.HTTPError) as error:
                    self.request(base + '/api/save', dict(**pair, revision=original['revision'], annotation=original['annotation']), token)
                self.assertEqual(error.exception.code, 409)
                self.assertEqual(json.loads(path.read_text())['description'], 'Changed externally')

    def test_requests_need_local_origin_and_session_token(self):
        for name in SERVERS:
            with self.subTest(tool=name):
                base, token, _ = self.start(name)
                for headers, supplied_token in [({}, None), ({'Origin': 'https://example.com'}, token)]:
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        self.request(base + '/api/open', {'path': str(self.root)}, supplied_token, headers)
                    self.assertEqual(error.exception.code, 403)

    def test_parts_deletion_restores_exact_image_and_annotation(self):
        base, token, pair = self.start('parts')
        original = self.request(base + '/api/annotation?' + urllib.parse.urlencode(pair))
        folder = self.root / 'parts'
        before = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}
        removed = self.request(base + '/api/delete-image', dict(**pair, revision=original['revision'],
                               image_revision=original['image_revision']), token)
        self.assertFalse((folder / 'synthetic.png').exists())
        self.assertFalse((folder / 'synthetic.json').exists())
        self.request(base + '/api/restore-image', dict(dataset=pair['dataset'], deletion_id=removed['deletion_id']), token)
        for name, content in before.items():
            self.assertEqual((folder / name).read_bytes(), content)

    def test_restore_does_not_overwrite_new_image(self):
        base, token, pair = self.start('parts')
        original = self.request(base + '/api/annotation?' + urllib.parse.urlencode(pair))
        removed = self.request(base + '/api/delete-image', dict(**pair, revision=original['revision'],
                               image_revision=original['image_revision']), token)
        image = self.root / 'parts' / 'synthetic.png'
        image.write_bytes(b'new sample must not be overwritten')
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request(base + '/api/restore-image', dict(dataset=pair['dataset'], deletion_id=removed['deletion_id']), token)
        self.assertEqual(error.exception.code, 409)
        self.assertEqual(image.read_bytes(), b'new sample must not be overwritten')

    def test_square_preview_and_rejection_of_invalid_corners(self):
        import cv2
        import numpy as np
        doc = json.loads((self.root / 'square' / 'synthetic.json').read_text())
        report = geometry.check(doc)
        self.assertTrue(report['ok'], report)
        preview, _ = geometry.preview(self.root / 'square' / 'synthetic.png', report['tables'][0]['id'], doc)
        decoded = cv2.imdecode(np.frombuffer(preview, dtype=np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(decoded.shape[:2], (640, 640))
        doc['shapes'][2]['points'] = doc['shapes'][3]['points']
        self.assertFalse(geometry.check(doc)['ok'])


if __name__ == '__main__':
    unittest.main()
