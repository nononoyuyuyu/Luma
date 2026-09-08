import os
import time

import av
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from gallery.app import create_app
from gallery import video

HEADERS = {'X-Luma-Request': '1'}


def make_video(path, webm=False):
    with av.open(str(path), 'w', format='webm' if webm else 'mp4') as output:
        stream = output.add_stream('libvpx-vp9' if webm else 'libx264', rate=10)
        stream.width, stream.height, stream.pix_fmt = 160, 120, 'yuv420p'
        for index in range(30):
            frame = av.VideoFrame.from_image(Image.new('RGB', (160, 120), (index * 7, 90, 120)))
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    os.utime(path, (time.time() - 5, time.time() - 5))


@pytest.fixture
def media_env(tmp_path):
    app = create_app(tmp_path / 'data', tmp_path / 'public', False)
    with TestClient(app, base_url='http://localhost', client=('127.0.0.1', 1)) as client:
        account = {'username': 'video-fixture', 'password': 'synthetic-video-fixture'}
        client.post('/api/setup', headers=HEADERS, json=account).raise_for_status()
        client.post('/api/login', headers=HEADERS, json=account).raise_for_status()
        make_video(tmp_path / 'public/clip.mp4')
        app.state.library.scan()
        yield app, client, tmp_path


def test_video_metadata_poster_and_ranges(media_env):
    app, c, root = media_env
    rows = c.get('/api/images').json()['items']
    assert len(rows) == 1
    item = rows[0]
    assert item['kind'] == 'video' and item['duration'] == pytest.approx(3)
    assert item['video_codec'] == 'h264'
    poster = c.get(f'/api/media/{item["id"]}/thumb')
    assert poster.status_code == 200 and poster.headers['content-type'] == 'image/webp'
    for range_value in ['bytes=0-99', 'bytes=-100', 'bytes=100-']:
        response = c.get(f'/api/media/{item["id"]}/original', headers={'Range': range_value})
        assert response.status_code == 206 and response.headers['content-type'] == 'video/mp4'
        assert response.headers['cache-control'] == 'no-store'
    assert c.get(f'/api/media/{item["id"]}/original', headers={'Range': 'bytes=99999999-'}).status_code == 416
    c.cookies.clear()
    assert c.get(f'/api/media/{item["id"]}/original', headers={'Range': 'bytes=0-99'}).status_code == 401


def test_video_upload_and_management(media_env):
    app, c, root = media_env
    (root / 'public/target').mkdir()
    content = (root / 'public/clip.mp4').read_bytes()
    result = c.post('/api/upload?name=upload.mp4', content=content, headers=HEADERS)
    assert result.status_code == 200, result.text
    image_id = result.json()['id']
    edited = c.patch('/api/images/' + image_id, headers=HEADERS, json={'folder': 'target', 'name': 'renamed.mp4', 'tags': ['video']})
    assert edited.status_code == 200 and edited.json()['kind'] == 'video'
    assert edited.json()['tags'] == ['video']
    assert (root / 'public/target/renamed.mp4').read_bytes() == content


def test_invalid_video_never_published(media_env):
    _, c, root = media_env
    for data in [b'not a video', b'#EXTM3U\nhttp://example.invalid/video', b'<svg/>']:
        response = c.post('/api/upload?name=broken.mp4', content=data, headers=HEADERS)
        assert response.status_code == 400
    assert not (root / 'public/broken.mp4').exists()
    assert not list((root / 'data').glob('upload-*.tmp'))


def test_webm_probe_and_thumbnail(tmp_path):
    path = tmp_path / 'clip.webm'
    make_video(path, webm=True)
    info = video.inspect(path)
    assert info['format'] == 'WEBM' and info['video_codec'] == 'vp9'
    with video.inspect(path, thumbnail=True) as frame:
        assert frame.width == 160
