"""Loopback-only manager + real TCP/TLS data plane; no account credentials."""
import asyncio
import base64
import json
from pathlib import Path
import ssl
import sys
import tempfile
import threading
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.gateway import GatewayManager
from core.protocol import close_writer, encode_address, open_tunnel, read_address

TOKEN = "test-fake-key-runtime"


class FixtureAccount:
    def __init__(self, ports):
        self.ports, self.created, self.removed = ports, [], []
        self.entered, self.release = threading.Event(), threading.Event()

    def get_proxy_capabilities(self):
        return {"active_limit": 20, "creates_per_minute": 20, "known": True}

    def create_proxy(self, **kwargs):
        number = len(self.created) + 1
        if number > 3:
            self.entered.set()
            self.release.wait(5)
        port = self.ports[min(number, 3) - 1]
        item = dict(client_id=f"runtime-{number}", host="127.0.0.1", http_port=port,
                    socks_port=port, auth_token=TOKEN)
        self.created.append(item)
        return dict(item)

    def remove_proxy(self, client_id):
        self.removed.append(client_id)
        return {"ok": True}


class RuntimeFixture:
    def __init__(self):
        self.servers, self.handlers, self.posts = [], set(), []
        self.drop_post = False
        self.tmp = tempfile.TemporaryDirectory()
        self.manager = None
        self.writers = []

    async def _tracked(self, fn, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        try:
            await fn(reader, writer)
        except (OSError, asyncio.IncompleteReadError):
            pass
        finally:
            await close_writer(writer)
            self.handlers.discard(task)

    async def _target(self, reader, writer):
        first = await reader.readexactly(1)
        if first in (b"G", b"P"):
            head = first + await reader.readuntil(b"\r\n\r\n")
            length = next((int(line.split(b":", 1)[1]) for line in head.split(b"\r\n")
                           if line.lower().startswith(b"content-length:")), 0)
            body = await reader.readexactly(length)
            if head.startswith(b"POST "):
                self.posts.append(body)
                body = b"OK"
                if self.drop_post:
                    return
            else:
                body = b"127.0.0.1"
            writer.write(b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: " +
                         str(len(body)).encode() + b"\r\n\r\n" + body)
            await writer.drain()
        else:
            writer.write(first)
            await writer.drain()
            while data := await reader.read(4096):
                writer.write(data)
                await writer.drain()

    @staticmethod
    async def _bridge(local_r, local_w, remote_r, remote_w):
        async def pipe(reader, writer):
            while data := await reader.read(65536):
                writer.write(data)
                await writer.drain()
        tasks = [asyncio.create_task(pipe(local_r, remote_w)),
                 asyncio.create_task(pipe(remote_r, local_w))]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _upstream(self, reader, writer):
        remote_w = None
        try:
            first = await reader.readexactly(1)
            head = None
            if first == b"\x05":
                count = (await reader.readexactly(1))[0]
                assert b"\x02" in await reader.readexactly(count)
                writer.write(b"\x05\x02")
                await writer.drain()
                version, length = await reader.readexactly(2)
                user = await reader.readexactly(length)
                length = (await reader.readexactly(1))[0]
                password = await reader.readexactly(length)
                assert (version, user, password) == (1, TOKEN.encode(), b"none")
                writer.write(b"\x01\x00")
                await writer.drain()
                _, _, _, atyp = await reader.readexactly(4)
                host = await read_address(reader, atyp)
                port = int.from_bytes(await reader.readexactly(2), "big")
            else:
                head = first + await reader.readuntil(b"\r\n\r\n")
                method, target, _ = head.split(b"\r\n", 1)[0].split()
                expected = b"Proxy-Authorization: Basic " + base64.b64encode((TOKEN + ":none").encode())
                assert expected in head
                parsed = urlsplit((b"//" + target if method == b"CONNECT" else target).decode())
                host, port = parsed.hostname, parsed.port
            # Fixtures only forward to their own loopback target, not arbitrary hosts.
            assert host == "127.0.0.1" and port == self.target_port
            remote_r, remote_w = await asyncio.open_connection(host, port)
            if first == b"\x05":
                writer.write(b"\x05\x00\x00\x01" + b"\0" * 6)
            elif head.startswith(b"CONNECT "):
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            else:
                remote_w.write(head)
                await remote_w.drain()
            await writer.drain()
            await self._bridge(reader, writer, remote_r, remote_w)
        finally:
            await close_writer(remote_w)

    async def probe(self, upstream):
        results = {}
        for protocol in ("http", "socks"):
            writer = None
            try:
                reader, writer = await open_tunnel(upstream, "127.0.0.1", self.target_port,
                                                  protocol=protocol, timeout=.3)
                writer.write(b"GET /ip HTTP/1.1\r\nHost: fixture\r\n\r\n")
                await writer.drain()
                response = await asyncio.wait_for(reader.read(), .5)
                results[protocol] = response.endswith(b"127.0.0.1")
            except OSError:
                results[protocol] = False
            finally:
                await close_writer(writer)
        return dict(ok=all(results.values()), http_ok=results["http"], socks_ok=results["socks"],
                    exit_ip="127.0.0.1" if all(results.values()) else "", latency_ms=1,
                    country_code="US", country_codes={"http": "US", "socks": "US"},
                    country_source="loopback-fixture", city="Fixture City", region="Fixture Region")

    async def start(self):
        target = await asyncio.start_server(lambda r, w: self._tracked(self._target, r, w), "127.0.0.1", 0)
        self.servers.append(target)
        self.target_port = target.sockets[0].getsockname()[1]
        self.nodes = []
        for _ in range(3):
            node = await asyncio.start_server(lambda r, w: self._tracked(self._upstream, r, w), "127.0.0.1", 0)
            self.nodes.append(node)
            self.servers.append(node)
        self.account = FixtureAccount([node.sockets[0].getsockname()[1] for node in self.nodes])
        reservation = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
        port = reservation.sockets[0].getsockname()[1]
        reservation.close()
        await reservation.wait_closed()
        self.manager = GatewayManager(self.account, Path(self.tmp.name) / "gateways.json", probe=self.probe)
        await self.manager.initialize()
        row = await self.manager.create_gateway(dict(name="Runtime fixture", port=port, enabled=True,
            mode="city", country_code="US", location_id="exact-fixture-city", city="Requested City",
            residential_only=False))
        self.runtime = self.manager._gateways[row["id"]]
        self.port = port
        for _ in range(200):
            if len(self.runtime.members) == 3:
                break
            await asyncio.sleep(.01)
        assert len(self.runtime.members) == 3
        assert (await self.manager.list_gateways())[0]["pool_ready"] == 3

    async def connect(self, *, tls=False, socks=False):
        context = ssl._create_unverified_context() if tls else None  # loopback certificate only
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port, ssl=context)
        self.writers.append(writer)
        if socks:
            writer.write(b"\x05\x01\x00")
            await writer.drain()
            assert await reader.readexactly(2) == b"\x05\x00"
            writer.write(b"\x05\x01\x00" + encode_address("127.0.0.1") + self.target_port.to_bytes(2, "big"))
            await writer.drain()
            assert (await reader.readexactly(10))[:2] == b"\x05\x00"
        else:
            authority = f"127.0.0.1:{self.target_port}".encode()
            writer.write(b"CONNECT " + authority + b" HTTP/1.1\r\nHost: " + authority + b"\r\n\r\n")
            await writer.drain()
            assert b"200" in await reader.readuntil(b"\r\n\r\n")
        return reader, writer

    async def post(self, body):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        self.writers.append(writer)
        writer.write(f"POST http://127.0.0.1:{self.target_port}/upload HTTP/1.1\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body)
        await writer.drain()
        response = await asyncio.wait_for(reader.read(), 2)
        await close_writer(writer)
        return response

    @staticmethod
    async def echo(reader, writer):
        writer.write(b"ping")
        await writer.drain()
        assert await asyncio.wait_for(reader.readexactly(4), 1) == b"ping"

    async def run(self):
        await self.start()
        # A disappears; HTTP POST uses B without waiting for a replacement create.
        self.nodes[0].close()
        await self.nodes[0].wait_closed()
        started = asyncio.get_running_loop().time()
        assert b"200 OK" in await self.post(b"first-post")
        assert self.runtime.upstream["client_id"] == "runtime-2"
        first_switch = asyncio.get_running_loop().time() - started
        keep_r, keep_w = await asyncio.wait_for(self.connect(), 2)
        await self.echo(keep_r, keep_w)
        # B stops accepting; C is ready. Existing B stream is still healthy.
        self.nodes[1].close()
        # Python 3.13 waits for live streams too: intentionally keep B tunnel open.
        started = asyncio.get_running_loop().time()
        reader, writer = await asyncio.wait_for(self.connect(), 2)
        await self.echo(reader, writer)
        second_switch = asyncio.get_running_loop().time() - started
        assert self.runtime.upstream["client_id"] == "runtime-3"
        await self.echo(keep_r, keep_w)
        for options in ({"tls": True}, {"socks": True}):
            reader, writer = await asyncio.wait_for(self.connect(**options), 2)
            await self.echo(reader, writer)
        # Business bytes were committed: a dropped POST is not replayed.
        self.drop_post = True
        await self.post(b"committed-post")
        assert self.posts == [b"first-post", b"committed-post"]
        for _ in range(100):
            if self.account.entered.is_set():
                break
            await asyncio.sleep(.01)
        assert self.account.entered.is_set() and len(self.account.created) == 3
        assert first_switch < 1 and second_switch < 1
        row = (await self.manager.list_gateways())[0]
        assert row["status"] == "running" and row["active_client_id"] == "runtime-3"
        assert TOKEN not in json.dumps(row)
        return {"verdict": "PASS", "protocols": ["HTTP POST", "CONNECT", "HTTPS/TLS CONNECT", "SOCKS5"],
                "ready_before": 3, "active_after_two_failures": row["active_client_id"],
                "fallback_seconds": [round(first_switch, 4), round(second_switch, 4)],
                "slow_create_not_waited": True, "healthy_tunnel_preserved": True, "post_replayed": False}

    async def close(self):
        for writer in self.writers:
            await close_writer(writer)
        if hasattr(self, "account"):
            self.account.release.set()
        if self.manager:
            await self.manager.close()
            assert not self.manager.has_owned_clients
            assert set(self.account.removed) == {item["client_id"] for item in self.account.created}
        for server in self.servers:
            server.close()
            await server.wait_closed()
        for task in list(self.handlers):
            task.cancel()
        await asyncio.gather(*list(self.handlers), return_exceptions=True)
        self.tmp.cleanup()


async def main():
    fixture = RuntimeFixture()
    try:
        result = await asyncio.wait_for(fixture.run(), 15)
    finally:
        await fixture.close()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
