import asyncio
import ipaddress
import json
import os
import secrets
import threading
import time
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .library import Library, LibraryError, valid_name, EXTENSIONS
from . import video
from .security import LoginLimiter, address, allowed, networks, password_hash, password_matches, token_hash

PROJECT = Path(__file__).resolve().parent.parent
STATIC = PROJECT / 'static'
COOKIE = 'luma_session'


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class Setup(Login):
    password: str = Field(min_length=12, max_length=256)
    filter_enabled: bool = False
    whitelist: list[str] = Field(default_factory=list, max_length=100)


class AccessSettings(BaseModel):
    filter_enabled: bool
    whitelist: list[str] = Field(max_length=100)


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=256)
    password: str = Field(min_length=12, max_length=256)


class EditImage(BaseModel):
    name: str | None = Field(default=None, max_length=180)
    folder: str | None = Field(default=None, max_length=1000)
    tags: list[str] | None = Field(default=None, max_length=30)
    favorite: bool | None = None


class Batch(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=200)
    folder: str | None = Field(default=None, max_length=1000)
    add_tags: list[str] = Field(default_factory=list, max_length=30)
    remove_tags: list[str] = Field(default_factory=list, max_length=30)
    favorite: bool | None = None


class Folder(BaseModel):
    parent: str = Field(default='', max_length=1000)
    name: str = Field(min_length=1, max_length=180)


class FolderMove(BaseModel):
    source: str = Field(min_length=1, max_length=1000)
    parent: str = Field(default='', max_length=1000)


def clean_tags(tags):
    result = sorted(set(t.strip() for t in tags if t.strip()))
    if any(len(t) > 40 or any(ord(c) < 32 for c in t) for t in result):
        raise LibraryError('タグは1つ40文字以内で入力してください。')
    return result


