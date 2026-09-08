"""Small security primitives; never trust forwarded client addresses."""
import hashlib
import hmac
import ipaddress
import secrets
import threading
import time
from collections import OrderedDict


def password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=32768, r=8, p=1,
                            maxmem=64 * 1024 * 1024)
    return f"scrypt${salt.hex()}${digest.hex()}"


def password_matches(password: str, encoded: str) -> bool:
    try:
        _, salt, expected = encoded.split("$")
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt),
                                n=32768, r=8, p=1, maxmem=64 * 1024 * 1024)
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def address(value: str):
    result = ipaddress.ip_address(value)
    return result.ipv4_mapped if isinstance(result, ipaddress.IPv6Address) and result.ipv4_mapped else result


def networks(values: list[str]) -> list[str]:
    if len(values) > 100:
        raise ValueError("許可アドレスは100件までです。")
    result = []
    for value in values:
        if value.strip():
            result.append(str(ipaddress.ip_network(value.strip(), strict=False)))
    return list(dict.fromkeys(result))


def allowed(peer: str, values: list[str]) -> bool:
    try:
        ip = address(peer)
        return ip.is_loopback or any(ip in ipaddress.ip_network(n) for n in values)
    except ValueError:
        return False


class LoginLimiter:
    """Bounded per-address attempts, plus a global expensive-hash budget."""
    def __init__(self):
        self.lock = threading.Lock()
        self.entries = OrderedDict()
        self.global_attempts = []

    def take(self, peer: str) -> bool:
        now = time.monotonic()
        with self.lock:
            attempts = [t for t in self.entries.pop(peer, []) if now - t < 300]
            self.global_attempts = [t for t in self.global_attempts if now - t < 60]
            ok = len(attempts) < 10 and len(self.global_attempts) < 60
            if ok:
                attempts.append(now)
                self.global_attempts.append(now)
            self.entries[peer] = attempts
            while len(self.entries) > 2048:
                self.entries.popitem(last=False)
            return ok
