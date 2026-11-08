"""Bounded TCP proxy handshakes (RFC 1928/1929), shared by relay and checks."""
import asyncio
import base64
import ipaddress
import ssl
import struct


class ProxyError(OSError):
    pass


class UpstreamError(ProxyError):
    """A proxy transport/authentication fault, not a destination refusal."""
    def __init__(self, message, reason="protocol"):
        super().__init__(message)
        self.reason = reason


class DestinationError(ProxyError):
    """The proxy answered but rejected the requested destination."""


async def close_writer(writer):
    if writer is not None:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), 2)
        except (OSError, asyncio.TimeoutError):
            pass


def encode_address(host):
    try:
        ip = ipaddress.ip_address(host)
        return bytes([1 if ip.version == 4 else 4]) + ip.packed
    except ValueError:
        data = host.encode('idna')
        if not 0 < len(data) <= 255:
            raise ProxyError('Invalid destination hostname')
        return b'\x03' + bytes([len(data)]) + data


async def read_address(reader, atyp):
    if atyp == 1:
        return str(ipaddress.ip_address(await reader.readexactly(4)))
    if atyp == 4:
        return str(ipaddress.ip_address(await reader.readexactly(16)))
    if atyp == 3:
        length = (await reader.readexactly(1))[0]
        if length == 0:
            raise ProxyError('Empty destination hostname')
        return (await reader.readexactly(length)).decode('ascii')
    raise ProxyError('Unsupported address type')


async def socks_connect(reader, writer, host, port, username, password):
    """Authenticate to upstream and consume the complete variable-length reply."""
    u, p = username.encode(), password.encode()
    if not 0 < len(u) <= 255 or not 0 < len(p) <= 255:
        raise UpstreamError('Invalid upstream credential length', 'authentication')
    writer.write(b'\x05\x01\x02')
    await writer.drain()
    if await reader.readexactly(2) != b'\x05\x02':
        raise UpstreamError('Upstream SOCKS authentication method rejected', 'authentication')
    writer.write(b'\x01' + bytes([len(u)]) + u + bytes([len(p)]) + p)
    await writer.drain()
    if await reader.readexactly(2) != b'\x01\x00':
        raise UpstreamError('Upstream SOCKS authentication rejected', 'authentication')
    writer.write(b'\x05\x01\x00' + encode_address(host) + struct.pack('!H', port))
    await writer.drain()
    version, reply, reserved, atyp = await reader.readexactly(4)
    if version != 5 or reserved != 0:
        raise UpstreamError('Invalid upstream SOCKS response')
    if reply != 0:
        raise DestinationError('Upstream SOCKS CONNECT rejected (%s)' % reply)
    await read_address(reader, atyp)
    await reader.readexactly(2)


def authority(host, port):
    return ('[%s]:%s' if ':' in host else '%s:%s') % (host, port)


async def http_connect(reader, writer, host, port, username, password):
    dest = authority(host, port)
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    writer.write(('CONNECT %s HTTP/1.1\r\nHost: %s\r\n'
                  'Proxy-Authorization: Basic %s\r\n\r\n' % (dest, dest, token)).encode())
    await writer.drain()
    head = await reader.readuntil(b'\r\n\r\n')
    if len(head) > 32768 or len(head.split(b'\r\n', 1)[0].split()) < 2:
        raise UpstreamError('Invalid upstream HTTP response')
    status = head.split(b'\r\n', 1)[0].split()[1]
    if status == b'407':
        raise UpstreamError('Upstream HTTP authentication rejected', 'authentication')
    if status != b'200':
        raise DestinationError('Upstream HTTP CONNECT rejected')


async def open_tunnel(upstream, host, port, protocol='socks', timeout=20):
    """The complete connect + handshake has a single deadline, no direct fallback.
    Supports 'socks', 'http', and 'https' (TLS-encrypted HTTP CONNECT) upstream."""
    if protocol not in ('socks', 'http', 'https', 'auto'):
        raise ProxyError('Unsupported upstream protocol')
    if protocol == 'https' and not upstream.get('https_port'):
        raise ProxyError('HTTPS upstream port is missing')
    stage = 'dial'
    async def connect():
        nonlocal stage
        use_https = (protocol == 'https') or (protocol == 'auto' and upstream.get('https_port'))
        try:
            if use_https and upstream.get('https_port'):
                ssl_ctx = ssl.create_default_context()
                reader, writer = await asyncio.open_connection(
                    upstream['host'], upstream['https_port'], ssl=ssl_ctx, limit=32768)
                fn = http_connect
            else:
                actual_proto = 'http' if protocol == 'http' else 'socks'
                target_port = upstream['http_port'] if actual_proto == 'http' else upstream['socks_port']
                reader, writer = await asyncio.open_connection(
                    upstream['host'], target_port, limit=32768)
                fn = http_connect if actual_proto == 'http' else socks_connect
        except OSError as exc:
            raise UpstreamError('Upstream connection failed', 'dial') from exc

        try:
            stage = 'handshake'
            await fn(reader, writer, host, port, upstream['auth_token'], upstream.get('password', 'none'))
            return reader, writer
        except BaseException:
            await close_writer(writer)
            raise
    try:
        return await asyncio.wait_for(connect(), timeout)
    except asyncio.TimeoutError as exc:
        if stage == 'dial':
            raise UpstreamError('Upstream connection timed out', 'dial') from exc
        raise DestinationError('Upstream CONNECT timed out') from exc
