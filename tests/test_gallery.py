import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from gallery.app import create_app
from gallery.library import LibraryError
from gallery.security import allowed, networks

HEADERS = {'X-Luma-Request': '1'}
ACCOUNT = {'username': 'test-owner', 'password': 'fixture-only-password-2026'}


def make_image(path, color='salmon', fmt=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (800, 600), color).save(path, format=fmt)
    past = time.time() - 5
    os.utime(path, (past, past))
    return path


@pytest.fixture
def env(tmp_path):
    app = create_app(tmp_path / 'data', tmp_path / 'public', start_scanner=False)
    with TestClient(app, base_url='http://localhost', client=('127.0.0.1', 41000)) as client:
        yield app, client, tmp_path


def configure(client):
    assert client.post('/api/setup', json=ACCOUNT, headers=HEADERS).status_code == 200
    assert client.post('/api/login', json=ACCOUNT, headers=HEADERS).status_code == 200


@pytest.fixture
def ready(env):
    app, client, root = env
    configure(client)
    make_image(root / 'public' / 'one.jpg')
    make_image(root / 'public' / 'nested' / 'two.png', 'navy')
    app.state.library.scan()
    return env


def get_items(client):
    return client.get('/api/images?recursive=true&sort=name').json()['items']


def test_setup_local_only_and_one_time(env):
    app, client, root = env
    with TestClient(app, base_url='http://192.0.2.10', client=('192.168.0.20', 20)) as remote:
        assert 'public_dir' not in remote.get('/api/status').json()
        assert remote.post('/api/setup', json=ACCOUNT, headers=HEADERS).status_code == 403
    configure(client)
    assert client.post('/api/setup', json=ACCOUNT, headers=HEADERS).status_code == 409
    assert client.get('/api/settings').json()['filter_enabled'] is False


def test_authentication_covers_metadata_and_every_media_kind(ready):
    app, client, root = ready
    image_id = get_items(client)[0]['id']
    client.cookies.clear()
    for path in ['/api/me', '/api/images', '/api/summary', '/api/settings', f'/api/images/{image_id}',
                 *[f'/api/media/{image_id}/{kind}' for kind in ['thumb', 'preview', 'original', 'download']]]:
        assert client.get(path).status_code == 401, path


def test_login_cookie_and_hash_storage(env):
    app, client, root = env
    configure(client)
    response = client.post('/api/login', headers=HEADERS, json=ACCOUNT)
    cookie = response.headers['set-cookie'].lower()
    assert 'httponly' in cookie and 'samesite=strict' in cookie
    with app.state.library.db() as db:
        row = db.execute('SELECT password FROM users').fetchone()
        assert row[0].startswith('scrypt$') and ACCOUNT['password'] not in row[0]
        session = db.execute('SELECT token FROM sessions LIMIT 1').fetchone()[0]
        assert len(session) == 64 and session != client.cookies.get('luma_session')
    assert client.get('/api/me').headers['cache-control'] == 'no-store'
    assert 'frame-ancestors' in client.get('/').headers['content-security-policy']
    assert client.post('/api/logout', headers=HEADERS).status_code == 200
    assert client.get('/api/me').status_code == 401


def test_secure_cookie_https(env):
    app, client, _ = env
    configure(client)
    with TestClient(app, base_url='https://localhost', client=('127.0.0.1', 20)) as secure:
        response = secure.post('/api/login', headers=HEADERS, json=ACCOUNT)
        assert 'secure' in response.headers['set-cookie'].lower()


def test_expired_session_is_rejected(env):
    app, client, _ = env
    configure(client)
    with app.state.library.db() as db:
        db.execute('UPDATE sessions SET expires=0')
    assert client.get('/api/me').status_code == 401


def test_default_filter_off_but_login_required(env):
    app, client, _ = env
    configure(client)
    with TestClient(app, base_url='http://192.0.2.10', client=('192.168.99.90', 1)) as remote:
        assert remote.get('/api/status').status_code == 200
        assert remote.get('/api/images').status_code == 401
        assert remote.post('/api/login', json=ACCOUNT, headers=HEADERS).status_code == 200


