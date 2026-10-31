"""Bounded TCP proxy handshakes (RFC 1928/1929), shared by relay and checks."""
import asyncio
import base64
import ipaddress
import struct


class ProxyError(OSError):
    pass


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
        raise ProxyError('Invalid upstream credential length')
    writer.write(b'\x05\x01\x02')
    await writer.drain()
    if await reader.readexactly(2) != b'\x05\x02':
        raise ProxyError('Upstream SOCKS authentication method rejected')
    writer.write(b'\x01' + bytes([len(u)]) + u + bytes([len(p)]) + p)
    await writer.drain()
    if await reader.readexactly(2) != b'\x01\x00':
        raise ProxyError('Upstream SOCKS authentication rejected')
    writer.write(b'\x05\x01\x00' + encode_address(host) + struct.pack('!H', port))
    await writer.drain()
    version, reply, reserved, atyp = await reader.readexactly(4)
    if version != 5 or reserved != 0 or reply != 0:
        raise ProxyError('Upstream SOCKS CONNECT rejected (%s)' % reply)
    await read_address(reader, atyp)
    await reader.readexactly(2)


def authority(host, port):
    return ('[%s]:%s' if ':' in host else '%s:%s') % (host, port)


async def http_connect(reader, writer, host, port, username, password):
    dest = authority(host, port)
    token = base64.b64encode(('%s:%s' % (username, password)).encode()).decode()
    writer.write(('CONNECT %s HTTP/1.1\r\nHost: %s\r\n'
                  'Proxy-Authorization: Basic %s\r\n\r\n' % (dest, dest, token)).encode())
    await writer.drain()
    head = await reader.readuntil(b'\r\n\r\n')
    if len(head) > 32768 or len(head.split(b'\r\n', 1)[0].split()) < 2:
        raise ProxyError('Invalid upstream HTTP response')
    if head.split(b'\r\n', 1)[0].split()[1] != b'200':
        raise ProxyError('Upstream HTTP CONNECT rejected')


async def open_tunnel(upstream, host, port, protocol='socks', timeout=12):
    """The complete connect + handshake has a single deadline, no direct fallback."""
    async def connect():
        reader, writer = await asyncio.open_connection(
            upstream['host'], upstream['socks_port' if protocol == 'socks' else 'http_port'], limit=32768)
        try:
            fn = socks_connect if protocol == 'socks' else http_connect
            await fn(reader, writer, host, port, upstream['auth_token'], upstream.get('password', 'none'))
            return reader, writer
        except BaseException:
            await close_writer(writer)
            raise
    return await asyncio.wait_for(connect(), timeout)
