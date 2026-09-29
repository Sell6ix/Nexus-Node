"""Discover LAN / Tailscale URLs so phones can find the hub."""

from __future__ import annotations

import socket
from ipaddress import ip_address


def _is_usable(ip: str) -> bool:
    try:
        addr = ip_address(ip)
    except ValueError:
        return False
    if addr.is_loopback or addr.is_link_local or addr.is_multicast:
        return False
    return addr.version == 4


def local_ipv4_addresses() -> list[str]:
    found: list[str] = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if _is_usable(ip) and ip not in found:
                found.append(ip)
    except OSError:
        pass
    # UDP trick: no packets sent, just bind routing decision
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("1.1.1.1", 80))
        ip = sock.getsockname()[0]
        sock.close()
        if _is_usable(ip) and ip not in found:
            found.insert(0, ip)
    except OSError:
        pass
    return found


def tailscale_ipv4() -> str | None:
    """Best-effort: Tailscale CGNAT is 100.64.0.0/10."""
    for ip in local_ipv4_addresses():
        try:
            packed = int(ip_address(ip))
        except ValueError:
            continue
        if 0x64400000 <= packed <= 0x647FFFFF:
            return ip
    return None


def hub_urls(host: str, port: int) -> list[str]:
    urls = [f"http://127.0.0.1:{port}"]
    if host not in {"127.0.0.1", "localhost", "::1"}:
        for ip in local_ipv4_addresses():
            urls.append(f"http://{ip}:{port}")
    ts = tailscale_ipv4()
    if ts:
        url = f"http://{ts}:{port}"
        if url not in urls:
            urls.append(url)
    # unique preserve order
    out: list[str] = []
    for u in urls:
        if u not in out:
            out.append(u)
    return out


def print_hub_banner(host: str, port: int) -> None:
    print(f"Nexus Node web hub on port {port}")
    print("Open on this PC or phone:")
    for url in hub_urls(host, port):
        kind = "this PC"
        if "127.0.0.1" in url:
            kind = "this PC"
        elif url.startswith("http://100."):
            kind = "Tailscale (phone away from home)"
        else:
            kind = "same Wi‑Fi"
        print(f"  {url}   [{kind}]")
    print("First register = owner. Install as app from the browser menu.")
    print("Away from home: install Tailscale on PC + phone, use the 100.x URL.")
    print("Closed-app reminder push needs HTTPS (localhost or Tailscale Serve).")
