import io
import os
import time

import pytest
from PIL import Image
from fastapi.testclient import TestClient
from gallery.app import create_app
from tools.check_publication import inspect_blob

HEADERS = {'X-Luma-Request': '1'}


@pytest.fixture
def workspace(tmp_path):
    app = create_app(tmp_path / 'data', tmp_path / 'public', False)
    with TestClient(app, base_url='http://localhost', client=('127.0.0.1', 1)) as client:
        account = {'username': 'fixture', 'password': 'only-for-test-fixtures'}
        client.post('/api/setup', json=account, headers=HEADERS).raise_for_status()
        client.post('/api/login', json=account, headers=HEADERS).raise_for_status()
        (tmp_path / 'public/child/deep').mkdir(parents=True)
        (tmp_path / 'public/destination').mkdir()
        for name in ['root.jpg', 'child/one.jpg', 'child/deep/two.jpg']:
            p = tmp_path / 'public' / name
            Image.new('RGB', (32, 24), 'blue').save(p)
            os.utime(p, (time.time() - 5, time.time() - 5))
        app.state.library.scan()
        yield app, client, tmp_path


def payload():
    buf = io.BytesIO()
    Image.new('RGB', (40, 30), 'red').save(buf, 'PNG')
    return buf.getvalue()


def test_current_directory_only(workspace):
    _, c, _ = workspace
    assert [i['name'] for i in c.get('/api/images').json()['items']] == ['root.jpg']
    assert [i['name'] for i in c.get('/api/images?folder=child').json()['items']] == ['one.jpg']
    assert c.get('/api/images?folder=child/deep').json()['total'] == 1


def test_upload_is_indexed_without_full_scan(workspace):
    _, c, root = workspace
    response = c.post('/api/upload?folder=child&name=upload.png', content=payload(), headers=HEADERS)
    assert response.status_code == 200, response.text
    assert c.get('/api/images?folder=child').json()['total'] == 2
    assert (root / 'public/child/upload.png').read_bytes() == payload()
    assert not list((root / 'data').glob('upload-*.tmp'))


def test_upload_duplicate_and_invalid_content(workspace):
    _, c, root = workspace
    data = payload()
    c.post('/api/upload?name=upload.png', content=data, headers=HEADERS).raise_for_status()
    assert c.post('/api/upload?name=upload.png', content=data, headers=HEADERS).status_code == 400
    assert c.post('/api/upload?name=broken.png', content=b'not image', headers=HEADERS).status_code == 400
    assert c.post('/api/upload?name=script.svg', content=b'<svg/>', headers=HEADERS).status_code == 422
    assert (root / 'public/upload.png').read_bytes() == data
    assert not list((root / 'data').glob('upload-*.tmp'))


def test_upload_auth_csrf_size_and_path_boundary(workspace):
    app, c, root = workspace
    assert c.post('/api/upload?name=x.png', content=payload()).status_code == 403
    assert c.post('/api/upload?name=x.png', content=payload(), headers={**HEADERS, 'Origin': 'http://evil.example'}).status_code == 403
    assert c.post('/api/upload?name=x.png', content=b'', headers={**HEADERS, 'Content-Length': str(64 * 1024 * 1024 + 1)}).status_code == 413
    for params in [{'name': '../escape.png'}, {'name': 'x.png', 'folder': '../outside'}, {'name': 'x.png', 'folder': 'child/../../'}]:
        assert c.post('/api/upload', params=params, content=payload(), headers=HEADERS).status_code == 400
    c.cookies.clear()
    assert c.post('/api/upload?name=x.png', content=payload(), headers=HEADERS).status_code == 401
    assert not (root / 'public/x.png').exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows atomic directory rename')
def test_folder_move_preserves_tags_and_empty_folders(workspace):
    app, c, root = workspace
    image = c.get('/api/images?folder=child/deep').json()['items'][0]
    c.patch('/api/images/' + image['id'], headers=HEADERS, json={'tags': ['keep']})
    (root / 'public/child/empty').mkdir()
    response = c.post('/api/folders/move', headers=HEADERS, json={'source': 'child', 'parent': 'destination'})
    assert response.status_code == 200, response.text
    assert (root / 'public/destination/child/empty').is_dir()
    assert not (root / 'public/child').exists()
    detail = c.get('/api/images/' + image['id']).json()
    assert detail['path'] == 'destination/child/deep/two.jpg' and detail['tags'] == ['keep']
    app.state.library.scan()
    assert c.get('/api/images?folder=destination/child/deep').json()['total'] == 1


def test_folder_cycle_and_collision_rejected(workspace):
    _, c, root = workspace
    for parent in ['child', 'child/deep', '../outside']:
        assert c.post('/api/folders/move', headers=HEADERS, json={'source': 'child', 'parent': parent}).status_code == 400
    (root / 'public/destination/child').mkdir()
    assert c.post('/api/folders/move', headers=HEADERS, json={'source': 'child', 'parent': 'destination'}).status_code == 400
    assert (root / 'public/child/deep/two.jpg').is_file()


def test_folder_move_recovery(workspace):
    app, c, root = workspace
    src = root / 'public/child'
    info = src.stat()
    with app.state.library.db() as db:
        db.execute('INSERT INTO folder_moves VALUES(?,?,?,?,?)', ('interrupted-folder', 'child', 'destination/child', f'{info.st_dev}:{info.st_ino}', 'pending'))
    src.rename(root / 'public/destination/child')
    app.state.library.recover_moves()
    assert c.get('/api/images?folder=destination/child').json()['total'] == 1


def test_publication_rejects_images_runtime_and_personal_paths():
    for path in ['public/photo.jpg', 'data/library.sqlite3', 'static/favicon.svg', 'test-results/screenshot.png', 'secret.env']:
        assert inspect_blob(path, b'test') is not None
    assert inspect_blob('README.md', b'plain documentation') is None
    assert inspect_blob('README.md', b'path: ' + b'C:' + b'\\Users\\example\\secret') is not None
