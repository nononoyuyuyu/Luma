import contextlib
import hashlib
import json
import logging
import os
import re
import sqlite3
import stat
import threading
import time
import uuid
import warnings
from pathlib import Path

from PIL import Image, ImageOps
from . import video

log = logging.getLogger(__name__)
EXTENSIONS = video.EXTENSIONS | {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.avif', '.bmp', '.tif', '.tiff'}
FORMATS = {'JPEG', 'PNG', 'WEBP', 'GIF', 'AVIF', 'BMP', 'TIFF'}
Image.MAX_IMAGE_PIXELS = 80_000_000
warnings.simplefilter('error', Image.DecompressionBombWarning)


class LibraryError(Exception):
    pass


def linked(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def valid_name(name: str) -> str:
    if (not name or len(name) > 180 or name in {'.', '..'} or name.endswith((' ', '.'))
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
            or name.startswith('.')
            or name.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *{f'COM{i}' for i in range(1, 10)}, *{f'LPT{i}' for i in range(1, 10)}}):
        raise LibraryError('使用できない名前です。区切り文字・予約名・末尾の空白は使えません。')
    return name


class Library:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir.resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cache = self.data_dir / 'cache'
        self.cache.mkdir(exist_ok=True)
        self.db_path = self.data_dir / 'library.sqlite3'
        self.lock = threading.RLock()
        self.scan_lock = threading.Lock()
        self.render_slots = threading.BoundedSemaphore(2)
        self.render_locks = [threading.Lock() for _ in range(64)]
        self.folder_cache = None
        self._root_value = None
        self.scan_state = {'running': False, 'last_scan': None, 'processed': 0, 'errors': 0, 'message': '', 'revision': 0}
        with self.db() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS images (
                    id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, folder TEXT NOT NULL, name TEXT NOT NULL,
                    size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, identity TEXT,
                    width INTEGER NOT NULL, height INTEGER NOT NULL, format TEXT NOT NULL,
                    animated INTEGER NOT NULL DEFAULT 0, favorite INTEGER NOT NULL DEFAULT 0,
                    available INTEGER NOT NULL DEFAULT 1, added REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS image_folder ON images(folder, available);
                CREATE INDEX IF NOT EXISTS image_date ON images(available, mtime_ns DESC, id);
                CREATE INDEX IF NOT EXISTS image_identity ON images(identity);
                CREATE TABLE IF NOT EXISTS tags (image_id TEXT NOT NULL REFERENCES images(id) ON DELETE CASCADE,
                    tag TEXT NOT NULL, PRIMARY KEY(image_id, tag));
                CREATE INDEX IF NOT EXISTS tag_name ON tags(tag, image_id);
                CREATE TABLE IF NOT EXISTS moves (id TEXT PRIMARY KEY, image_id TEXT NOT NULL,
                    source TEXT NOT NULL, destination TEXT NOT NULL, status TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS folder_moves (id TEXT PRIMARY KEY, source TEXT NOT NULL,
                    destination TEXT NOT NULL, identity TEXT NOT NULL, status TEXT NOT NULL);
            ''')
            columns = {row[1] for row in db.execute('PRAGMA table_info(images)')}
            for name, declaration in [('kind', "TEXT NOT NULL DEFAULT 'image'"), ('duration', 'REAL NOT NULL DEFAULT 0'), ('video_codec', "TEXT NOT NULL DEFAULT ''")]:
                if name not in columns:
                    db.execute(f'ALTER TABLE images ADD COLUMN {name} {declaration}')


    @contextlib.contextmanager
    def db(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def setting(self, key, default=None):
        with self.db() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    @property
    def root(self) -> Path:
        raw = self._root_value or self.setting('root')
        if not raw:
            raise LibraryError('画像フォルダが未設定です。')
        self._root_value = raw
        root = Path(raw)
        # Recheck every access: an owner may have replaced a folder with a junction.
        for part in [root, *root.parents]:
            if part.exists() and linked(part):
                raise LibraryError('リンク・ジャンクションを画像フォルダには使用できません。')
        if not root.is_dir():
            raise LibraryError('画像フォルダに接続できません。ドライブの接続を確認してください。')
        return root

    def path(self, relative: str, must_exist=True) -> Path:
        root = self.root
        if '\\' in relative or ':' in relative or '\x00' in relative:
            raise LibraryError('無効なパスです。')
        parts = relative.split('/') if relative else []
        if any(p in {'', '.', '..'} or p.startswith('.') or p.endswith((' ', '.')) for p in parts):
            raise LibraryError('無効なパスです。')
        target = root.joinpath(*parts)
        current = root
        for p in parts:
            current = current / p
            if current.exists() or current.is_symlink():
                if linked(current):
                    raise LibraryError('リンク・ジャンクションにはアクセスできません。')
        if not target.resolve().is_relative_to(root.resolve()):
            raise LibraryError('画像フォルダの外にはアクセスできません。')
        if must_exist and not target.exists():
            raise LibraryError('ファイルまたはフォルダが見つかりません。再読み込みしてください。')
        return target

    def recover_moves(self):
        with self.lock, self.db() as db:
            for job in db.execute("SELECT * FROM folder_moves WHERE status='pending'").fetchall():
                src, dst = self.path(job['source'], False), self.path(job['destination'], False)
                info = dst.stat() if dst.is_dir() else None
                if info and not src.exists() and job['identity'] == f'{info.st_dev}:{info.st_ino}':
                    self._finish_folder_move(db, job['source'], job['destination'])
                    status = 'done'
                else:
                    status = 'aborted'
                db.execute('UPDATE folder_moves SET status=? WHERE id=?', (status, job['id']))
            for job in db.execute("SELECT * FROM moves WHERE status='pending'").fetchall():
                src = self.path(job['source'], False)
                dst = self.path(job['destination'], False)
                expected = db.execute('SELECT identity,size,mtime_ns FROM images WHERE id=?', (job['image_id'],)).fetchone()
                info = dst.stat() if dst.is_file() else None
                matches = bool(info and expected and expected['identity'] == f'{info.st_dev}:{info.st_ino}'
                               and expected['size'] == info.st_size and expected['mtime_ns'] == info.st_mtime_ns)
                if matches and src.exists() and os.path.samefile(src, dst):
                    src.unlink()
                if matches and not src.exists():
                    db.execute('UPDATE images SET path=?,folder=?,name=? WHERE id=?',
                               (job['destination'], job['destination'].rsplit('/', 1)[0] if '/' in job['destination'] else '', dst.name, job['image_id']))
                    status = 'done'
                else:
                    status = 'aborted'
                db.execute('UPDATE moves SET status=? WHERE id=?', (status, job['id']))

    def _finish_folder_move(self, db, source, destination):
        for row in db.execute('SELECT id,path FROM images').fetchall():
            if not row['path'].startswith(source + '/'):
                continue
            path = destination + row['path'][len(source):]
            old = db.execute('SELECT id FROM images WHERE path=? AND id<>?', (path, row['id'])).fetchone()
            if old:
                db.execute('UPDATE images SET path=?,available=0 WHERE id=?', (f'__luma_missing__/{old["id"]}/image', old['id']))
            db.execute('UPDATE images SET path=?,folder=? WHERE id=?', (path, path.rsplit('/', 1)[0], row['id']))
        self.folder_cache = None
        self.scan_state['revision'] += 1

    def move_folder(self, source, parent):
        if not source:
            raise LibraryError('ライブラリ直下は移動できません。')
        with self.lock:
            self.recover_moves()
            src, destination_parent = self.path(source), self.path(parent)
            if not src.is_dir() or not destination_parent.is_dir():
                raise LibraryError('フォルダを指定してください。')
            if destination_parent.resolve().is_relative_to(src.resolve()):
                raise LibraryError('自分自身や配下のフォルダには移動できません。')
            destination = f'{parent}/{src.name}' if parent else src.name
            if source == destination:
                return {'path': source}
            dst = self.path(destination, False)
            if dst.exists():
                raise LibraryError('同名のフォルダが存在します。結合・上書きは行いません。')
            # Native Windows rename is atomic and fails if the destination exists.
            if os.name != 'nt':
                raise LibraryError('フォルダ自体の移動はWindows版で利用できます。')
            info, job = src.stat(), uuid.uuid4().hex
            with self.db() as db:
                db.execute('INSERT INTO folder_moves VALUES(?,?,?,?,?)', (job, source, destination, f'{info.st_dev}:{info.st_ino}', 'pending'))
            os.rename(src, dst)
            with self.db() as db:
                self._finish_folder_move(db, source, destination)
                db.execute("UPDATE folder_moves SET status='done' WHERE id=?", (job,))
            return {'path': destination}

    def scan(self):
        if not self.scan_lock.acquire(blocking=False):
            return
        self.scan_state.update(running=True, processed=0, errors=0, message='')
        seen = set()
        traversal_ok = True
        try:
            root = self.root
            with self.lock:
                self.recover_moves()
                with self.db() as db:
                    unchanged = {r['path']: dict(r) for r in db.execute('SELECT path,size,mtime_ns,available FROM images')}

            def on_error(error):
                nonlocal traversal_ok
                traversal_ok = False
                self.scan_state['errors'] += 1

            for directory, dirs, files in os.walk(root, followlinks=False, onerror=on_error):
                dirs[:] = [d for d in dirs if not d.startswith('.') and not linked(Path(directory) / d)]
                for name in files:
                    p = Path(directory) / name
                    if name.startswith('.') or p.suffix.lower() not in EXTENSIONS:
                        continue
                    relative = p.relative_to(root).as_posix()
                    try:
                        with self.lock:
                            p = self.path(relative)
                            info = p.stat()
                            # Files still being copied are picked up on the next scan.
                            if time.time_ns() - info.st_mtime_ns < 1_000_000_000:
                                seen.add(relative)
                                continue
                            identity = f'{info.st_dev}:{info.st_ino}' if info.st_ino else None
                            previous = unchanged.get(relative)
                            if previous and previous['available'] and previous['size'] == info.st_size and previous['mtime_ns'] == info.st_mtime_ns:
                                seen.add(relative)
                                self.scan_state['processed'] += 1
                                continue
                            with self.db() as db:
                                row = db.execute('SELECT * FROM images WHERE path=?', (relative,)).fetchone()
                                if row and row['size'] == info.st_size and row['mtime_ns'] == info.st_mtime_ns and row['available']:
                                    seen.add(relative)
                                    self.scan_state['processed'] += 1
                                    continue
                                # Preserve tags across external renames when the old path is gone.
                                if not row and identity:
                                    candidates = db.execute('SELECT * FROM images WHERE identity=?', (identity,)).fetchall()
                                    candidates = [r for r in candidates if not self.path(r['path'], False).exists() and r['size'] == info.st_size and r['mtime_ns'] == info.st_mtime_ns]
                                    if len(candidates) == 1:
                                        row = candidates[0]
                                meta = self.metadata(p)
                                width, height, fmt, animated = (meta[k] for k in ('width', 'height', 'format', 'animated'))
                                # A file that changed while reading must be retried.
                                end = p.stat()
                                if (end.st_size, end.st_mtime_ns) != (info.st_size, info.st_mtime_ns):
                                    seen.add(relative)
                                    continue
                                values = (relative, '' if p.parent == root else p.parent.relative_to(root).as_posix(), name, info.st_size, info.st_mtime_ns, identity, width, height, fmt, animated)
                                if row:
                                    db.execute('UPDATE images SET path=?,folder=?,name=?,size=?,mtime_ns=?,identity=?,width=?,height=?,format=?,animated=?,available=1 WHERE id=?', (*values, row['id']))
                                else:
                                    db.execute('INSERT INTO images(id,path,folder,name,size,mtime_ns,identity,width,height,format,animated,added) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (uuid.uuid4().hex, *values, time.time()))
                                db.execute('UPDATE images SET kind=?,duration=?,video_codec=? WHERE path=?', (meta['kind'], meta['duration'], meta['video_codec'], relative))
                            self.scan_state['revision'] += 1
                            seen.add(relative)
                            self.scan_state['processed'] += 1
                    except (OSError, ValueError, LibraryError, Image.DecompressionBombError, Image.DecompressionBombWarning):
                        self.scan_state['errors'] += 1
                        # Do not serve stale derivatives after a file becomes invalid.
                        with self.db() as db:
                            changed = db.execute('UPDATE images SET available=0 WHERE path=? AND available=1', (relative,)).rowcount
                            self.scan_state['revision'] += changed
            if traversal_ok:
                with self.lock, self.db() as db:
                    for row in db.execute('SELECT id,path FROM images WHERE available=1').fetchall():
                        if row['path'] not in seen and not self.path(row['path'], False).is_file():
                            db.execute('UPDATE images SET available=0 WHERE id=?', (row['id'],))
                            self.scan_state['revision'] += 1
            self.scan_state['last_scan'] = time.time()
            self.folder_cache = None
            self.scan_state['message'] = '一部の画像を読み込めませんでした。' if self.scan_state['errors'] else ''
            self.trim_cache()
        except (OSError, LibraryError):
            self.scan_state['message'] = '画像フォルダを読み込めません。ドライブとフォルダを確認してください。'
        except Exception:
            log.exception('Library scan failed')
            self.scan_state['message'] = 'スキャンに失敗しました。サーバーログを確認してください。'
        finally:
            self.scan_state['running'] = False
            self.scan_lock.release()

    def trim_cache(self):
        files = []
        for p in self.cache.glob('*.webp'):
            try:
                st = p.stat()
                files.append((st.st_mtime, st.st_size, p))
            except OSError:
                pass
        total = sum(f[1] for f in files)
        for _, size, p in sorted(files):
            if total <= 2 * 1024 ** 3:
                break
            try:
                p.unlink(missing_ok=True)
                total -= size
            except OSError:
                pass

    def image(self, image_id):
        with self.db() as db:
            row = db.execute('SELECT * FROM images WHERE id=? AND available=1', (image_id,)).fetchone()
        if not row:
            raise LibraryError('画像が見つかりません。')
        return dict(row)

    def derivative(self, row, kind):
        with self.lock:
            source = self.path(row['path'])
            info = source.stat()
            # Cache keys use current filesystem state, not just the scan database.
            key = hashlib.sha256(f"v2:{row['id']}:{info.st_mtime_ns}:{info.st_size}:{kind}".encode()).hexdigest()
        target = self.cache / f'{key}.webp'
        with self.render_locks[int(key[:4], 16) % len(self.render_locks)]:
            if target.exists():
                return target
            with self.render_slots:
                with self.lock:
                    source = self.path(row['path'])
                    opened = video.inspect(source, thumbnail=True) if row.get('kind') == 'video' else Image.open(source, formats=list(FORMATS))
                with opened as im:
                    limit = 480 if kind == 'thumb' else 2560
                    im.draft('RGB', (limit, limit))
                    im = ImageOps.exif_transpose(im)
                    im.thumbnail((limit, limit), Image.Resampling.LANCZOS)
                    if im.mode not in {'RGB', 'RGBA'}:
                        im = im.convert('RGBA' if 'transparency' in im.info else 'RGB')
                    temp = target.with_suffix('.tmp')
                    try:
                        im.save(temp, format='WEBP', quality=82 if kind == 'thumb' else 91, method=4)
                        temp.replace(target)
                    finally:
                        temp.unlink(missing_ok=True)
        return target

    def folders(self):
        if self.folder_cache is not None:
            return [dict(f) for f in self.folder_cache]
        result = [{'path': '', 'name': 'ライブラリ直下'}]
        root = self.root
        for directory, dirs, _ in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith('.') and not linked(Path(directory) / d))
            for name in dirs:
                path = (Path(directory) / name).relative_to(root).as_posix()
                result.append({'path': path, 'name': name})
        self.folder_cache = result
        return [dict(f) for f in result]

    def move(self, image_id, folder=None, name=None):
        with self.lock:
            row = self.image(image_id)
            src = self.path(row['path'])
            info = src.stat()
            if info.st_size != row['size'] or info.st_mtime_ns != row['mtime_ns'] or (row['identity'] and row['identity'] != f'{info.st_dev}:{info.st_ino}'):
                raise LibraryError('ファイルが外部で変更されました。再スキャンしてから操作してください。')
            dest_folder = row['folder'] if folder is None else folder
            parent = self.path(dest_folder)
            if not parent.is_dir():
                raise LibraryError('移動先はフォルダで指定してください。')
            dest_name = valid_name(name if name is not None else row['name'])
            if Path(dest_name).suffix.lower() != src.suffix.lower():
                raise LibraryError('画像の拡張子は変更できません。')
            dest_relative = f'{dest_folder}/{dest_name}' if dest_folder else dest_name
            if dest_relative == row['path']:
                return row
            dst = self.path(dest_relative, False)
            if dst.exists():
                raise LibraryError('同じ名前のファイルが存在します。上書きは行いません。')
            job_id = uuid.uuid4().hex
            with self.db() as db:
                old = db.execute('SELECT id FROM images WHERE path=? AND id<>?', (dest_relative, image_id)).fetchone()
                if old:
                    # Keep an unavailable image's tags, but release its old filename.
                    db.execute('UPDATE images SET path=?,available=0 WHERE id=?', (f'__luma_missing__/{old["id"]}/{dest_name}', old['id']))
                db.execute('INSERT INTO moves VALUES(?,?,?,?,?)', (job_id, image_id, row['path'], dest_relative, 'pending'))
            try:
                if os.name == 'nt':
                    os.rename(src, dst)  # Windows rename fails if destination exists.
                else:
                    os.link(src, dst)  # Atomic no-clobber publication on other platforms.
                    src.unlink()
            except OSError as error:
                if not (src.exists() and dst.exists() and os.path.samefile(src, dst)):
                    with self.db() as db:
                        db.execute("UPDATE moves SET status='aborted' WHERE id=?", (job_id,))
                raise LibraryError('ファイルを移動できません。使用中・権限・同名ファイルを確認してください。') from error
            with self.db() as db:
                db.execute('UPDATE images SET path=?,folder=?,name=? WHERE id=?', (dest_relative, dest_folder, dest_name, image_id))
                db.execute("UPDATE moves SET status='done' WHERE id=?", (job_id,))
            return self.image(image_id)

    def import_image(self, temporary, folder, name):
        """Validate before atomic publication; never overwrite an existing file."""
        try:
            meta = self.metadata(temporary, name=name, validate=True)
            width, height, fmt, animated = (meta[k] for k in ('width', 'height', 'format', 'animated'))
        except (OSError, ValueError, Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
            raise LibraryError('有効な画像・動画として読み込めません。形式・破損・画素数を確認してください。') from exc
        with self.lock:
            valid_name(name)
            parent = self.path(folder)
            if not parent.is_dir():
                raise LibraryError('アップロード先はフォルダで指定してください。')
            relative = f'{folder}/{name}' if folder else name
            target = self.path(relative, False)
            if target.exists():
                raise LibraryError('同じ名前のファイルが存在します。上書きは行いません。')
            if os.name == 'nt':
                os.rename(temporary, target)
            else:
                os.link(temporary, target)
                temporary.unlink()
            info = target.stat()
            image_id = uuid.uuid4().hex
            with self.db() as db:
                old = db.execute('SELECT id FROM images WHERE path=?', (relative,)).fetchone()
                if old:
                    db.execute('UPDATE images SET path=?,available=0 WHERE id=?', (f'__luma_missing__/{old["id"]}/{name}', old['id']))
                db.execute('INSERT INTO images(id,path,folder,name,size,mtime_ns,identity,width,height,format,animated,added) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                           (image_id, relative, folder, name, info.st_size, info.st_mtime_ns, f'{info.st_dev}:{info.st_ino}', width, height, fmt, animated, time.time()))
                db.execute('UPDATE images SET kind=?,duration=?,video_codec=? WHERE id=?', (meta['kind'], meta['duration'], meta['video_codec'], image_id))
            self.scan_state['revision'] += 1
            return {'id': image_id, 'name': name, 'folder': folder}

    @staticmethod
    def metadata(path, name=None, validate=False):
        if Path(name or path).suffix.lower() in video.EXTENSIONS:
            meta = video.inspect(path)
            if validate:
                with video.inspect(path, thumbnail=True):
                    pass
            return meta
        if validate:
            with Image.open(path, formats=list(FORMATS)) as im:
                im.verify()
        with Image.open(path, formats=list(FORMATS)) as im:
            if validate:
                im.load()
            width, height = im.size
            if im.getexif().get(274) in {5, 6, 7, 8}:
                width, height = height, width
            return {'width': width, 'height': height, 'format': im.format, 'animated': int(getattr(im, 'is_animated', False)),
                    'kind': 'image', 'duration': 0, 'video_codec': ''}
