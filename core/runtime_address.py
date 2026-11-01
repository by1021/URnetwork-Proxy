"""Local route addresses, not NAT/public-IP lookup or proxy exit addresses."""
import ipaddress
import socket
from urllib.parse import urlsplit


def detect_runtime_ips(bind_host="127.0.0.1"):
    """UDP connect asks the OS for a source address; no packets are sent."""
    addresses = []

    def add(value):
        try:
            ip = ipaddress.ip_address(value)
        except ValueError:
            return
        if not (ip.is_unspecified or ip.is_multicast or ip.is_loopback or ip.is_link_local):
            if str(ip) not in addresses:
                addresses.append(str(ip))

    host = bind_host.strip("[]")
    add(host)
    # Numeric destinations avoid DNS and never contact an external IP service.
    for family, target in ((socket.AF_INET, ("192.0.2.1", 80)),
                           (socket.AF_INET6, ("2001:db8::1", 80, 0, 0))):
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as probe:
                probe.settimeout(0.2)
                probe.connect(target)
                add(probe.getsockname()[0])
        except OSError:
            pass
    return addresses or (["::1"] if ":" in host else ["127.0.0.1"])


def host_allowed(authority, allowed):
    """Match the Host header exactly as the management handler does."""
    if not allowed or "*" in allowed:
        return True
    name = authority[:authority.index("]") + 1] if authority.startswith("[") and "]" in authority else authority.split(":")[0]
    return authority in allowed or name in allowed or name.strip("[]") in allowed


def _authority(host, port):
    return f"{'[' + host + ']' if ':' in host else host}:{port}"


def _allowed_alias(entry, port):
    """Only DNS aliases may supplement the actual listener's IP candidates."""
    if not isinstance(entry, str) or not entry or any(ord(c) <= 32 or c in "/\\?#@" for c in entry):
        return None
    try:
        parsed = urlsplit("http://" + entry)
        if not parsed.hostname or (parsed.port is not None and not 1 <= parsed.port <= 65535):
            return None
        try:
            ipaddress.ip_address(parsed.hostname)
        except ValueError:
            return entry if parsed.port is not None else f"{entry}:{port}"
    except ValueError:
        pass
    return None


def runtime_info(ips, bind_host, port, *, dual_stack=False, allowed_hosts=None):
    host = bind_host.strip("[]")
    if host == "localhost":
        host = "127.0.0.1"
    wildcard = host in ("0.0.0.0", "::")
    loopback = not wildcard and ipaddress.ip_address(host).is_loopback
    if wildcard:
        version = 6 if ":" in host else 4
        hosts = [ip for ip in ips if dual_stack or ipaddress.ip_address(ip).version == version]
        hosts += ["127.0.0.1", "::1"] if dual_stack else (["::1"] if version == 6 else ["127.0.0.1"])
    else:
        hosts = [host]
    authorities = [_authority(ip, port) for ip in hosts]
    if allowed_hosts and "*" not in allowed_hosts:
        authorities = [value for value in authorities if host_allowed(value, allowed_hosts)]
        # A configured DNS alias may be the only accepted Host (e.g. reverse proxy).
        authorities += [value for entry in allowed_hosts
                        if (value := _allowed_alias(entry, port)) and host_allowed(value, allowed_hosts)]
    urls = [f"http://{value}/" for value in dict.fromkeys(authorities)]
    return dict(primary_ip=ips[0], ips=list(ips), web_host=bind_host, web_port=port,
                management_urls=urls, loopback_only=loopback,
                loopback_fallback=all(ipaddress.ip_address(ip).is_loopback for ip in ips))
