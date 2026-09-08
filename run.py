"""Single-process local server. A single instance owns filesystem mutations."""
import argparse
import os
import socket
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn

from gallery.app import PROJECT, create_app


def main():
    # Windows redirected logs default to cp932; use a deterministic encoding.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='backslashreplace')
    parser = argparse.ArgumentParser(description='Luma LAN image gallery')
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8790)
    parser.add_argument('--open', action='store_true', help='Open the local setup/login page')
    parser.add_argument('--https', action='store_true', help='Use data/tls/cert.pem and key.pem')
    args = parser.parse_args()
    data_dir = Path(os.environ.get('LUMA_DATA_DIR', PROJECT / 'data'))
    data_dir.mkdir(parents=True, exist_ok=True)
    # OS-held lock survives neither crashes nor shutdown; no stale PID guessing.
    lock = open(data_dir / 'server.lock', 'a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            lock.seek(0)
            if not lock.read(1):
                lock.write(b'0')
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit('Luma is already running with this data directory.')
    tls = {}
    if args.https:
        cert, key = data_dir / 'tls/cert.pem', data_dir / 'tls/key.pem'
        if not cert.is_file() or not key.is_file():
            sys.exit('Run: python tools/create_certificate.py --ip YOUR_LAN_IP')
        tls = {'ssl_certfile': str(cert), 'ssl_keyfile': str(key)}
    scheme = 'https' if args.https else 'http'
    print(f'\nLuma — Private gallery\nLocal: {scheme}://localhost:{args.port}\nPublic folder: {PROJECT / "public"}\n', flush=True)
    try:
        ips = sorted({entry[4][0] for entry in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)})
        for ip in ips:
            if not ip.startswith(('127.', '169.254.')):
                print(f'LAN candidate: {scheme}://{ip}:{args.port}', flush=True)
    except OSError:
        pass
    if args.open:
        timer = threading.Timer(1.5, lambda: webbrowser.open(f'{scheme}://localhost:{args.port}'))
        timer.daemon = True
        timer.start()
    uvicorn.run(create_app(data_dir=data_dir), host=args.host, port=args.port, proxy_headers=False,
                server_header=False, access_log=False, limit_concurrency=100, timeout_keep_alive=5, **tls)
    lock.close()


if __name__ == '__main__':
    main()
