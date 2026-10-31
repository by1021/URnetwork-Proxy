import asyncio
import base64
from core.protocol import close_writer, read_address


class FakeUpstream:
    """Real loopback HTTP/SOCKS endpoints with fragmented SOCKS domain replies."""
    def __init__(self, marker="203.0.113.10"):
        self.marker = marker
        self.requests = []
        self.writers = set()
        self.servers = []
        self.healthy = True

    async def start(self):
        self.servers = [await asyncio.start_server(self.http, "127.0.0.1", 0),
                        await asyncio.start_server(self.socks, "127.0.0.1", 0)]
        self.upstream = dict(host="127.0.0.1", http_port=self.servers[0].sockets[0].getsockname()[1],
                             socks_port=self.servers[1].sockets[0].getsockname()[1],
                             auth_token="fixture-token", password="none", client_id=self.marker)
        return self

    async def close(self):
        for server in self.servers:
            server.close()
            await server.wait_closed()
        for writer in list(self.writers):
            await close_writer(writer)

    async def tunnel(self, reader, writer):
        first = await reader.read(65536)
        if first.startswith(b"GET "):
            body = self.marker.encode()
            writer.write(b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: " +
                         str(len(body)).encode() + b"\r\n\r\n" + body)
            await writer.drain()
        else:
            writer.write(first)
            await writer.drain()
            while data := await reader.read(65536):
                writer.write(data)
                await writer.drain()

    async def socks(self, reader, writer):
        self.writers.add(writer)
        try:
            if not self.healthy:
                return
            if await reader.readexactly(3) != b"\x05\x01\x02":
                return
            writer.write(b"\x05")
            await writer.drain()
            await asyncio.sleep(.001)
            writer.write(b"\x02")
            await writer.drain()
            version, n = await reader.readexactly(2)
            user = await reader.readexactly(n)
            n = (await reader.readexactly(1))[0]
            pwd = await reader.readexactly(n)
            if user != b"fixture-token" or pwd != b"none":
                writer.write(b"\x01\x01")
                await writer.drain()
                return
            writer.write(b"\x01\x00")
            await writer.drain()
            v, cmd, r, atyp = await reader.readexactly(4)
            host = await read_address(reader, atyp)
            port = int.from_bytes(await reader.readexactly(2), "big")
            self.requests.append(("socks", host, port))
            for fragment in (b"\x05\x00", b"\x00\x03\x09", b"localhost", b"\x00\x50"):
                writer.write(fragment)
                await writer.drain()
                await asyncio.sleep(.001)
            await self.tunnel(reader, writer)
        except (OSError, asyncio.IncompleteReadError):
            pass
        finally:
            self.writers.discard(writer)
            await close_writer(writer)

    async def http(self, reader, writer):
        self.writers.add(writer)
        try:
            if not self.healthy:
                return
            head = await reader.readuntil(b"\r\n\r\n")
            self.requests.append(("http", head.decode()))
            auth = base64.b64encode(b"fixture-token:none")
            if b"Proxy-Authorization: Basic " + auth not in head:
                writer.write(b"HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
                return
            if head.startswith(b"CONNECT "):
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
                await self.tunnel(reader, writer)
            else:
                headers = {k.lower(): v.strip() for k, v in (
                    line.split(b":", 1) for line in head.split(b"\r\n")[1:] if b":" in line)}
                body = b""
                if b"content-length" in headers:
                    body = await reader.readexactly(int(headers[b"content-length"]))
                elif headers.get(b"transfer-encoding") == b"chunked":
                    while True:
                        size = int((await reader.readuntil(b"\r\n")).strip(), 16)
                        if size == 0:
                            await reader.readexactly(2)
                            break
                        body += await reader.readexactly(size)
                        await reader.readexactly(2)
                result = body or self.marker.encode()
                writer.write(b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: " +
                             str(len(result)).encode() + b"\r\n\r\n" + result)
                await writer.drain()
        except (OSError, asyncio.IncompleteReadError):
            pass
        finally:
            self.writers.discard(writer)
            await close_writer(writer)
