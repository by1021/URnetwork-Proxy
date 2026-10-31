"""One TCP listener for HTTP forward proxy, CONNECT and SOCKS5 CONNECT."""
import asyncio
import base64
import hmac
from urllib.parse import urlsplit

from .protocol import ProxyError, close_writer, open_tunnel, read_address

HEADER_LIMIT = 32768


class MixedProxyServer:
    """Each accepted connection snapshots its upstream; established tunnels never migrate."""
    def __init__(self, host, port, get_upstream, *, username="", password="",
                 on_result=None, timeout=15, idle_timeout=300, max_connections=256):
        self.host, self.port = host, port
        self.get_upstream = get_upstream
        self.username, self.password = username, password
        self.on_result = on_result
        self.timeout, self.idle_timeout = timeout, idle_timeout
        self.max_connections = max_connections
        self.server = None
        self.tasks = set()
        self.connections = 0

    async def start(self):
        self.server = await asyncio.start_server(self._accept, self.host, self.port, limit=HEADER_LIMIT)
        self.port = self.server.sockets[0].getsockname()[1]

    async def close(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _accept(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        if self.connections >= self.max_connections:
            await close_writer(writer)
            self.tasks.discard(task)
            return
        self.connections += 1
        try:
            first = await asyncio.wait_for(reader.readexactly(1), self.timeout)
            if first == b"\x05":
                await self._socks(reader, writer)
            elif b"A" <= first <= b"Z":
                await self._http(first, reader, writer)
        except (OSError, ValueError, UnicodeError, asyncio.IncompleteReadError,
                asyncio.LimitOverrunError, asyncio.TimeoutError):
            pass
        finally:
            self.connections -= 1
            self.tasks.discard(task)
            await close_writer(writer)

    async def _pipe(self, source, dest):
        while True:
            chunk = await asyncio.wait_for(source.read(65536), self.idle_timeout)
            if not chunk:
                if dest.can_write_eof():
                    dest.write_eof()
                return
            dest.write(chunk)
            await asyncio.wait_for(dest.drain(), self.idle_timeout)

    async def _duplex(self, local_r, local_w, remote_r, remote_w):
        # Retain the opposite direction after a TCP half-close (uploads often use it).
        tasks = [asyncio.create_task(self._pipe(local_r, remote_w)),
                 asyncio.create_task(self._pipe(remote_r, local_w))]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def _upstream(self):
        upstream = self.get_upstream()
        if not upstream:
            raise ProxyError("Gateway has no healthy upstream")
        return dict(upstream)

    async def _socks(self, reader, writer):
        async def handshake():
            count = (await reader.readexactly(1))[0]
            methods = await reader.readexactly(count)
            method = 2 if self.username else 0
            if method not in methods:
                writer.write(b"\x05\xff")
                await writer.drain()
                return None
            writer.write(bytes([5, method]))
            await writer.drain()
            if method == 2:
                version, ulen = await reader.readexactly(2)
                user = await reader.readexactly(ulen)
                plen = (await reader.readexactly(1))[0]
                password = await reader.readexactly(plen)
                ok = (version == 1 and hmac.compare_digest(user, self.username.encode())
                      and hmac.compare_digest(password, self.password.encode()))
                writer.write(b"\x01" + (b"\x00" if ok else b"\x01"))
                await writer.drain()
                if not ok:
                    return None
            version, command, reserved, atyp = await reader.readexactly(4)
            if version != 5 or reserved != 0 or command != 1:
                writer.write(b"\x05\x07\x00\x01" + b"\0" * 6)
                await writer.drain()
                return None
            host = await read_address(reader, atyp)
            port = int.from_bytes(await reader.readexactly(2), "big")
            if not port:
                raise ProxyError("Invalid destination port")
            return host, port

        dest = await asyncio.wait_for(handshake(), self.timeout)
        if not dest:
            return
        remote_w = None
        try:
            remote_r, remote_w = await open_tunnel(self._upstream(), *dest, timeout=self.timeout)
        except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError):
            writer.write(b"\x05\x01\x00\x01" + b"\0" * 6)
            await writer.drain()
            return
        try:
            writer.write(b"\x05\x00\x00\x01" + b"\0" * 6)
            await writer.drain()
            await self._duplex(reader, writer, remote_r, remote_w)
        finally:
            await close_writer(remote_w)

    async def _http_error(self, writer, code, label):
        challenge = 'Proxy-Authenticate: Basic realm="Local Gateway"\r\n' if code == 407 else ""
        body = (label + "\n").encode()
        writer.write((f"HTTP/1.1 {code} {label}\r\nConnection: close\r\n{challenge}"
                      f"Content-Length: {len(body)}\r\n\r\n").encode() + body)
        await writer.drain()

    async def _http(self, first, reader, writer):
        remote_w = None
        committed = False
        try:
            head = first + await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), self.timeout)
            if len(head) > HEADER_LIMIT:
                raise ValueError("Headers too large")
            lines = head[:-4].decode("iso-8859-1").split("\r\n")
            method, target, version = lines[0].split()
            if version not in ("HTTP/1.0", "HTTP/1.1") or not method.isalpha():
                raise ValueError("Invalid request")
            headers = []
            for line in lines[1:]:
                key, sep, value = line.partition(":")
                if not sep or not key or not key.isascii() or not all(c.isalnum() or c in "!#$%&'*+-.^_`|~" for c in key):
                    raise ValueError("Invalid header")
                value = value.strip()
                if any(ord(c) < 32 and c != "\t" for c in value):
                    raise ValueError("Invalid header value")
                headers.append((key, value))
            def values(key):
                return [v for k, v in headers if k.lower() == key]
            if self.username:
                expected = "Basic " + base64.b64encode(
                    f"{self.username}:{self.password}".encode()).decode()
                auth = values("proxy-authorization")
                if len(auth) != 1 or not hmac.compare_digest(auth[0].encode(), expected.encode()):
                    await self._http_error(writer, 407, "Proxy Authentication Required")
                    return
            lengths, transfers = values("content-length"), values("transfer-encoding")
            if len(lengths) > 1 or len(transfers) > 1 or (lengths and transfers):
                raise ValueError("Ambiguous HTTP framing")
            length = 0
            if lengths:
                if not lengths[0].isascii() or not lengths[0].isdigit():
                    raise ValueError("Invalid content length")
                length = int(lengths[0])
            chunked = bool(transfers)
            if chunked and transfers[0].lower() != "chunked":
                raise ValueError("Unsupported transfer encoding")

            if method == "CONNECT":
                parsed = urlsplit("//" + target)
                host, port = parsed.hostname, parsed.port
                if not host or not port or parsed.path or parsed.username or parsed.query or parsed.fragment:
                    raise ValueError("Invalid CONNECT authority")
                remote_r, remote_w = await open_tunnel(
                    self._upstream(), host, port, protocol="http", timeout=self.timeout)
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
                committed = True
                await self._duplex(reader, writer, remote_r, remote_w)
                return

            parsed = urlsplit(target)
            if parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.fragment:
                raise ValueError("HTTP proxy requires an absolute http URL")
            if parsed.port is not None and not 1 <= parsed.port <= 65535:
                raise ValueError("Invalid destination port")
            upstream = self._upstream()
            remote_r, remote_w = await asyncio.wait_for(asyncio.open_connection(
                upstream["host"], upstream["http_port"], limit=HEADER_LIMIT), self.timeout)
            token = base64.b64encode(
                f'{upstream["auth_token"]}:{upstream.get("password", "none")}'.encode()).decode()
            removed = {"proxy-authorization", "proxy-connection", "connection", "keep-alive",
                       "host", "upgrade", "expect", "te", "trailer"}
            removed.update(x.strip().lower() for v in values("connection") for x in v.split(","))
            # Never strip framing headers named by an untrusted Connection header.
            if {"content-length", "transfer-encoding"} & removed:
                raise ValueError("Invalid Connection header")
            output = [f"{method} {target} HTTP/1.1", f"Host: {parsed.netloc}",
                      f"Proxy-Authorization: Basic {token}", "Connection: close"]
            output.extend(f"{k}: {v}" for k, v in headers if k.lower() not in removed)
            remote_w.write(("\r\n".join(output) + "\r\n\r\n").encode("iso-8859-1"))
            await remote_w.drain()
            if values("expect"):
                writer.write(b"HTTP/1.1 100 Continue\r\n\r\n")
                await writer.drain()

            async def request_body():
                if chunked:
                    while True:
                        line = await asyncio.wait_for(reader.readuntil(b"\r\n"), self.idle_timeout)
                        if len(line) > 4096:
                            raise ValueError("Chunk header too large")
                        size = int(line.split(b";", 1)[0].strip(), 16)
                        if size < 0:
                            raise ValueError("Negative chunk")
                        remote_w.write(("%x\r\n" % size).encode())
                        await remote_w.drain()
                        if size == 0:
                            # Bounded trailers are consumed, not forwarded (credentials may lurk here).
                            total = 0
                            while True:
                                trailer = await asyncio.wait_for(reader.readuntil(b"\r\n"), self.idle_timeout)
                                total += len(trailer)
                                if total > HEADER_LIMIT:
                                    raise ValueError("Trailers too large")
                                if trailer == b"\r\n":
                                    break
                            remote_w.write(b"\r\n")
                            await remote_w.drain()
                            return
                        await copy_exact(size)
                        if await asyncio.wait_for(reader.readexactly(2), self.idle_timeout) != b"\r\n":
                            raise ValueError("Invalid chunk ending")
                        remote_w.write(b"\r\n")
                else:
                    await copy_exact(length)

            async def copy_exact(count):
                while count:
                    data = await asyncio.wait_for(reader.readexactly(min(count, 65536)), self.idle_timeout)
                    remote_w.write(data)
                    await asyncio.wait_for(remote_w.drain(), self.idle_timeout)
                    count -= len(data)

            # Do not replay bodies or forward pipelined requests using stale/auth-less headers.
            committed = True
            upload = asyncio.create_task(request_body())
            response = asyncio.create_task(self._pipe(remote_r, writer))
            try:
                done, _ = await asyncio.wait((upload, response), return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
                if response not in done:
                    await response
            finally:
                for task in (upload, response):
                    task.cancel()
                await asyncio.gather(upload, response, return_exceptions=True)
        except (ValueError, UnicodeError, asyncio.LimitOverrunError):
            if not committed:
                await self._http_error(writer, 400, "Bad Request")
        except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError):
            if not committed:
                await self._http_error(writer, 502, "Bad Gateway")
        finally:
            await close_writer(remote_w)
