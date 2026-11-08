"""Real socket failover and no-replay checks for the mixed relay."""
import asyncio
from pathlib import Path
import ssl
import tempfile
import unittest

from core.multiplexer import MixedProxyServer
from core.protocol import close_writer, read_address


class RelayFailoverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.records, self.feedback, self.servers = [], [], []
        self.handlers = set()
        self.reject_destination = False
        self.close_after_post = False
        self.delay_connect = False
        self.connect_entered, self.connect_release = asyncio.Event(), asyncio.Event()

        async def upstream(reader, writer):
            task = asyncio.current_task()
            self.handlers.add(task)
            try:
                first = await reader.readexactly(1)
                if first == b"\x05":
                    count = (await reader.readexactly(1))[0]
                    await reader.readexactly(count)
                    writer.write(b"\x05\x02")
                    await writer.drain()
                    version, length = await reader.readexactly(2)
                    await reader.readexactly(length)
                    length = (await reader.readexactly(1))[0]
                    await reader.readexactly(length)
                    writer.write(b"\x01\x00")
                    await writer.drain()
                    _, _, _, atyp = await reader.readexactly(4)
                    await read_address(reader, atyp)
                    await reader.readexactly(2)
                    if self.reject_destination:
                        writer.write(b"\x05\x04\x00\x01" + b"\0" * 6)
                        await writer.drain()
                        return
                    writer.write(b"\x05\x00\x00\x01" + b"\0" * 6)
                    await writer.drain()
                else:
                    head = first + await reader.readuntil(b"\r\n\r\n")
                    if head.startswith(b"CONNECT "):
                        if self.reject_destination:
                            writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
                            await writer.drain()
                            return
                        if self.delay_connect:
                            self.connect_entered.set()
                            await self.connect_release.wait()
                        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                        await writer.drain()
                    else:
                        length = next((int(l.split(b":", 1)[1]) for l in head.split(b"\r\n")
                                       if l.lower().startswith(b"content-length:")), 0)
                        body = await reader.readexactly(length)
                        self.records.append((head, body))
                        if not self.close_after_post:
                            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK")
                            await writer.drain()
                        return
                while data := await reader.read(4096):
                    writer.write(data)
                    await writer.drain()
            except (OSError, asyncio.IncompleteReadError):
                pass
            finally:
                await close_writer(writer)
                self.handlers.discard(task)

        self.good = await asyncio.start_server(upstream, "127.0.0.1", 0)
        self.servers.append(self.good)
        good_port = self.good.sockets[0].getsockname()[1]
        dead = await asyncio.start_server(upstream, "127.0.0.1", 0)
        dead_port = dead.sockets[0].getsockname()[1]
        dead.close()
        await dead.wait_closed()
        self.candidates = [{"client_id": cid, "host": "127.0.0.1", "http_port": port,
                            "socks_port": port, "auth_token": "test-fake-key-relay", "_generation": 3}
                           for cid, port in (("dead", dead_port), ("good", good_port))]
        self.relay = MixedProxyServer("127.0.0.1", 0, lambda: self.candidates[0],
            get_candidates=lambda: self.candidates,
            on_upstream_result=lambda *args: self.feedback.append(args),
            timeout=2, connect_timeout=.3, tls_config={"state_dir": Path(self.tmp.name)})
        await self.relay.start()

    async def asyncTearDown(self):
        self.connect_release.set()
        self.relay.drain_active_connections()
        await self.relay.close()
        for server in self.servers:
            server.close()
        for task in list(self.handlers):
            task.cancel()
        await asyncio.gather(*list(self.handlers), return_exceptions=True)
        for server in self.servers:
            await server.wait_closed()
        self.tmp.cleanup()

    async def http(self, payload, tls=False):
        context = ssl._create_unverified_context() if tls else None  # local fixture certificate only
        reader, writer = await asyncio.open_connection("127.0.0.1", self.relay.port, ssl=context)
        writer.write(payload)
        await writer.drain()
        return reader, writer

    async def test_plain_http_post_uses_backup_once(self):
        reader, writer = await self.http(b"POST http://target.fixture/upload HTTP/1.1\r\nHost: target.fixture\r\nContent-Length: 4\r\n\r\nDATA")
        response = await asyncio.wait_for(reader.read(), 2)
        self.assertIn(b"200 OK", response)
        self.assertEqual([r[1] for r in self.records], [b"DATA"])
        self.assertTrue(any(x[0] == "dead" and x[2] == "dial" for x in self.feedback))
        await close_writer(writer)

    async def test_connect_and_tls_connect_use_backup_and_echo(self):
        for tls in (False, True):
            reader, writer = await self.http(b"CONNECT target.fixture:443 HTTP/1.1\r\nHost: target.fixture:443\r\n\r\n", tls)
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2)
            self.assertIn(b"200", head)
            writer.write(b"ping")
            await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.readexactly(4), 2), b"ping")
            await close_writer(writer)

    async def test_socks_uses_backup(self):
        reader, writer = await self.http(b"\x05\x01\x00")
        self.assertEqual(await reader.readexactly(2), b"\x05\x00")
        writer.write(b"\x05\x01\x00\x03\x0etarget.fixture\x01\xbb")
        await writer.drain()
        self.assertEqual((await reader.readexactly(10))[:2], b"\x05\x00")
        writer.write(b"ping")
        await writer.drain()
        self.assertEqual(await reader.readexactly(4), b"ping")
        await close_writer(writer)

    async def test_committed_post_is_not_replayed_after_upstream_disconnect(self):
        self.candidates = [self.candidates[1], self.candidates[1]]
        self.close_after_post = True
        reader, writer = await self.http(b"POST http://target.fixture/upload HTTP/1.1\r\nContent-Length: 4\r\n\r\nDATA")
        await asyncio.wait_for(reader.read(), 2)
        self.assertEqual(len(self.records), 1)
        self.assertEqual(self.records[0][1], b"DATA")
        await close_writer(writer)

    async def test_destination_rejection_is_not_reported_as_dial_failure(self):
        self.candidates = [self.candidates[1]]
        self.reject_destination = True
        reader, writer = await self.http(b"CONNECT target.fixture:443 HTTP/1.1\r\nHost: target.fixture:443\r\n\r\n")
        self.assertIn(b"502", await asyncio.wait_for(reader.read(), 2))
        self.assertTrue(any(x[2] == "destination" for x in self.feedback))
        self.assertFalse(any(x[2] == "dial" for x in self.feedback))
        await close_writer(writer)

    async def test_targeted_retirement_cancels_pending_handshake_not_other_tunnel(self):
        good = self.candidates[1]
        self.candidates = [dict(good, client_id="healthy")]
        keep_r, keep_w = await self.http(b"CONNECT target.fixture:443 HTTP/1.1\r\nHost: target.fixture:443\r\n\r\n")
        await keep_r.readuntil(b"\r\n\r\n")
        self.candidates = [dict(good, client_id="retired")]
        self.delay_connect = True
        reader, writer = await self.http(b"CONNECT target.fixture:443 HTTP/1.1\r\nHost: target.fixture:443\r\n\r\n")
        await asyncio.wait_for(self.connect_entered.wait(), 1)
        self.relay.drain_active_connections("retired")
        await asyncio.sleep(0)
        self.connect_release.set()
        response = await asyncio.wait_for(reader.read(4096), 1)
        self.assertNotIn(b"200", response)
        keep_w.write(b"ping")
        await keep_w.drain()
        self.assertEqual(await asyncio.wait_for(keep_r.readexactly(4), 1), b"ping")
        await close_writer(writer)
        await close_writer(keep_w)

    async def test_config_generation_change_rejects_late_success_before_business_bytes(self):
        self.candidates = [self.candidates[1]]
        self.delay_connect = True
        reader, writer = await self.http(b"CONNECT target.fixture:443 HTTP/1.1\r\nHost: target.fixture:443\r\n\r\n")
        await asyncio.wait_for(self.connect_entered.wait(), 1)
        self.candidates = [dict(self.candidates[0], _generation=4)]
        self.connect_release.set()
        response = await asyncio.wait_for(reader.read(4096), 1)
        self.assertNotIn(b"200", response)
        self.assertIn(b"502", response)
        await close_writer(writer)

    async def test_normal_switch_does_not_cancel_healthy_tunnel(self):
        self.candidates = [self.candidates[1]]
        reader, writer = await self.http(b"CONNECT target.fixture:443 HTTP/1.1\r\nHost: target.fixture:443\r\n\r\n")
        await reader.readuntil(b"\r\n\r\n")
        self.relay.drain_active_connections("other-client")
        writer.write(b"ping")
        await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.readexactly(4), 2), b"ping")
        await asyncio.wait_for(self.relay.close(), 1)
        self.assertEqual(await asyncio.wait_for(reader.read(), 1), b"")
        self.assertEqual(self.relay.connections, 0)
        self.assertFalse(self.relay.task_upstreams)
        await close_writer(writer)