def test_whitelist_and_forwarded_spoofing(ready):
    app, client, _ = ready
    assert client.put('/api/settings/access', headers=HEADERS, json={'filter_enabled': True, 'whitelist': ['192.168.0.25']}).status_code == 200
    for peer, expected in [('192.168.0.25', 200), ('192.168.0.26', 403), ('::ffff:192.168.0.25', 200)]:
        with TestClient(app, base_url='http://192.0.2.10', client=(peer, 1)) as remote:
            assert remote.get('/api/status', headers={'X-Forwarded-For': '192.168.0.25'}).status_code == expected
    assert client.get('/api/status').status_code == 200
    assert allowed('192.168.0.99', networks(['192.168.0.0/24']))
    assert not allowed('192.168.1.99', networks(['192.168.0.0/24']))


def test_remote_cannot_lock_itself_out(env):
    app, client, _ = env
    configure(client)
    with TestClient(app, base_url='http://192.0.2.10', client=('192.168.0.20', 1)) as remote:
        remote.post('/api/login', json=ACCOUNT, headers=HEADERS)
        assert remote.put('/api/settings/access', headers=HEADERS, json={'filter_enabled': True, 'whitelist': ['192.168.0.21']}).status_code == 422


@pytest.mark.parametrize('values', [['bad'], ['192.168.999.1'], []])
def test_invalid_whitelist(env, values):
    _, client, _ = env
    configure(client)
    assert client.put('/api/settings/access', headers=HEADERS, json={'filter_enabled': True, 'whitelist': values}).status_code == 422


def test_csrf_and_dns_rebinding(env):
    _, client, _ = env
    assert client.post('/api/setup', json=ACCOUNT).status_code == 403
    assert client.post('/api/setup', json=ACCOUNT, headers={**HEADERS, 'Origin': 'https://evil.example'}).status_code == 403
    assert client.post('/api/setup', json=ACCOUNT, headers={**HEADERS, 'Sec-Fetch-Site': 'cross-site'}).status_code == 403
    assert client.get('/', headers={'Host': 'evil.example'}).status_code == 403
    assert client.post('/api/setup', json=ACCOUNT, headers={**HEADERS, 'Origin': 'http://localhost'}).status_code == 200


def test_request_size_and_login_rate_limit(env):
    _, client, _ = env
    assert client.post('/api/login', headers=HEADERS, content=b'x' * 65537).status_code == 413
    for _ in range(10):
        assert client.post('/api/login', headers=HEADERS, json=ACCOUNT).status_code == 401
    assert client.post('/api/login', headers=HEADERS, json=ACCOUNT).status_code == 429


def test_scanning_search_tag_filters_and_pagination(ready):
    app, client, root = ready
    items = get_items(client)
    assert len(items) == 2
    first = items[0]['id']
    assert client.patch(f'/api/images/{first}', headers=HEADERS, json={'tags': ['旅行', '星空'], 'favorite': True}).status_code == 200
    assert client.get('/api/images?recursive=true&q=旅行').json()['total'] == 1
    assert client.get('/api/images?recursive=true&tag=旅行&tag=星空').json()['total'] == 1
    assert client.get('/api/images?recursive=true&tag=旅行&tag=none').json()['total'] == 0
    assert client.get('/api/images?recursive=true&favorite=true').json()['total'] == 1
    assert client.get('/api/images?recursive=true&folder=&recursive=false').json()['total'] == 1
    assert client.get('/api/images?recursive=true&folder=nested').json()['total'] == 1
    page1 = client.get('/api/images?recursive=true&limit=1&sort=name').json()
    page2 = client.get('/api/images?recursive=true&limit=1&offset=1&sort=name').json()
    assert page1['items'][0]['id'] != page2['items'][0]['id'] and page1['total'] == 2
    assert client.get('/api/images?recursive=true&sort=not-sql').status_code == 422
    make_image(root / 'public' / '100%_real.jpg')
    app.state.library.scan()
    assert client.get('/api/images', params={'q': '%_', 'recursive': 'true'}).json()['total'] == 1


def test_derivatives_original_and_cache(ready):
    app, client, _ = ready
    item = get_items(client)[0]
    for kind in ['thumb', 'preview', 'original', 'download']:
        result = client.get(f'/api/media/{item["id"]}/{kind}')
        assert result.status_code == 200
        assert result.headers['cache-control'] == 'no-store'
        assert result.headers['x-content-type-options'] == 'nosniff'
    assert len(list(app.state.library.cache.glob('*.webp'))) == 2
    client.get(f'/api/media/{item["id"]}/thumb')
    assert len(list(app.state.library.cache.glob('*.webp'))) == 2
    thumb = next(app.state.library.cache.glob('*.webp'))
    with Image.open(thumb) as image:
        assert image.format == 'WEBP'