def create_app(data_dir=None, public_dir=None, start_scanner=True):
    library = Library(Path(data_dir or os.environ.get('LUMA_DATA_DIR', PROJECT / 'data')))
    public = Path(public_dir or os.environ.get('LUMA_PUBLIC_DIR', PROJECT / 'public')).absolute()
    public.mkdir(parents=True, exist_ok=True)
    limiter = LoginLimiter()
    hash_lock = threading.BoundedSemaphore(2)
    upload_slots = threading.BoundedSemaphore(2)
    dummy_hash = password_hash(secrets.token_urlsafe(32))

    @asynccontextmanager
    async def lifespan(app):
        stop = threading.Event()

        def scanner():
            while not stop.is_set():
                if library.setting('configured', False):
                    library.scan()
                stop.wait(15)

        thread = threading.Thread(target=scanner, name='luma-scanner', daemon=True)
        if start_scanner:
            thread.start()
        yield
        stop.set()
        if start_scanner:
            await run_in_threadpool(thread.join, 5)

    app = FastAPI(title='Luma LAN Gallery', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.library = library
    app.state.limiter = limiter

    @app.exception_handler(LibraryError)
    async def library_error(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=400)

    @app.exception_handler(OSError)
    async def filesystem_error(request, exc):
        return JSONResponse({'detail': 'ファイルにアクセスできません。接続・権限・使用中の状態を確認してください。'}, status_code=409)

    @app.middleware('http')
    async def guard(request: Request, call_next):
        peer = request.client.host if request.client else ''
        host = request.headers.get('host', '')
        try:
            hostname = urlsplit('//' + host).hostname
            if hostname != 'localhost':
                ipaddress.ip_address(hostname or '')
            address(peer)
        except ValueError:
            return JSONResponse({'detail': 'IPアドレスまたはlocalhostでアクセスしてください。'}, status_code=403)
        if library.setting('filter_enabled', False) and not allowed(peer, library.setting('whitelist', [])):
            return JSONResponse({'detail': 'このIPアドレスは許可されていません。'}, status_code=403)
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            # No CORS. Requiring custom header prevents cross-origin HTML form writes.
            if request.headers.get('x-luma-request') != '1':
                return JSONResponse({'detail': 'リクエストの検証に失敗しました。'}, status_code=403)
            origin = request.headers.get('origin')
            if origin and origin != f'{request.url.scheme}://{host}':
                return JSONResponse({'detail': '異なるサイトからの操作は許可されていません。'}, status_code=403)
            if request.headers.get('sec-fetch-site') == 'cross-site':
                return JSONResponse({'detail': '異なるサイトからの操作は許可されていません。'}, status_code=403)
            is_upload = request.url.path == '/api/upload' and request.method == 'POST'
            if is_upload:
                try:
                    user(request)
                except HTTPException as exc:
                    return JSONResponse({'detail': exc.detail}, status_code=exc.status_code)
            body_limit = (1024 ** 3 if Path(request.query_params.get('name', '')).suffix.lower() in video.EXTENSIONS else 64 * 1024 * 1024) if is_upload else 65536
            try:
                if int(request.headers.get('content-length', '0')) > body_limit:
                    return JSONResponse({'detail': 'リクエストが大きすぎます。'}, status_code=413)
            except ValueError:
                return JSONResponse({'detail': '不正なリクエストです。'}, status_code=400)
            # Also bound chunked bodies; checking Content-Length alone is insufficient.
            if not is_upload:
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > body_limit:
                        return JSONResponse({'detail': 'リクエストが大きすぎます。'}, status_code=413)
                request._body = bytes(body)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
        # Do not let shared/browser disk caches bypass authentication on image requests.
        response.headers['Cache-Control'] = 'no-store'
        return response

    def user(request: Request):
        token = request.cookies.get(COOKIE, '')
        with library.db() as db:
            row = db.execute('SELECT users.id,users.username FROM sessions JOIN users ON users.id=sessions.user_id WHERE token=? AND expires>?', (token_hash(token), time.time())).fetchone()
        if not row:
            raise HTTPException(401, 'ログインしてください。')
        return dict(row)

    def local_only(request):
        if not address(request.client.host).is_loopback:
            raise HTTPException(403, '初回設定はこのPCのlocalhostから行ってください。')

    def issue_session(user_id, request):
        token = secrets.token_urlsafe(32)
        with library.db() as db:
            db.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))
            db.execute('INSERT INTO sessions VALUES(?,?,?)', (token_hash(token), user_id, time.time() + 12 * 3600))
        response = JSONResponse({'ok': True})
        response.set_cookie(COOKIE, token, max_age=12 * 3600, httponly=True, samesite='strict', secure=request.url.scheme == 'https')
        return response

    @app.get('/api/status')
    def status(request: Request):
        configured = library.setting('configured', False)
        result = {'configured': configured, 'local': address(request.client.host).is_loopback}
        if not configured and result['local']:
            result['public_dir'] = str(public)
        return result

    @app.post('/api/setup')
    def setup(body: Setup, request: Request):
        local_only(request)
        if not limiter.take(request.client.host):
            raise HTTPException(429, '試行回数が多すぎます。5分ほど待ってください。')
        try:
            whitelist = networks(body.whitelist)
        except ValueError:
            raise HTTPException(422, '許可IPはIPアドレスまたはCIDR形式で入力してください。')
        if body.filter_enabled and not whitelist:
            raise HTTPException(422, 'IPフィルタを有効にする場合は許可IPを入力してください。')
        username = body.username.strip()
        if not username:
            raise HTTPException(422, 'IDを入力してください。')
        with library.lock, library.db() as db:
            if library.setting('configured', False):
                raise HTTPException(409, '初回設定は完了しています。')
            with hash_lock:
                encoded = password_hash(body.password)
            db.execute('INSERT INTO users(username,password) VALUES(?,?)', (username, encoded))
            for key, value in {'root': str(public), 'filter_enabled': body.filter_enabled, 'whitelist': whitelist, 'configured': True}.items():
                db.execute('INSERT INTO settings VALUES(?,?)', (key, json.dumps(value)))
        # No auto-login: setup and the first real authenticated session stay explicit.
        return {'ok': True}

    @app.post('/api/login')
    def login(body: Login, request: Request):
        if not limiter.take(request.client.host):
            raise HTTPException(429, 'ログイン試行が多すぎます。5分ほど待ってください。')
        with library.db() as db:
            row = db.execute('SELECT * FROM users WHERE username=?', (body.username.strip(),)).fetchone()
        with hash_lock:
            matched = password_matches(body.password, row['password'] if row else dummy_hash)
        if not row or not matched:
            raise HTTPException(401, 'IDまたはパスワードが違います。')
        return issue_session(row['id'], request)

    @app.post('/api/logout')
    def logout(request: Request, current=Depends(user)):
        with library.db() as db:
            db.execute('DELETE FROM sessions WHERE token=?', (token_hash(request.cookies.get(COOKIE, '')),))
        response = JSONResponse({'ok': True})
        response.delete_cookie(COOKIE)
        return response

    @app.get('/api/me')
    def me(request: Request, current=Depends(user)):
        return {**current, 'client_ip': request.client.host}

    @app.get('/api/settings')
    def settings(current=Depends(user)):
        return {'public_dir': str(public), 'filter_enabled': library.setting('filter_enabled', False),
                'whitelist': library.setting('whitelist', []), 'scan': dict(library.scan_state)}

    @app.put('/api/settings/access')
    def update_access(body: AccessSettings, request: Request, current=Depends(user)):
        try:
            values = networks(body.whitelist)
        except ValueError:
            raise HTTPException(422, '許可IPはIPアドレスまたはCIDR形式で入力してください。')
        if body.filter_enabled and (not values or not allowed(request.client.host, values)):
            raise HTTPException(422, '現在の端末を含む許可IPを入力してください。localhostは常に許可されます。')
        with library.db() as db:
            for key, value in {'filter_enabled': body.filter_enabled, 'whitelist': values}.items():
                db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key, json.dumps(value)))
        return {'ok': True}

    @app.put('/api/settings/password')
    def change_password(body: PasswordChange, request: Request, current=Depends(user)):
        if not limiter.take(request.client.host):
            raise HTTPException(429, '試行回数が多すぎます。5分ほど待ってください。')
        with library.lock, library.db() as db, hash_lock:
            row = db.execute('SELECT password FROM users WHERE id=?', (current['id'],)).fetchone()
            if not password_matches(body.current_password, row['password']):
                raise HTTPException(400, '現在のパスワードが違います。')
            db.execute('UPDATE users SET password=? WHERE id=?', (password_hash(body.password), current['id']))
            db.execute('DELETE FROM sessions WHERE user_id=?', (current['id'],))
        return {'ok': True}

    def pack_rows(db, rows):
        result = [dict(r) for r in rows]
        tag_map = {r['id']: [] for r in result}
        if result:
            placeholders = ','.join('?' for _ in result)
            for t in db.execute(f'SELECT image_id,tag FROM tags WHERE image_id IN ({placeholders}) ORDER BY tag', list(tag_map)):
                tag_map[t['image_id']].append(t['tag'])
        for r in result:
            r['tags'] = tag_map[r['id']]
            r['version'] = str(r['mtime_ns'])
            r.pop('identity', None)
        return result

    @app.get('/api/images')
    def images(q: str = Query('', max_length=200), folder: str | None = Query(None, max_length=1000),
               recursive: bool = False, tag: list[str] = Query(default=[]), favorite: bool = False,
               sort: str = 'newest', offset: int = Query(0, ge=0), limit: int = Query(80, ge=1, le=200), current=Depends(user)):
        if len(tag) > 30:
            raise HTTPException(422, 'タグの指定が多すぎます。')
        where, args = ['available=1'], []
        # The root is a folder too: default listing never flattens descendants.
        if folder is None and not recursive:
            folder = ''
        def escaped(value):
            return value.replace('!', '!!').replace('%', '!%').replace('_', '!_')
        if q.strip():
            where.append("(name LIKE ? ESCAPE '!' COLLATE NOCASE OR EXISTS(SELECT 1 FROM tags t WHERE t.image_id=images.id AND t.tag LIKE ? ESCAPE '!'))")
            args.extend(['%' + escaped(q.strip()) + '%'] * 2)
        if folder is not None:
            library.path(folder)
            if recursive and folder:
                where.append("(folder=? OR folder LIKE ? ESCAPE '!')")
                args.extend([folder, escaped(folder) + '/%'])
            elif not recursive:
                where.append('folder=?')
                args.append(folder)
        if favorite:
            where.append('favorite=1')
        for t in tag:
            where.append('EXISTS(SELECT 1 FROM tags t WHERE t.image_id=images.id AND t.tag=?)')
            args.append(t)
        orders = {'newest': 'mtime_ns DESC,id', 'oldest': 'mtime_ns ASC,id', 'name': 'name COLLATE NOCASE,id',
                  'name_desc': 'name COLLATE NOCASE DESC,id', 'largest': 'size DESC,id', 'added': 'added DESC,id'}
        if sort not in orders:
            raise HTTPException(422, '無効な並び順です。')
        clause = ' AND '.join(where)
        with library.db() as db:
            total = db.execute(f'SELECT COUNT(*) FROM images WHERE {clause}', args).fetchone()[0]
            rows = db.execute(f'SELECT * FROM images WHERE {clause} ORDER BY {orders[sort]} LIMIT ? OFFSET ?', [*args, limit, offset]).fetchall()
            return {'items': pack_rows(db, rows), 'total': total, 'offset': offset}

    @app.get('/api/summary')
    def summary(current=Depends(user)):
        with library.db() as db:
            row = db.execute('SELECT COUNT(*) count, COALESCE(SUM(size),0) bytes, COALESCE(SUM(favorite),0) favorites FROM images WHERE available=1').fetchone()
            tags = [dict(r) for r in db.execute('SELECT tag,COUNT(*) count FROM tags JOIN images ON images.id=tags.image_id WHERE available=1 GROUP BY tag ORDER BY count DESC,tag')]
            counts = {r['folder']: r['n'] for r in db.execute('SELECT folder,COUNT(*) n FROM images WHERE available=1 GROUP BY folder')}
        try:
            folders = library.folders()
            totals = {'': sum(counts.values())}
            for path, n in counts.items():
                if not path:
                    continue
                parts = path.split('/')
                for i in range(1, len(parts) + 1):
                    parent = '/'.join(parts[:i])
                    totals[parent] = totals.get(parent, 0) + n
            for f in folders:
                f['count'] = totals.get(f['path'], 0)
        except (OSError, LibraryError):
            folders = []
        return {**dict(row), 'tags': tags, 'folders': folders, 'scan': dict(library.scan_state)}

    @app.post('/api/scan')
    def scan(current=Depends(user)):
        if not library.scan_state['running']:
            threading.Thread(target=library.scan, daemon=True).start()
        return {'ok': True}

    @app.post('/api/folders')
    def create_folder(body: Folder, current=Depends(user)):
        with library.lock:
            parent = library.path(body.parent)
            name = valid_name(body.name)
            relative = f'{body.parent}/{name}' if body.parent else name
            library.path(relative, False).mkdir(exist_ok=False)
            library.folder_cache = None
        return {'path': relative}

    @app.post('/api/upload')
    async def upload(request: Request, name: str = Query(..., max_length=180),
                     folder: str = Query('', max_length=1000), current=Depends(user)):
        valid_name(name)
        if Path(name).suffix.lower() not in EXTENSIONS:
            raise HTTPException(422, '対応している画像・動画ファイルを選択してください。')
        if not library.path(folder).is_dir():
            raise LibraryError('アップロード先のフォルダが見つかりません。')
        if not upload_slots.acquire(blocking=False):
            raise HTTPException(429, 'アップロードが混み合っています。少し待って再試行してください。')
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=library.data_dir, prefix='upload-', suffix='.tmp', delete=False) as output:
                temporary = Path(output.name)
                size = 0
                async with asyncio.timeout(600):
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > (1024 ** 3 if Path(name).suffix.lower() in video.EXTENSIONS else 64 * 1024 * 1024):
                            raise HTTPException(413, '画像は64MiB、動画は1GiBまでです。')
                        await run_in_threadpool(output.write, chunk)
                await run_in_threadpool(output.flush)
                await run_in_threadpool(os.fsync, output.fileno())
            return await run_in_threadpool(library.import_image, temporary, folder, name)
        except TimeoutError:
            raise HTTPException(408, 'アップロードがタイムアウトしました。再試行してください。')
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            upload_slots.release()

    @app.post('/api/folders/move')
    def move_folder(body: FolderMove, current=Depends(user)):
        return library.move_folder(body.source, body.parent)

    @app.get('/api/images/{image_id}')
    def image_detail(image_id: str, current=Depends(user)):
        row = library.image(image_id)
        with library.db() as db:
            return pack_rows(db, [row])[0]

    @app.patch('/api/images/{image_id}')
    def edit_image(image_id: str, body: EditImage, current=Depends(user)):
        tags = clean_tags(body.tags) if body.tags is not None else None
        with library.lock:
            library.image(image_id)
            if body.name is not None or body.folder is not None:
                library.move(image_id, body.folder, body.name)
            with library.db() as db:
                if tags is not None:
                    db.execute('DELETE FROM tags WHERE image_id=?', (image_id,))
                    db.executemany('INSERT INTO tags VALUES(?,?)', [(image_id, t) for t in tags])
                if body.favorite is not None:
                    db.execute('UPDATE images SET favorite=? WHERE id=?', (int(body.favorite), image_id))
        return image_detail(image_id, current)

    @app.post('/api/batch')
    def batch(body: Batch, current=Depends(user)):
        add, remove = clean_tags(body.add_tags), clean_tags(body.remove_tags)
        results = []
        for image_id in dict.fromkeys(body.ids):
            try:
                with library.lock:
                    library.image(image_id)
                    with library.db() as db:
                        existing = {r[0] for r in db.execute('SELECT tag FROM tags WHERE image_id=?', (image_id,))}
                    tags = sorted((existing - set(remove)) | set(add))
                    if len(tags) > 30:
                        raise LibraryError('タグは1ファイルにつき30個までです。')
                    edit_image(image_id, EditImage(folder=body.folder, tags=tags, favorite=body.favorite), current)
                results.append({'id': image_id, 'ok': True})
            except (LibraryError, OSError) as exc:
                results.append({'id': image_id, 'ok': False, 'error': str(exc) if isinstance(exc, LibraryError) else 'ファイルを移動できません。'})
        return {'results': results}

    @app.get('/api/media/{image_id}/{kind}')
    def media(image_id: str, kind: str, current=Depends(user)):
        row = library.image(image_id)
        if kind in {'thumb', 'preview'}:
            try:
                path = library.derivative(row, kind)
            except (ValueError, *ImageErrorTypes) as exc:
                raise HTTPException(422, 'プレビューを生成できません。') from exc
            return FileResponse(path, media_type='image/webp')
        if kind in {'original', 'download'}:
            with library.lock:
                path = library.path(row['path'])
                if row.get('kind') == 'video':
                    info = path.stat()
                    if info.st_size != row['size'] or info.st_mtime_ns != row['mtime_ns']:
                        raise HTTPException(409, '動画が変更されました。再スキャンしてください。')
                    mime = 'video/webm' if row['format'] == 'WEBM' else 'video/mp4'
                else:
                    from PIL import Image
                    from .library import FORMATS
                    with Image.open(path, formats=list(FORMATS)) as im:
                        fmt = im.format
                    mime = {'JPEG': 'image/jpeg', 'PNG': 'image/png', 'GIF': 'image/gif', 'WEBP': 'image/webp', 'AVIF': 'image/avif', 'BMP': 'image/bmp', 'TIFF': 'image/tiff'}[fmt]
            return FileResponse(path, media_type=mime, filename=row['name'], content_disposition_type='attachment' if kind == 'download' else 'inline')
        raise HTTPException(404, '見つかりません。')

    @app.get('/')
    def index():
        return FileResponse(STATIC / 'index.html', media_type='text/html')

    @app.get('/static/{name}')
    def static_file(name: str):
        if name not in {'app.js', 'style.css', 'manifest.webmanifest'}:
            raise HTTPException(404)
        return FileResponse(STATIC / name)

    return app


from PIL import Image as _Image
ImageErrorTypes = (_Image.DecompressionBombError, _Image.DecompressionBombWarning)
