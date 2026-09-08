"""Inspect staged Git blobs, never the owner's images or authentication files."""
import re
import subprocess
import sys
from pathlib import PurePosixPath

ROOT_FILES = {'.gitignore', '.gitattributes', 'README.md', 'requirements.txt', 'requirements-dev.txt',
              'requirements.lock', 'run.py', 'start.cmd', 'stop.ps1'}
STATIC_FILES = {'static/index.html', 'static/style.css', 'static/app.js', 'static/manifest.webmanifest'}


def permitted(name):
    path = PurePosixPath(name)
    return (name in ROOT_FILES or name in STATIC_FILES or
            len(path.parts) == 2 and path.parts[0] in {'gallery', 'tests', 'tools'} and
            path.suffix in ({'.py', '.ps1'} if path.parts[0] == 'tools' else {'.py'}))


def inspect_blob(name, data):
    if not permitted(name):
        return 'not a permitted source path'
    if len(data) > 512_000 or b'\x00' in data:
        return 'binary or oversized file'
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError:
        return 'not UTF-8 source text'
    patterns = [
        r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
        r'gh[pousr]_[A-Za-z0-9]{20,}', r'github_pat_[A-Za-z0-9_]{20,}',
        r'AKIA[A-Z0-9]{16}', r'data:image/[^;]+;base64,',
        r'(?i)[a-z]:[\\/](?:Users[\\/]|image_Viewer)',
    ]
    if any(re.search(pattern, text) for pattern in patterns):
        return 'credential, embedded image, or private absolute path pattern'
    return None


def main():
    if '--history' in sys.argv:
        checked = set()
        commits = subprocess.check_output(['git', 'rev-list', '--all']).splitlines()
        for commit in commits:
            entries = subprocess.check_output(['git', 'ls-tree', '-r', '-z', commit.decode()]).split(b'\0')
            for entry in entries:
                if not entry:
                    continue
                meta, raw_name = entry.split(b'\t', 1)
                mode, kind, oid = meta.split()
                name = raw_name.decode('utf-8')
                if mode not in {b'100644', b'100755'} or kind != b'blob':
                    raise SystemExit(f'BLOCKED history entry: {name}')
                if (oid, name) in checked:
                    continue
                checked.add((oid, name))
                issue = inspect_blob(name, subprocess.check_output(['git', 'cat-file', 'blob', oid.decode()]))
                if issue:
                    raise SystemExit(f'BLOCKED history: {name}: {issue}')
        print(f'PASS: {len(commits)} commits, {len(checked)} source/document blobs inspected.')
        return
    entries = subprocess.check_output(['git', 'ls-files', '--stage', '-z']).split(b'\0')
    count = 0
    for entry in entries:
        if not entry:
            continue
        meta, raw_name = entry.split(b'\t', 1)
        mode, oid, stage = meta.split()
        name = raw_name.decode('utf-8')
        if mode not in {b'100644', b'100755'} or stage != b'0':
            raise SystemExit(f'BLOCKED: unsupported Git entry: {name}')
        data = subprocess.check_output(['git', 'cat-file', 'blob', oid.decode()])
        issue = inspect_blob(name, data)
        if issue:
            # Report the reason and path, never the matched sensitive value.
            raise SystemExit(f'BLOCKED: {name}: {issue}')
        count += 1
    if not count:
        raise SystemExit('No staged source files to inspect.')
    print(f'PASS: {count} staged source/document files; no image or runtime data files.')


if __name__ == '__main__':
    main()
