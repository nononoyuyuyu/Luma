"""Local recovery without logging, command-line, or environment passwords."""
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gallery.library import Library
from gallery.security import password_hash


def main():
    library = Library(Path(__file__).resolve().parents[1] / 'data')
    if not library.setting('configured', False):
        raise SystemExit('Complete initial setup in the browser first.')
    first = getpass.getpass('New password (at least 12 characters): ')
    second = getpass.getpass('Confirm password: ')
    if first != second or not 12 <= len(first) <= 256:
        raise SystemExit('Passwords must match and contain 12 to 256 characters.')
    with library.db() as db:
        db.execute('UPDATE users SET password=?', (password_hash(first),))
        db.execute('DELETE FROM sessions')
    print('Password changed. All sessions have been revoked.')


if __name__ == '__main__':
    main()