def test_original_is_revalidated_after_external_change(ready):
    app, client, root = ready
    item = get_items(client)[0]
    (root / 'public' / item['path']).write_text('<script>alert(1)</script>')
    os.utime(root / 'public' / item['path'], (time.time() - 5, time.time() - 5))
    assert client.get(f'/api/media/{item["id"]}/original').status_code != 200
    app.state.library.scan()
    assert client.get('/api/images?recursive=true').json()['total'] == 1


def test_edit_move_and_tag_survive_restart(ready):
    app, client, root = ready
    item = get_items(client)[0]
    assert client.post('/api/folders', headers=HEADERS, json={'parent': '', 'name': '整理済み'}).status_code == 200
    result = client.patch(f'/api/images/{item["id"]}', headers=HEADERS, json={'name': '改名.jpg', 'folder': '整理済み', 'tags': ['作品'], 'favorite': True})
    assert result.status_code == 200, result.text
    assert (root / 'public/整理済み/改名.jpg').is_file()
    assert not (root / 'public/one.jpg').exists()
    app.state.library.scan()
    app2 = create_app(root / 'data', root / 'public', False)
    with TestClient(app2, base_url='http://localhost', client=('127.0.0.1', 1)) as second:
        second.post('/api/login', headers=HEADERS, json=ACCOUNT)
        image = second.get(f'/api/images/{item["id"]}').json()
        assert image['tags'] == ['作品'] and image['favorite'] == 1


def test_external_rename_preserves_tags(ready):
    app, client, root = ready
    item = get_items(client)[0]
    client.patch(f'/api/images/{item["id"]}', headers=HEADERS, json={'tags': ['keep']})
    (root / 'public/one.jpg').rename(root / 'public/renamed.jpg')
    app.state.library.scan()
    detail = client.get(f'/api/images/{item["id"]}').json()
    assert detail['name'] == 'renamed.jpg' and detail['tags'] == ['keep']


def test_collision_and_batch_partial_success(ready):
    app, client, root = ready
    items = get_items(client)
    make_image(root / 'public/nested/one.jpg', 'green')
    original = (root / 'public/nested/one.jpg').read_bytes()
    result = client.post('/api/batch', headers=HEADERS, json={'ids': [i['id'] for i in items], 'folder': 'nested', 'add_tags': ['batch']})
    statuses = result.json()['results']
    assert statuses[0]['ok'] is False and statuses[1]['ok'] is True
    assert (root / 'public/one.jpg').is_file()
    assert (root / 'public/nested/one.jpg').read_bytes() == original


@pytest.mark.parametrize('name', ['../escape.jpg', 'bad:ads.jpg', 'CON.jpg', 'space.jpg ', 'a.png', '.hidden.jpg', 'folder\\x.jpg'])
def test_unsafe_rename_rejected(ready, name):
    _, client, root = ready
    item = get_items(client)[0]
    assert client.patch(f'/api/images/{item["id"]}', headers=HEADERS, json={'name': name}).status_code == 400
    assert (root / 'public/one.jpg').is_file()


@pytest.mark.parametrize('folder', ['..', '../outside', 'nested/../../outside', 'C:/Windows', 'nested\\..', '/absolute'])
def test_unsafe_paths_rejected(ready, folder):
    _, client, _ = ready
    item = get_items(client)[0]
    assert client.patch(f'/api/images/{item["id"]}', headers=HEADERS, json={'folder': folder}).status_code == 400


def test_junction_cannot_escape_library(ready):
    app, client, root = ready
    outside = root / 'outside'
    make_image(outside / 'secret.jpg')
    junction = root / 'public' / 'link'
    if os.name == 'nt':
        import subprocess
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(outside)], check=True, capture_output=True)
    else:
        junction.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(LibraryError):
            app.state.library.path('link/secret.jpg')
        app.state.library.scan()
        assert client.get('/api/images?recursive=true').json()['total'] == 2
    finally:
        if os.name == 'nt':
            junction.rmdir()  # Removes only the junction, not its target.
        else:
            junction.unlink()
    assert (outside / 'secret.jpg').is_file()


def test_interrupted_move_recovery(ready):
    app, client, root = ready
    item = get_items(client)[0]
    with app.state.library.db() as db:
        db.execute('INSERT INTO moves VALUES(?,?,?,?,?)', ('test-job', item['id'], 'one.jpg', 'nested/recovered.jpg', 'pending'))
    (root / 'public/one.jpg').rename(root / 'public/nested/recovered.jpg')
    app.state.library.recover_moves()
    assert app.state.library.image(item['id'])['path'] == 'nested/recovered.jpg'


