import asyncio
import unittest
from core import checker
from core.multiplexer import MixedProxyServer
from core.protocol import close_writer
from tests.fixtures import FakeUpstream


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.remote = await FakeUpstream().start()
        self.active = self.remote.upstream
        self.relay = MixedProxyServer("127.0.0.1", 0, lambda: self.active, timeout=.5, idle_timeout=1)
        await self.relay.start()

    async def asyncTearDown(self):
        await self.relay.close()
        await self.remote.close()

    async def connect(self):
        return await asyncio.open_connection("127.0.0.1", self.relay.port)

    async def request(self, request):
        reader, writer = await self.connect()
        try:
            writer.write(request)
            await writer.drain()
            return await asyncio.wait_for(reader.read(), 3)
        finally:
            await close_writer(writer)

    async def test_http_forward_strips_client_credentials(self):
        result = await self.request(b"GET http://nonexistent.invalid/path HTTP/1.1\r\nHost: wrong\r\nProxy-Authorization: Basic local-secret\r\n\r\n")
        self.assertIn(b"200 OK", result)
        self.assertTrue(result.endswith(b"203.0.113.10"))
        head = self.remote.requests[-1][1]
        self.assertNotIn("local-secret", head)
        self.assertIn("Host: nonexistent.invalid", head)

    async def test_connect_same_port_echo(self):
        reader, writer = await self.connect()
        try:
            writer.write(b"CONNECT nonexistent.invalid:443 HTTP/1.1\r\nHost: nonexistent.invalid\r\n\r\nping")
            await writer.drain()
            self.assertIn(b"200", await reader.readuntil(b"\r\n\r\n"))
            self.assertEqual(await reader.readexactly(4), b"ping")
        finally:
            await close_writer(writer)

    async def test_socks_same_port_fragmented_domain_dns_remote(self):
        reader, writer = await self.connect()
        try:
            for fragment in (b"\x05", b"\x01", b"\x00"):
                writer.write(fragment)
                await writer.drain()
            self.assertEqual(await reader.readexactly(2), b"\x05\x00")
            domain = b"nonexistent.invalid"
            writer.write(b"\x05\x01\x00\x03" + bytes([len(domain)]) + domain + b"\x01\xbb")
            await writer.drain()
            self.assertEqual((await reader.readexactly(10))[:2], b"\x05\x00")
            writer.write(b"hello")
            await writer.drain()
            self.assertEqual(await reader.readexactly(5), b"hello")
            self.assertIn(("socks", "nonexistent.invalid", 443), self.remote.requests)
        finally:
            await close_writer(writer)

    async def test_socks_udp_rejected(self):
        reader, writer = await self.connect()
        try:
            writer.write(b"\x05\x01\x00")
            await writer.drain()
            await reader.readexactly(2)
            writer.write(b"\x05\x03\x00\x01" + b"\0" * 6)
            await writer.drain()
            self.assertEqual((await reader.readexactly(10))[1], 7)
        finally:
            await close_writer(writer)

    async def test_post_and_chunked_upload(self):
        for headers, body in ((b"Content-Length: 5", b"hello"),
                               (b"Transfer-Encoding: chunked", b"5\r\nhello\r\n0\r\n\r\n")):
            result = await self.request(b"POST http://test.invalid/ HTTP/1.1\r\nHost: test.invalid\r\n" + headers + b"\r\n\r\n" + body)
            self.assertTrue(result.endswith(b"hello"))

    async def test_ambiguous_framing_rejected(self):
        result = await self.request(b"POST http://test.invalid/ HTTP/1.1\r\nContent-Length: 5\r\nTransfer-Encoding: chunked\r\n\r\n")
        self.assertIn(b"400", result)

    async def test_no_upstream_fails_closed(self):
        self.active = None
        self.assertIn(b"502", await self.request(b"CONNECT test.invalid:443 HTTP/1.1\r\n\r\n"))

    async def test_switch_changes_new_connections_only(self):
        other = await FakeUpstream("203.0.113.20").start()
        try:
            request = b"GET http://test.invalid/ HTTP/1.1\r\n\r\n"
            self.assertTrue((await self.request(request)).endswith(b".10"))
            self.active = other.upstream
            self.assertTrue((await self.request(request)).endswith(b".20"))
        finally:
            await other.close()

    async def test_optional_local_auth(self):
        self.relay.username, self.relay.password = "local", "secret"
        self.assertIn(b"407", await self.request(b"GET http://test.invalid/ HTTP/1.1\r\n\r\n"))

    async def test_checker_consumes_variable_reply(self):
        for fn, key in ((checker.test_socks5_proxy, "socks_port"), (checker.test_http_proxy, "http_port")):
            result = await asyncio.to_thread(fn, "127.0.0.1", self.remote.upstream[key],
                "fixture-token", target_host="test.invalid", timeout=2, target_port=80, use_tls=False)
            self.assertTrue(result[0], result)
            self.assertEqual(result[2], "203.0.113.10")

    async def test_checker_rejects_wrong_auth(self):
        result = await asyncio.to_thread(checker.test_socks5_proxy, "127.0.0.1",
            self.remote.upstream["socks_port"], "wrong-token", timeout=1, target_port=80, use_tls=False)
        self.assertFalse(result[0])
