"""Bounded synthetic library benchmark, independent of the owner's public folder."""
import io
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from PIL import Image
from gallery.app import create_app


def main():
    results = Path(__file__).resolve().parents[1] / 'test-results'
    results.mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='benchmark-', dir=results))
    app = create_app(root / 'data', root / 'public', False)
    buf = io.BytesIO()
    Image.new('RGB', (128, 96), 'tan').save(buf, 'JPEG')
    payload = buf.getvalue()
    past = time.time() - 10
    for i in range(2000):
        folder = root / 'public' / f'collection-{i % 20:02}'
        folder.mkdir(exist_ok=True)
        path = folder / f'image-{i:05}.jpg'
        path.write_bytes(payload)
        os.utime(path, (past, past))
    with TestClient(app, base_url='http://localhost', client=('127.0.0.1', 1)) as client:
        account = {'username': 'benchmark-fixture', 'password': 'benchmark-fixture-only-2026'}
        headers = {'X-Luma-Request': '1'}
        client.post('/api/setup', json=account, headers=headers).raise_for_status()
        client.post('/api/login', json=account, headers=headers).raise_for_status()
        started = time.perf_counter()
        app.state.library.scan()
        initial = time.perf_counter() - started
        started = time.perf_counter()
        app.state.library.scan()
        incremental = time.perf_counter() - started
        latencies = []
        for i in range(30):
            started = time.perf_counter()
            response = client.get('/api/images', params={'recursive': 'true', 'offset': (i % 20) * 80, 'limit': 80})
            response.raise_for_status()
            assert response.json()['total'] == 2000
            latencies.append((time.perf_counter() - started) * 1000)
        image_id = response.json()['items'][0]['id']
        start = time.perf_counter()
        client.get(f'/api/media/{image_id}/thumb').raise_for_status()
        cold = (time.perf_counter() - start) * 1000
        start = time.perf_counter()
        client.get(f'/api/media/{image_id}/thumb').raise_for_status()
        warm = (time.perf_counter() - start) * 1000
    report = {'images': 2000, 'folders': 20, 'fixture_dimensions': '128x96', 'initial_scan_seconds': round(initial, 3),
              'unchanged_scan_seconds': round(incremental, 3), 'page_80_images_median_ms': round(statistics.median(latencies), 2),
              'page_80_images_p95_ms': round(sorted(latencies)[28], 2), 'thumbnail_cold_ms': round(cold, 2), 'thumbnail_cached_ms': round(warm, 2),
              'scope': 'Synthetic small JPEGs on this PC; excludes Wi-Fi, phone decoding and large-file decoding.'}
    (root / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({**report, 'report_path': str(root / 'report.json')}))


if __name__ == '__main__':
    main()
