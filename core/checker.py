"""Bounded end-to-end probes: authentication, verified TLS, valid IP response."""
import asyncio
import base64
import ipaddress
import socket
import ssl
import time
import urllib.request


def _remaining(deadline):
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError('Probe deadline exceeded')
    return left


def _recv(sock, count, deadline):
    data = b''
    while len(data) < count:
        sock.settimeout(_remaining(deadline))
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise OSError('Unexpected end of stream')
        data += chunk
    return data


def _head(sock, deadline):
    head = bytearray()
    while not head.endswith(b'\r\n\r\n'):
        head.extend(_recv(sock, 1, deadline))
        if len(head) > 16384:
            raise OSError('Response headers too large')
    return bytes(head)


def test_tcp_ping(host, port, timeout=3):
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout):
            return True, round((time.monotonic() - start) * 1000, 2), None
    except OSError as exc:
        return False, None, type(exc).__name__


def _probe(host, port, token, password, target_host, timeout, protocol,
           target_port=443, use_tls=True):
    start = time.monotonic()
    deadline = start + timeout
    sock = None
    try:
        sock = socket.create_connection((host, port), _remaining(deadline))
        if protocol == 'socks':
            user, pwd = (token or '').encode(), (password or '').encode()
            has_creds = bool(user and user != b'none')
            sock.sendall(b'\x05\x01\x02' if has_creds else b'\x05\x01\x00')
            method_reply = _recv(sock, 2, deadline)
            if has_creds:
                if method_reply != b'\x05\x02':
                    raise OSError('SOCKS authentication method rejected')
                if not 0 < len(user) <= 255 or not 0 < len(pwd) <= 255:
                    raise ValueError('Invalid credential length')
                sock.sendall(b'\x01' + bytes([len(user)]) + user + bytes([len(pwd)]) + pwd)
                if _recv(sock, 2, deadline) != b'\x01\x00':
                    raise OSError('SOCKS authentication rejected')
            else:
                if method_reply != b'\x05\x00':
                    raise OSError('SOCKS authentication method rejected')
            domain = target_host.encode('idna')
            sock.sendall(b'\x05\x01\x00\x03' + bytes([len(domain)]) + domain + target_port.to_bytes(2, 'big'))
            version, rep, reserved, atyp = _recv(sock, 4, deadline)
            if version != 5 or rep or reserved:
                raise OSError('SOCKS CONNECT rejected')
            length = {1: 4, 4: 16}.get(atyp)
            if atyp == 3:
                length = _recv(sock, 1, deadline)[0]
            if length is None:
                raise OSError('Invalid SOCKS reply')
            _recv(sock, length + 2, deadline)
        else:
            auth = base64.b64encode(f'{token}:{password}'.encode()).decode()
            dest = f'{target_host}:{target_port}'
            sock.sendall((f'CONNECT {dest} HTTP/1.1\r\nHost: {dest}\r\n'
                          f'Proxy-Authorization: Basic {auth}\r\n\r\n').encode())
            header = _head(sock, deadline)
            if header.split(b'\r\n', 1)[0].split()[1:2] != [b'200']:
                raise OSError('HTTP CONNECT rejected')
        if use_tls:
            sock.settimeout(_remaining(deadline))
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=target_host)
        sock.settimeout(_remaining(deadline))
        sock.sendall((f'GET / HTTP/1.1\r\nHost: {target_host}\r\n'
                      'Connection: close\r\nAccept: text/plain\r\n\r\n').encode())
        header = _head(sock, deadline)
        if header.split(b'\r\n', 1)[0].split()[1:2] != [b'200']:
            raise OSError('Probe target returned non-200 status')
        body = b''
        while True:
            sock.settimeout(_remaining(deadline))
            chunk = sock.recv(1024)
            if not chunk:
                break
            body += chunk
            if len(body) > 4096:
                raise OSError('Probe response too large')
        if b'transfer-encoding: chunked' in header.lower():
            decoded = b''
            while body:
                line, sep, body = body.partition(b'\r\n')
                size = int(line.split(b';')[0], 16)
                if not sep or size < 0 or len(body) < size + 2:
                    raise ValueError('Invalid chunked response')
                if not size:
                    break
                decoded += body[:size]
                body = body[size + 2:]
            body = decoded
        ip = str(ipaddress.ip_address(body.decode('ascii').strip()))
        return True, round((time.monotonic() - start) * 1000, 2), ip
    except (OSError, ValueError, IndexError) as exc:
        return False, None, type(exc).__name__
    finally:
        if sock is not None:
            sock.close()


def test_http_proxy(host, port, token, password='none', target_host='api.ipify.org', timeout=15, **kwargs):
    return _probe(host, port, token, password, target_host, timeout, 'http', **kwargs)


def test_socks5_proxy(host, port, token, password='none', target_host='api.ipify.org', timeout=15, **kwargs):
    return _probe(host, port, token, password, target_host, timeout, 'socks', **kwargs)


def test_full_proxy(host, http_port, socks_port, token, password='none', timeout=15):
    http = test_http_proxy(host, http_port, token, password, timeout=timeout)
    socks = test_socks5_proxy(host, socks_port, token, password, timeout=timeout)
    return dict(http_ok=http[0], http_ms=http[1], http_result=http[2],
                socks_ok=socks[0], socks_ms=socks[1], socks_result=socks[2])


async def probe_upstream(upstream, timeout=15):
    targets = ('api.ipify.org', 'checkip.amazonaws.com', 'icanhazip.com')
    async def one(fn, port):
        last = (False, None, 'Probe failed')
        # Try primary and secondary targets, with quick retry on transient timeout
        for target in targets:
            last = await asyncio.to_thread(fn, upstream['host'], port,
                upstream['auth_token'], upstream.get('password', 'none'), target, timeout)
            if last[0]:
                return last
        # One quick retry on transient failure to avoid false-positive dead node rotation
        for target in targets[:2]:
            last = await asyncio.to_thread(fn, upstream['host'], port,
                upstream['auth_token'], upstream.get('password', 'none'), target, timeout)
            if last[0]:
                return last
        return last
    http, socks = await asyncio.gather(one(test_http_proxy, upstream['http_port']),
                                       one(test_socks5_proxy, upstream['socks_port']))
    ok = http[0] and socks[0]
    return dict(ok=ok, http_ok=http[0], socks_ok=socks[0],
                exit_ip=socks[2] if socks[0] else (http[2] if http[0] else ''),
                latency_ms=max(http[1] or 0, socks[1] or 0),
                error='' if ok else 'HTTP: %s; SOCKS5: %s' % (
                    'ok' if http[0] else http[2], 'ok' if socks[0] else socks[2]))


async def probe_control():
    """Detect local network/probe-service outage before spending new proxy slots."""
    def check():
        for host in ('api.ipify.org', 'checkip.amazonaws.com'):
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(f'https://{host}/', timeout=4) as response:
                    ipaddress.ip_address(response.read(128).decode().strip())
                    return True
            except (OSError, ValueError):
                pass
        return False
    return await asyncio.to_thread(check)
