"""Local round-table contour reviewer. Python standard library only."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import mimetypes
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
import webbrowser

APP = Path(__file__).resolve().parent
EXTENSIONS = {'.png', '.jpg', '.jpeg', '.bmp', '.webp'}


def revision(data):
    return hashlib.sha256(data).hexdigest()


def inside(path, root):
    path = path.resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise ValueError('文件不在所选目录内')
    return path


def scan(root):
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError('目录不存在，请选择图片与 JSON 所在的目录')
    images = {}
    json_paths = []
    for folder, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and
                         d not in {'backups', 'node_modules', '__pycache__'} and 'history' not in d)
        for name in sorted(files):
            path = Path(folder) / name
            if path.suffix.lower() in EXTENSIONS:
                images.setdefault(path.name.lower(), []).append(path)
            elif path.suffix.lower() == '.json':
                json_paths.append(path)
    rows, entries, skipped = [], {}, 0
    for path in sorted(json_paths):
        try:
            inside(path, root)
            if path.stat().st_size > 10_000_000:
                continue
            doc = json.loads(path.read_text(encoding='utf-8-sig'))
            if not isinstance(doc, dict) or not isinstance(doc.get('shapes'), list):
                continue
            image_path = str(doc.get('imagePath', '')).replace('\\', '/')
            image_name = Path(image_path).name
            choices = [path.parent / image_path, root / image_path]
            choices.extend(path.with_suffix(ext) for ext in EXTENSIONS)
            matching = images.get(image_name.lower(), [])
            if len(matching) == 1:
                choices.extend(matching)
            image = next((inside(p, root) for p in choices if p.is_file() and
                          p.suffix.lower() in EXTENSIONS and p.resolve().is_relative_to(root)), None)
            if image is None:
                skipped += 1
                continue
            labels = {s.get('label') for s in doc['shapes'] if isinstance(s, dict)}
            supported = bool(labels & {'circle', 'round', 'round_table', 'table_circle', '圆桌', '圆桌轮廓'})
            # Keep images whose table contour is missing, even if other labels exist.
            key = path.relative_to(root).as_posix()
            entries[key] = (path, image)
            quality = doc.get('round_table_review') or {}
            rows.append(dict(id=key, name=image.name, checked=quality.get('completed') is True,
                             reviewed=0, kind='圆桌' if supported else '待补圆桌'))
        except (ValueError, OSError, TypeError, AttributeError):
            skipped += 1
    return root, rows, entries, skipped


def validate(doc, old):
    if not isinstance(doc, dict) or any(doc.get(k) != old.get(k) for k in
                                       ('imagePath', 'imageWidth', 'imageHeight')):
        raise ValueError('原图片名称与尺寸不能改变')
    if not isinstance(doc.get('checked'), bool):
        raise ValueError('审核状态无效')
    shapes = doc.get('shapes')
    if not isinstance(shapes, list) or len(shapes) > 5000:
        raise ValueError('标注数量无效')
    for s in shapes:
        if not isinstance(s, dict) or not isinstance(s.get('label'), str) or not isinstance(s.get('points'), list):
            raise ValueError('标注格式无效')
        for p in s['points']:
            if not isinstance(p, list) or len(p) != 2 or any(type(v) not in (int, float) or
                    not math.isfinite(v) or abs(v) > 100000 for v in p):
                raise ValueError('标注坐标无效')
    quality = doc.get('round_table_review')
    if not isinstance(quality, dict) or type(quality.get('completed')) is not bool:
        raise ValueError('质量审核进度无效')
    if not isinstance(quality.get('shape_signature'), str):
        raise ValueError('圆桌审核进度无效')


def choose_directory():
    if sys.platform == 'darwin':
        p = subprocess.run(['osascript', '-e',
                            'POSIX path of (choose folder with prompt "选择要审核的图片和 JSON 目录")'],
                           capture_output=True, text=True, timeout=120)
        return p.stdout.strip() if p.returncode == 0 else None
    # Run Tk on the GUI child's main thread, with an explicit topmost owner.
    options = dict(capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)
    if sys.platform == 'win32':
        options['creationflags'] = subprocess.CREATE_NO_WINDOW
    try:
        p = subprocess.run([sys.executable, str(APP / 'directory_picker.py'), str(APP.parent)], **options)
    except subprocess.TimeoutExpired:
        raise ValueError('目录选择超时，已关闭选择窗口。请重试，或使用“输入路径”。') from None
    if p.returncode:
        raise ValueError('系统目录选择器不可用，请在页面中输入目录路径')
    try:
        directory = json.loads(p.stdout)
    except ValueError:
        raise ValueError('未能读取所选目录，请使用“输入路径”。') from None
    if not isinstance(directory, str):
        raise ValueError('目录选择结果无效，请使用“输入路径”。')
    return directory or None


def make_server(port=8774, default=None):
    datasets, lock, token = {}, threading.Lock(), secrets.token_urlsafe(32)
    picker_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            if len(args) > 1 and str(args[1]).startswith(('4', '5')):
                super().log_message(fmt, *args)

        def respond(self, value, code=200, mime='application/json; charset=utf-8'):
            data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(data)

        def trusted(self):
            host = self.headers.get('Host', '')
            hostname = urlparse('http://' + host).hostname
            origin = self.headers.get('Origin')
            return hostname in {'127.0.0.1', 'localhost'} and (not origin or
                    urlparse(origin).netloc == host)

        def entry(self, q):
            dataset = datasets[q['dataset'][0]]
            path, image = dataset['entries'][q['id'][0]]
            return inside(path, dataset['root']), inside(image, dataset['root'])

        def do_GET(self):
            if not self.trusted():
                return self.respond({'error': '仅接受本机请求'}, 403)
            url = urlparse(self.path)
            q = parse_qs(url.query)
            try:
                if url.path in {'/', '/index.html', '/app.js', '/model.js', '/style.css'}:
                    file = APP / ('index.html' if url.path == '/' else url.path[1:])
                    return self.respond(file.read_bytes(), mime=mimetypes.guess_type(file)[0] or 'text/plain')
                if url.path == '/api/config':
                    presets = []
                    return self.respond(dict(token=token, presets=presets, default=str(default) if default else None))
                if url.path == '/favicon.ico':
                    return self.respond(b'', code=204, mime='image/x-icon')
                if url.path == '/api/annotation':
                    path, _ = self.entry(q)
                    data = path.read_bytes()
                    return self.respond(dict(annotation=json.loads(data.decode('utf-8-sig')), revision=revision(data)))
                if url.path == '/api/image':
                    _, image = self.entry(q)
                    return self.respond(image.read_bytes(), mime=mimetypes.guess_type(image)[0] or 'image/png')
                return self.respond({'error': '页面不存在'}, 404)
            except (KeyError, ValueError, OSError) as e:
                self.respond({'error': str(e)}, 400)

        def do_POST(self):
            if not self.trusted() or self.headers.get('X-Review-Token') != token:
                return self.respond({'error': '请求来源无效，请刷新页面'}, 403)
            if not self.headers.get('Content-Type', '').startswith('application/json'):
                return self.respond({'error': '需要 JSON 请求'}, 415)
            try:
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length <= 10_000_000:
                    raise ValueError('请求大小无效')
                body = json.loads(self.rfile.read(length))
                if self.path in {'/api/open', '/api/pick'}:
                    if self.path == '/api/pick':
                        if not picker_lock.acquire(blocking=False):
                            raise ValueError('已有目录选择窗口，请先完成或取消当前选择。')
                        try:
                            directory = choose_directory()
                        finally:
                            picker_lock.release()
                    else:
                        directory = body['path']
                    if not directory:
                        return self.respond(dict(cancelled=True))
                    root, rows, entries, skipped = scan(Path(directory))
                    uid = secrets.token_urlsafe(18)
                    datasets[uid] = dict(root=root, entries=entries)
                    return self.respond(dict(dataset=uid, directory=str(root), images=rows, skipped=skipped))
                if self.path == '/api/save':
                    q = {k: [body[k]] for k in ('dataset', 'id')}
                    path, _ = self.entry(q)
                    with lock:
                        old = path.read_bytes()
                        if body.get('revision') != revision(old):
                            return self.respond({'error': 'JSON 已被其他窗口或软件修改。已阻止覆盖；请保留当前页面并导出草稿。'}, 409)
                        doc = body['annotation']
                        validate(doc, json.loads(old.decode('utf-8-sig')))
                        data = (json.dumps(doc, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()
                        # Same-directory atomic replacement; no extra annotation/history files.
                        fd, temporary = tempfile.mkstemp(prefix='.' + path.stem, suffix='.tmp', dir=path.parent)
                        try:
                            with os.fdopen(fd, 'wb') as f:
                                f.write(data)
                                f.flush()
                                os.fsync(f.fileno())
                            os.chmod(temporary, path.stat().st_mode & 0o777)
                            os.replace(temporary, path)
                        finally:
                            if os.path.exists(temporary):
                                os.unlink(temporary)
                    return self.respond(dict(ok=True, revision=revision(data)))
                self.respond({'error': '页面不存在'}, 404)
            except (KeyError, ValueError, TypeError, OSError, subprocess.SubprocessError) as e:
                self.respond({'error': str(e)}, 400)

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8774)
    parser.add_argument('--dataset', type=Path)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    server = make_server(args.port, args.dataset)
    url = f'http://127.0.0.1:{server.server_port}/'
    print(f'圆桌轮廓审核：{url}', flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
