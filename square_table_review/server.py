"""Local desk bounding-box and four-corner reviewer, with independent quadrilateral validation."""
from __future__ import annotations

import argparse
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
from urllib.parse import parse_qs, urlparse
import webbrowser

import geometry

APP = Path(__file__).resolve().parent
CONFIG = json.loads((APP/'config.json').read_text(encoding='utf-8'))
EXTENSIONS = {'.png', '.jpg', '.jpeg', '.bmp', '.webp'}


def revision(data):
    return hashlib.sha256(data).hexdigest()


def shape_signature(doc):
    value = {k: doc.get(k) for k in ('shapes', 'imageWidth', 'imageHeight')}
    return revision(json.dumps(value, ensure_ascii=False, sort_keys=True,
                               separators=(',', ':'), allow_nan=False).encode())


def review_valid(doc):
    quality = doc.get('square_table_review') or {}
    return isinstance(quality, dict) and quality.get('completed') is True and quality.get('shape_signature') == shape_signature(doc)


def inside(path, root):
    path = path.resolve()
    if not path.is_relative_to(root):
        raise ValueError('文件不在所选目录内')
    return path


def scan(root):
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError('目录不存在，请选择图片与 JSON 所在的目录')
    images, by_name, json_paths = {}, {}, []
    for folder, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d not in {'node_modules', '__pycache__', 'backups'} and 'history' not in d)
        for name in sorted(files):
            path = Path(folder) / name
            if path.suffix.lower() in EXTENSIONS:
                images[str(path).casefold()] = path
                by_name.setdefault(path.name.casefold(), []).append(path)
            elif path.suffix.lower() == '.json':
                json_paths.append(path)
    rows, entries, skipped = [], {}, 0
    for candidate in sorted(json_paths):
        try:
            path = inside(candidate, root)
            if path.stat().st_size > 10_000_000:
                skipped += 1
                continue
            doc = json.loads(path.read_text(encoding='utf-8-sig'))
            if not isinstance(doc, dict) or not isinstance(doc.get('shapes'), list):
                continue
            image_path = str(doc.get('imagePath', '')).replace('\\', '/')
            options = [path.parent/image_path, root/image_path]
            options.extend(path.with_suffix(ext) for ext in sorted(EXTENSIONS))
            names = by_name.get(Path(image_path).name.casefold(), [])
            if len(names) == 1:
                options.extend(names)
            image = None
            for option in options:
                normalized = Path(os.path.abspath(option))
                found = images.get(str(normalized).casefold())
                if found:
                    image = inside(found, root)
                    break
            if image is None:
                skipped += 1
                continue
            key = path.relative_to(root).as_posix()
            entries[key] = (path, image)
            labels = {s.get('label') for s in doc['shapes'] if isinstance(s, dict)}
            rows.append(dict(id=key, name=image.name, checked=review_valid(doc),
                             kind='方桌' if 'desk' in labels else '待补方桌'))
        except (ValueError, OSError, TypeError, AttributeError):
            skipped += 1
    return root, rows, entries, skipped


def validate(doc, old, saving=False):
    if not isinstance(doc, dict) or any(doc.get(k) != old.get(k) for k in ('imagePath', 'imageWidth', 'imageHeight')):
        raise ValueError('原图片名称与尺寸不能改变')
    if any(type(doc.get(k)) not in (int, float) or not math.isfinite(doc[k]) or doc[k] <= 0 for k in ('imageWidth', 'imageHeight')):
        raise ValueError('图片尺寸无效')
    shapes = doc.get('shapes')
    if not isinstance(shapes, list) or len(shapes) > 5000:
        raise ValueError('标注数量无效')
    for shape in shapes:
        if not isinstance(shape, dict) or not isinstance(shape.get('label'), str) or not isinstance(shape.get('points'), list):
            raise ValueError('标注格式无效')
        if any(not geometry.point(p) or any(abs(v)>100000 for v in p) for p in shape['points']):
            raise ValueError('标注坐标无效')
    if saving:
        quality = doc.get('square_table_review')
        if not isinstance(quality, dict) or type(quality.get('completed')) is not bool or type(doc.get('checked')) is not bool:
            raise ValueError('方桌审核进度无效')
        if doc['checked'] != quality['completed']:
            raise ValueError('审核完成状态不一致')
        if quality['completed']:
            report = geometry.check(doc)
            if not report['ok']:
                reasons = report['issues'] + [issue for t in report['tables'] for issue in t['issues']]
                raise ValueError('不能确认：' + '；'.join(dict.fromkeys(reasons)))


def choose_directory():
    try:
        options = dict(capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)
        if sys.platform == 'win32':
            options['creationflags'] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run([sys.executable, str(APP/'directory_picker.py'), str(APP.parent)], **options)
    except subprocess.TimeoutExpired:
        raise ValueError('目录选择超时，已关闭窗口。请重试，或使用“输入路径”。') from None
    if result.returncode:
        raise ValueError('系统目录选择器不可用，请在页面中输入目录路径')
    directory = json.loads(result.stdout)
    if not isinstance(directory, str):
        raise ValueError('目录选择结果无效')
    return directory or None


