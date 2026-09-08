"""Generate a local self-signed TLS certificate; never overwrite a private key."""
import argparse
import datetime
import ipaddress
import os
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ip', action='append', required=True, help='LAN IP address; repeat for more addresses')
    args = parser.parse_args()
    ips = [ipaddress.ip_address(v) for v in args.ip]
    folder = Path(os.environ.get('LUMA_DATA_DIR', Path(__file__).resolve().parents[1] / 'data')) / 'tls'
    folder.mkdir(parents=True, exist_ok=True)
    key_path, cert_path = folder / 'key.pem', folder / 'cert.pem'
    if key_path.exists() or cert_path.exists():
        raise SystemExit('Certificate files already exist. They will not be overwritten.')
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Luma LAN Gallery')])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=365))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'), x509.IPAddress(ipaddress.ip_address('127.0.0.1')),
                                                       x509.IPAddress(ipaddress.ip_address('::1')), *[x509.IPAddress(ip) for ip in ips]]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    with key_path.open('xb') as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    if os.name != 'nt':
        key_path.chmod(0o600)
    with cert_path.open('xb') as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    print('Certificate generated. Start with: start.cmd --https')
    print('Certificate SHA-256 fingerprint: ' + cert.fingerprint(hashes.SHA256()).hex(':'))
    print('This is a self-signed certificate. Trust only the certificate from your own PC.')


if __name__ == '__main__':
    main()
