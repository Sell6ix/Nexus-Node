"""Dev TLS certs so the PWA can use Web Push off localhost."""

from __future__ import annotations

import datetime as dt
from ipaddress import ip_address
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from core.netinfo import local_ipv4_addresses, tailscale_ipv4


def ensure_dev_cert(data_dir: Path) -> tuple[Path, Path]:
    """Create a self-signed cert covering localhost + LAN/Tailscale IPs."""
    cert_dir = Path(data_dir) / "certs"
    cert_dir.mkdir(parents=True, exist_ok=True)
    cert_path = cert_dir / "cert.pem"
    key_path = cert_dir / "key.pem"
    if cert_path.is_file() and key_path.is_file():
        return cert_path, key_path

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    names = ["localhost"]
    ips = ["127.0.0.1"]
    for ip in local_ipv4_addresses():
        if ip not in ips:
            ips.append(ip)
    ts = tailscale_ipv4()
    if ts and ts not in ips:
        ips.append(ts)

    san: list[x509.GeneralName] = [x509.DNSName(n) for n in names]
    for ip in ips:
        san.append(x509.IPAddress(ip_address(ip)))

    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Nexus Node")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5))
        .not_valid_after(dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    try:
        key_path.chmod(0o600)
    except OSError:
        pass
    return cert_path, key_path