def make_server(port=8775, default=None):
    datasets, save_lock, picker_lock = {}, threading.Lock(), threading.Lock()
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            if len(args)>1 and str(args[1]).startswith(('4','5')):
                super().log_message(fmt, *args)

        def respond(self, value, code=200, mime='application/json; charset=utf-8'):
            data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def trusted(self):
            host = self.headers.get('Host', '')
            origin = self.headers.get('Origin')
            return urlparse('http://'+host).hostname in {'127.0.0.1','localhost'} and (not origin or urlparse(origin).netloc==host)

        def entry(self, q):
            dataset = datasets[q['dataset'][0]]
            path, image = dataset['entries'][q['id'][0]]
            return inside(path, dataset['root']), inside(image, dataset['root'])

        def do_GET(self):
            if not self.trusted():
                return self.respond({'error':'仅接受本机请求'},403)
            url = urlparse(self.path)
            q = parse_qs(url.query)
            try:
                if url.path in {'/','/index.html','/style.css','/model.js','/app.js'}:
                    file = APP/('index.html' if url.path=='/' else url.path[1:])
                    return self.respond(file.read_bytes(),mime=(mimetypes.guess_type(file)[0] or 'text/plain')+'; charset=utf-8')
                if url.path=='/api/config':
                    reference = {'description': 'Independent OpenCV quadrilateral validation'}
                    presets = []
                    core = geometry.quad_geometry
                    return self.respond(dict(app=CONFIG['app'],token=token,presets=presets,default=str(default) if default else None,
                                             geometry=dict(min_area_ratio=core.MIN_AREA_RATIO,max_area_ratio=core.MAX_AREA_RATIO,min_corner_distance=core.MIN_CORNER_DISTANCE,max_side_ratio=core.MAX_SIDE_RATIO,max_homography_condition=core.MAX_HOMOGRAPHY_COND,destination_size=CONFIG['preview']['width']),reference=reference))
                if url.path=='/api/annotation':
                    path,_ = self.entry(q)
                    raw = path.read_bytes()
                    doc = json.loads(raw.decode('utf-8-sig'))
                    return self.respond(dict(annotation=doc,revision=revision(raw),review_valid=review_valid(doc)))
                if url.path=='/api/image':
                    _,image = self.entry(q)
                    return self.respond(image.read_bytes(),mime=mimetypes.guess_type(image)[0] or 'image/png')
                if url.path=='/favicon.ico':
                    return self.respond(b'',204,'image/x-icon')
                return self.respond({'error':'页面不存在'},404)
            except (KeyError,ValueError,OSError,TypeError) as exc:
                self.respond({'error':str(exc)},400)

        def do_POST(self):
            if not self.trusted() or self.headers.get('X-Review-Token')!=token:
                return self.respond({'error':'请求来源无效，请刷新页面'},403)
            if not self.headers.get('Content-Type','').startswith('application/json'):
                return self.respond({'error':'需要 JSON 请求'},415)
            try:
                length = int(self.headers.get('Content-Length',0))
                if not 0<length<=10_000_000:
                    raise ValueError('请求大小无效')
                body = json.loads(self.rfile.read(length))
                if self.path in {'/api/open','/api/pick'}:
                    if self.path=='/api/pick':
                        if not picker_lock.acquire(blocking=False):
                            raise ValueError('已有目录选择窗口，请先完成或取消当前选择')
                        try:
                            directory = choose_directory()
                        finally:
                            picker_lock.release()
                    else:
                        directory = body['path']
                    if not directory:
                        return self.respond(dict(cancelled=True))
                    root,rows,entries,skipped = scan(Path(directory))
                    uid = secrets.token_urlsafe(18)
                    datasets[uid] = dict(root=root,entries=entries)
                    return self.respond(dict(dataset=uid,directory=str(root),images=rows,skipped=skipped))
                if self.path in {'/api/save','/api/geometry','/api/preview'}:
                    path,image = self.entry({key:[body[key]] for key in ('dataset','id')})
                    with save_lock:
                        old = path.read_bytes()
                        if self.path=='/api/save' and body.get('revision')!=revision(old):
                            return self.respond({'error':'JSON 已被其他窗口或软件修改。已阻止覆盖；请保留页面并导出草稿。'},409)
                        before = json.loads(old.decode('utf-8-sig'))
                        doc = body['annotation']
                        validate(doc,before,saving=self.path=='/api/save')
                        if self.path=='/api/geometry':
                            return self.respond(geometry.check(doc))
                        if self.path=='/api/preview':
                            png,report = geometry.preview(image,body['table'],doc)
                            return self.respond(dict(ok=True,image='data:image/png;base64,'+base64.b64encode(png).decode('ascii'),geometry=report))
                        doc['square_table_review']['version'] = 1
                        doc['square_table_review']['shape_signature'] = shape_signature(doc)
                        raw = (json.dumps(doc,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode()
                        fd,temp = tempfile.mkstemp(prefix='.'+path.stem,suffix='.tmp',dir=path.parent)
                        try:
                            with os.fdopen(fd,'wb') as file:
                                file.write(raw)
                                file.flush()
                                os.fsync(file.fileno())
                            os.chmod(temp,path.stat().st_mode&0o777)
                            os.replace(temp,path)
                        finally:
                            if os.path.exists(temp):
                                os.unlink(temp)
                    return self.respond(dict(ok=True,revision=revision(raw),annotation=doc,review_valid=review_valid(doc)))
                return self.respond({'error':'页面不存在'},404)
            except (KeyError,ValueError,OSError,TypeError,subprocess.SubprocessError) as exc:
                self.respond({'error':str(exc)},400)

    return ThreadingHTTPServer(('127.0.0.1',port),Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=CONFIG['server']['port'])
    parser.add_argument('--dataset',type=Path)
    parser.add_argument('--no-browser',action='store_true')
    args = parser.parse_args()
    server = make_server(args.port,args.dataset)
    url = f'http://127.0.0.1:{server.server_port}/'
    print('方桌四角审核：'+url,flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__=='__main__':
    main()
