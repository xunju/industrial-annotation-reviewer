"""Local annotation quality reviewer. Python standard library only."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import mimetypes
import os
from pathlib import Path, PurePosixPath
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
import webbrowser

APP = Path(__file__).resolve().parent
EXTENSIONS = {'.png', '.jpg', '.jpeg', '.bmp', '.webp'}


class APIError(Exception):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


@contextmanager
def operation_lock():
    """One OS lock shared by all reviewer processes for this local user.

    Always acquire the server's thread lock first, then this lock. A single
    file avoids parent/child-root lock ordering conflicts across open datasets.
    """
    path = Path(tempfile.gettempdir()) / 'annotation_quality_review-file-operations-v1.lock'
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    stream = os.fdopen(fd, 'r+b')
    acquired = False
    try:
        if os.fstat(stream.fileno()).st_size == 0:
            stream.write(b'0')
            stream.flush()
        deadline = time.monotonic() + 45
        if os.name == 'nt':
            import errno
            import msvcrt
            while True:
                stream.seek(0)
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    acquired = True
                    break
                except OSError as error:
                    if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                        raise
                    if time.monotonic() >= deadline:
                        raise APIError('其它审核窗口正在操作文件，请稍后重试', 409) from None
                    time.sleep(.05)
        else:
            import fcntl
            while True:
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise APIError('其它审核窗口正在操作文件，请稍后重试', 409) from None
                    time.sleep(.05)
        yield
    finally:
        if acquired:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


def now_utc():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def file_revision(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def deletion_area(root, create=False):
    area = root / '.review_deleted'
    if area.resolve() != area or area.is_symlink():
        raise APIError('删除记录目录不能是链接或位于数据集以外', 403)
    if create:
        area.mkdir(exist_ok=True)
    if not area.is_dir():
        raise APIError('没有可恢复的删除记录', 404)
    return area


def deletion_key(root, create=False):
    area = deletion_area(root, create)
    path = area / '.signing_key'
    if path.resolve() != path or path.is_symlink():
        raise APIError('删除记录密钥路径无效', 403)
    if create and not path.exists():
        fd, temporary = tempfile.mkstemp(prefix='.key-', dir=area)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(secrets.token_bytes(32))
                stream.flush()
                os.fsync(stream.fileno())
            # Windows rename refuses an existing destination. POSIX hard-link
            # creation provides the same exclusive installation semantics.
            if os.name == 'nt':
                try:
                    os.rename(temporary, path)
                except FileExistsError:
                    pass
            else:
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    pass
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    try:
        key = path.read_bytes()
    except OSError:
        raise APIError('删除记录密钥缺失，无法验证服务器签发记录', 403) from None
    if len(key) != 32:
        raise APIError('删除记录密钥无效', 403)
    return key


def record_bytes(record):
    unsigned = {k: v for k, v in record.items() if k != 'signature'}
    return json.dumps(unsigned, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def write_deletion_record(path, record, key):
    record['signature'] = hmac.new(key, record_bytes(record), hashlib.sha256).hexdigest()
    data = (json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
    fd, temporary = tempfile.mkstemp(prefix='.metadata-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def safe_relative_path(value):
    if not isinstance(value, str) or not value or '\\' in value or ':' in value:
        raise APIError('删除记录中的相对路径无效', 403)
    path = PurePosixPath(value)
    if not path.parts or path.is_absolute() or any(p in {'', '.', '..'} for p in path.parts) or path.as_posix() != value:
        raise APIError('删除记录中的相对路径越界', 403)
    return Path(*path.parts)


def load_deletion_record(root, deletion_id):
    if not isinstance(deletion_id, str) or len(deletion_id) != 32:
        raise APIError('删除记录编号无效', 403)
    try:
        if uuid.UUID(hex=deletion_id).hex != deletion_id:
            raise ValueError()
    except ValueError:
        raise APIError('删除记录编号无效', 403) from None
    directory = deletion_area(root) / deletion_id
    if directory.resolve() != directory or directory.is_symlink():
        raise APIError('删除记录路径无效', 403)
    path = directory / 'metadata.json'
    if path.resolve() != path or path.is_symlink():
        raise APIError('删除记录路径无效', 403)
    try:
        record = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise APIError('删除记录不存在或损坏', 404) from None
    key = deletion_key(root)
    if not isinstance(record, dict) or not isinstance(record.get('signature'), str) or not hmac.compare_digest(
            record['signature'], hmac.new(key, record_bytes(record), hashlib.sha256).hexdigest()):
        raise APIError('删除记录签名无效，已阻止恢复', 403)
    if record.get('generator') != 'annotation_quality_review' or record.get('schema') != 1 or record.get('deletion_id') != deletion_id or record.get('root') != str(root):
        raise APIError('删除记录不属于当前数据根目录', 403)
    if record.get('state') != 'deleted':
        raise APIError('该记录已经恢复或尚未完成删除，不能重复恢复', 409)
    if record.get('reason') != 'user_excluded_sample' or set(record.get('files', {})) != {'json', 'image'}:
        raise APIError('删除记录内容无效', 403)
    pairs = []
    for kind in ('json', 'image'):
        info = record['files'][kind]
        relative = safe_relative_path(info.get('relative_path'))
        original = root / relative
        archived = directory / 'files' / relative
        if inside(original, root) != original or inside(archived, directory) != archived or info.get('original_path') != str(original):
            raise APIError('删除记录目标路径发生变化或越界', 403)
        sha = info.get('sha256')
        if not isinstance(sha, str) or len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha):
            raise APIError('删除记录文件校验值无效', 403)
        if os.path.lexists(original):
            raise APIError('原位置已存在文件或目录，恢复不会覆盖，请先核对', 409)
        if not archived.is_file() or file_revision(archived) != sha:
            raise APIError('归档文件不存在或内容已变化，已阻止恢复', 409)
        pairs.append((archived, original))
    if pairs[0][1] == pairs[1][1] or pairs[0][1].suffix.lower() != '.json' or pairs[1][1].suffix.lower() not in EXTENSIONS:
        raise APIError('删除记录文件配对无效', 403)
    return path, record, key, pairs


def move_no_overwrite(source, target):
    if os.path.lexists(target):
        raise FileExistsError('目标位置已存在文件，已阻止覆盖')
    if os.name == 'nt':
        os.rename(source, target)
    else:
        os.link(source, target)
        try:
            source.unlink()
        except OSError:
            target.unlink()
            raise


def paired_transaction(path, record, key, pairs, operation, ready=None):
    moved = []
    record['state'] = 'pending'
    record['operation'] = operation
    write_deletion_record(path, record, key)
    try:
        for source, target in pairs:
            target.parent.mkdir(parents=True, exist_ok=True)
            move_no_overwrite(source, target)
            moved.append((source, target))
        result = ready() if ready else None
        record['state'] = 'deleted' if operation == 'delete' else 'restored'
        record['deleted_at' if operation == 'delete' else 'restored_at'] = now_utc()
        write_deletion_record(path, record, key)
        return result
    except Exception as error:
        rollback_errors = []
        for source, target in reversed(moved):
            try:
                move_no_overwrite(target, source)
            except OSError as rollback_error:
                rollback_errors.append(str(rollback_error))
        record['state'] = 'deleted' if operation == 'restore' and not rollback_errors else 'rollback_error'
        record.pop('restored_at' if operation == 'restore' else 'deleted_at', None)
        record['last_error'] = str(error)
        record['rollback_complete'] = not rollback_errors
        record['rollback_errors'] = rollback_errors
        try:
            write_deletion_record(path, record, key)
        except OSError:
            pass
        if rollback_errors:
            raise APIError('文件成对操作未完成，回滚失败；文件仍在原位置或删除记录目录，请保留该记录核对', 500) from error
        if isinstance(error, APIError):
            raise error
        raise APIError('文件成对操作未完成，已回滚，原入口保留：' + str(error), 500) from error


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
            washer_labels = {'wai', 'nei', 'shim', 'wai_1', 'wai_2', 'wai_3', 'nei_1', 'nei_2', 'nei_3'}
            supported = bool(labels & (washer_labels | {'screw', 'top_left', 'top_right', 'bot_left', 'bot_right'}))
            if not supported and doc['shapes']:
                skipped += 1
                continue
            key = path.relative_to(root).as_posix()
            entries[key] = (path, image)
            quality = doc.get('quality_review') or {}
            rows.append(dict(id=key, name=image.name, checked=quality.get('completed') is True,
                             reviewed=len(quality.get('confirmed', [])),
                             kind='混合' if labels & washer_labels and 'screw' in labels else
                             '垫片' if labels & washer_labels else '螺栓' if 'screw' in labels else '空标注'))
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
    quality = doc.get('quality_review')
    if not isinstance(quality, dict) or type(quality.get('completed')) is not bool:
        raise ValueError('质量审核进度无效')
    for key in ('confirmed', 'deferred'):
        if not isinstance(quality.get(key), list) or any(not isinstance(v, str) for v in quality[key]):
            raise ValueError('零件审核进度无效')


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


def make_server(port=8773, default=None):
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
            try:
                dataset = datasets[q['dataset'][0]]
                path, image = dataset['entries'][q['id'][0]]
            except KeyError:
                raise APIError('该图片入口已失效或已删除，请重新读取目录', 404) from None
            path, image = inside(path, dataset['root']), inside(image, dataset['root'])
            if not path.is_file() or not image.is_file():
                dataset['entries'].pop(q['id'][0], None)
                raise APIError('该图片已被其它窗口删除或文件配对失效，请重新读取目录', 404)
            return path, image

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
                    return self.respond(dict(token=token, presets=presets, default=str(default) if default else None,
                                             capabilities=dict(image_deletion=True)))
                if url.path == '/favicon.ico':
                    return self.respond(b'', code=204, mime='image/x-icon')
                if url.path == '/api/annotation':
                    with lock, operation_lock():
                        path, image = self.entry(q)
                        data = path.read_bytes()
                        image_sha = file_revision(image)
                    return self.respond(dict(annotation=json.loads(data.decode('utf-8-sig')),
                                             revision=revision(data), image_revision=image_sha))
                if url.path == '/api/image':
                    with lock, operation_lock():
                        _, image = self.entry(q)
                        data = image.read_bytes()
                    return self.respond(data, mime=mimetypes.guess_type(image)[0] or 'image/png')
                return self.respond({'error': '页面不存在'}, 404)
            except APIError as e:
                self.respond({'error': str(e)}, e.code)
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
                    with lock, operation_lock():
                        root, rows, entries, skipped = scan(Path(directory))
                        uid = secrets.token_urlsafe(18)
                        datasets[uid] = dict(root=root, entries=entries)
                    return self.respond(dict(dataset=uid, directory=str(root), images=rows, skipped=skipped))
                if self.path == '/api/delete-image':
                    with lock, operation_lock():
                        q = {k: [body[k]] for k in ('dataset', 'id')}
                        path, image = self.entry(q)
                        dataset = datasets[body['dataset']]
                        root = dataset['root']
                        raw_path = dataset['entries'][body['id']][0]
                        if raw_path.absolute() != path:
                            raise APIError('不能删除链接指向的标注文件', 403)
                        data = path.read_bytes()
                        if body.get('revision') != revision(data):
                            raise APIError('JSON 已变化，已阻止删除；请刷新当前图片后核对', 409)
                        image_sha = file_revision(image)
                        if body.get('image_revision') != image_sha:
                            raise APIError('图片已变化，已阻止删除；请刷新当前图片后核对', 409)
                        _, _, fresh_entries, _ = scan(root)
                        fresh_pair = fresh_entries.get(body['id'])
                        if not fresh_pair or tuple(p.resolve() for p in fresh_pair) != (path, image):
                            raise APIError('当前文件配对已变化，已阻止删除；请重新读取目录', 409)
                        all_entries = list(fresh_entries.values())
                        all_entries.extend(pair for session in datasets.values() for pair in session['entries'].values())
                        if any(other.resolve() != path and other_image.resolve() == image for other, other_image in all_entries):
                            raise APIError('这张图片被其它标注 JSON 共用，不能成对删除，请先核对共享标注', 409)
                        exclusion = dict(scope='current_pair')
                        deletion_id = uuid.uuid4().hex
                        key = deletion_key(root, create=True)
                        directory = deletion_area(root) / deletion_id
                        directory.mkdir()
                        json_relative = path.relative_to(root).as_posix()
                        image_relative = image.relative_to(root).as_posix()
                        record = dict(schema=1, generator='annotation_quality_review', deletion_id=deletion_id,
                                      root=str(root), id=body['id'], state='pending', created_at=now_utc(),
                                      reason='user_excluded_sample', exclusion=exclusion,
                                      files=dict(json=dict(original_path=str(path), relative_path=json_relative, sha256=revision(data)),
                                                 image=dict(original_path=str(image), relative_path=image_relative, sha256=image_sha)))
                        pairs = [(path, directory / 'files' / safe_relative_path(json_relative)),
                                 (image, directory / 'files' / safe_relative_path(image_relative))]
                        def archived_checks():
                            if any(file_revision(target) != record['files'][kind]['sha256']
                                   for kind, (_, target) in zip(('json', 'image'), pairs)):
                                raise APIError('文件在删除过程中被其它软件修改，已成对回滚并阻止删除，请刷新后核对', 409)
                        paired_transaction(directory / 'metadata.json', record, key, pairs, 'delete', archived_checks)
                        # All sessions lose stale read/save entries only after both
                        # files and the signed deletion record commit successfully.
                        for session in datasets.values():
                            for row_id, pair in list(session['entries'].items()):
                                if pair[0].resolve() == path or pair[1].resolve() == image:
                                    del session['entries'][row_id]
                        return self.respond(dict(ok=True, deletion_id=deletion_id, id=body['id'],
                                                 json_relative_path=json_relative, image_relative_path=image_relative,
                                                 remaining_count=len(dataset['entries'])))
                if self.path == '/api/restore-image':
                    with lock, operation_lock():
                        dataset = datasets.get(body['dataset'])
                        if dataset is None:
                            raise APIError('数据集入口已失效，请重新读取目录', 404)
                        root = dataset['root']
                        metadata, record, key, pairs = load_deletion_record(root, body['deletion_id'])
                        restored_id = record['files']['json']['relative_path']
                        def restored_scan():
                            if any(file_revision(target) != record['files'][kind]['sha256']
                                   for kind, (_, target) in zip(('json', 'image'), pairs)):
                                raise APIError('文件在恢复过程中发生变化，已成对回滚并阻止恢复', 409)
                            result = scan(root)
                            if restored_id not in result[2] or tuple(p.resolve() for p in result[2][restored_id]) != tuple(p[1] for p in pairs):
                                raise ValueError('恢复后的标注与图片未能重新配对')
                            return result
                        _, rows, restored_entries, _ = paired_transaction(metadata, record, key, pairs, 'restore', restored_scan)
                        for session in datasets.values():
                            if session['root'] == root:
                                session['entries'] = dict(restored_entries)
                            elif all(p[1].is_relative_to(session['root']) for p in pairs):
                                session['entries'][pairs[0][1].relative_to(session['root']).as_posix()] = tuple(p[1] for p in pairs)
                        row = next(row for row in rows if row['id'] == restored_id)
                        return self.respond(dict(ok=True, deletion_id=body['deletion_id'], id=restored_id, row=row,
                                                 json_relative_path=record['files']['json']['relative_path'],
                                                 image_relative_path=record['files']['image']['relative_path'],
                                                 remaining_count=len(dataset['entries'])))
                if self.path == '/api/save':
                    q = {k: [body[k]] for k in ('dataset', 'id')}
                    with lock, operation_lock():
                        path, _ = self.entry(q)
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
            except APIError as e:
                self.respond({'error': str(e)}, e.code)
            except (KeyError, ValueError, TypeError, OSError, subprocess.SubprocessError) as e:
                self.respond({'error': str(e)}, 400)

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8773)
    parser.add_argument('--dataset', type=Path)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    server = make_server(args.port, args.dataset)
    url = f'http://127.0.0.1:{server.server_port}/'
    print(f'标注质量审核：{url}', flush=True)
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