def test_reuse_deleted_destination_preserves_old_metadata(ready):
    app, client, root = ready
    first, second = get_items(client)
    make_image(root / 'public' / 'gone.jpg')
    app.state.library.scan()
    (root / 'public' / 'gone.jpg').unlink()
    result = client.patch(f'/api/images/{first["id"]}', headers=HEADERS, json={'name': 'gone.jpg'})
    assert result.status_code == 200, result.text
    assert (root / 'public' / 'gone.jpg').is_file()


def test_password_change_revokes_all_sessions(env):
    app, client, _ = env
    configure(client)
    assert client.put('/api/settings/password', headers=HEADERS, json={'current_password': 'wrong', 'password': 'new-test-password-2026'}).status_code == 400
    assert client.put('/api/settings/password', headers=HEADERS, json={'current_password': ACCOUNT['password'], 'password': 'new-test-password-2026'}).status_code == 200
    assert client.get('/api/me').status_code == 401
    assert client.post('/api/login', headers=HEADERS, json=ACCOUNT).status_code == 401


def test_concurrent_moves_never_overwrite(ready):
    app, client, root = ready
    make_image(root / 'public' / 'other.jpg', 'blue')
    app.state.library.scan()
    items = [i for i in get_items(client) if i['format'] == 'JPEG']
    def move(i):
        try:
            app.state.library.move(i['id'], name='target.jpg')
            return True
        except LibraryError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(move, items)) == [False, True]
    assert sum(p.is_file() for p in (root / 'public').glob('*.jpg')) == 2


def test_corrupt_and_nonimage_files_are_not_indexed(env):
    app, client, root = env
    configure(client)
    (root / 'public' / 'broken.jpg').write_bytes(b'not-an-image')
    os.utime(root / 'public' / 'broken.jpg', (time.time() - 5, time.time() - 5))
    (root / 'public' / 'active.svg').write_text('<svg onload="alert(1)"/>')
    app.state.library.scan()
    assert client.get('/api/images?recursive=true').json()['total'] == 0
    assert app.state.library.scan_state['errors'] == 1


def test_unchanged_scan_does_not_bump_revision(ready):
    app, _, _ = ready
    previous = app.state.library.scan_state['revision']
    app.state.library.scan()
    assert app.state.library.scan_state['revision'] == previous


def test_recovery_does_not_adopt_unrelated_destination(ready):
    app, client, root = ready
    item = get_items(client)[0]
    with app.state.library.db() as db:
        db.execute('INSERT INTO moves VALUES(?,?,?,?,?)', ('interrupted', item['id'], 'one.jpg', 'nested/unrelated.jpg', 'pending'))
    make_image(root / 'public/nested/unrelated.jpg', 'black')
    (root / 'public/one.jpg').unlink()
    app.state.library.recover_moves()
    with app.state.library.db() as db:
        assert db.execute('SELECT status FROM moves WHERE id=?', ('interrupted',)).fetchone()[0] == 'aborted'
    assert app.state.library.image(item['id'])['path'] == 'one.jpg'


def test_exif_orientation_and_animation(env):
    app, client, root = env
    configure(client)
    im = Image.new('RGB', (800, 400), 'purple')
    exif = Image.Exif()
    exif[274] = 6
    im.save(root / 'public/rotated.jpg', exif=exif)
    frames = [Image.new('RGB', (100, 100), color) for color in ['red', 'blue']]
    frames[0].save(root / 'public/animated.gif', save_all=True, append_images=frames[1:], duration=200, loop=0)
    for path in (root / 'public').iterdir():
        os.utime(path, (time.time() - 5, time.time() - 5))
    app.state.library.scan()
    items = {i['name']: i for i in get_items(client)}
    assert (items['rotated.jpg']['width'], items['rotated.jpg']['height']) == (400, 800)
    assert items['animated.gif']['animated'] == 1
    preview = app.state.library.derivative(items['rotated.jpg'], 'thumb')
    with Image.open(preview) as image:
        assert image.height == 480 and image.width == 240


def test_oversized_decoded_image_is_rejected(env, monkeypatch):
    app, client, root = env
    configure(client)
    make_image(root / 'public/too-large.jpg')
    monkeypatch.setattr(Image, 'MAX_IMAGE_PIXELS', 1000)
    app.state.library.scan()
    assert client.get('/api/images?recursive=true').json()['total'] == 0
    assert app.state.library.scan_state['errors'] == 1
