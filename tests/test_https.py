"""Real TLS proxy regressions: trust, framing, nested TLS and lifecycle."""
import asyncio
import os
from pathlib import Path
import ssl
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from core import checker
from core.config import ConfigError, validate_config
from core.multiplexer import MixedProxyServer
from core.protocol import ProxyError, close_writer, open_tunnel
from core.tls_helper import generate_self_signed_cert, get_ssl_context
from core.web_server import ManagementApp
from tests.fixtures import FakeUpstream
from tests.test_gateway import FakeClient, GOOD
from tests.test_integration import free_port


class HTTPSProxyTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.context = get_ssl_context(state_dir=cls.root)
        cls.cert = cls.root / 'gateway_tls.crt'
        cls.client_context = ssl.create_default_context(cafile=str(cls.cert))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    async def asyncSetUp(self):
        self.remote = await FakeUpstream().start()
        self.relay = MixedProxyServer('127.0.0.1', 0, lambda: self.remote.upstream,
                                      ssl_context=self.context, timeout=1, idle_timeout=2)
        await self.relay.start()

    async def asyncTearDown(self):
        await self.relay.close()
        await self.remote.close()

    async def tls_request(self, request, port=None, context=None):
        reader, writer = await asyncio.wait_for(asyncio.open_connection(
            '127.0.0.1', port or self.relay.port, ssl=context or self.client_context), 3)
        try:
            writer.write(request)
            await writer.drain()
            return await asyncio.wait_for(reader.read(), 5)
        finally:
            await close_writer(writer)

    async def test_https_forward_auth_and_large_upload(self):
        self.relay.username, self.relay.password = 'local', 'secret'
        denied = await self.tls_request(b'GET http://fixture.invalid/ HTTP/1.1\r\n\r\n')
        self.assertIn(b'407', denied)
        self.assertEqual(self.remote.requests, [])
        body = b'upload-data-' * 25000
        response = await self.tls_request(
            b'POST http://fixture.invalid/ HTTP/1.1\r\nProxy-Authorization: Basic bG9jYWw6c2VjcmV0\r\n'
            + f'Content-Length: {len(body)}\r\n\r\n'.encode() + body)
        self.assertEqual(response.partition(b'\r\n\r\n')[2], body)
        self.assertNotIn('bG9jYWw6c2VjcmV0', self.remote.requests[-1][1])

    async def test_untrusted_certificate_rejected(self):
        with self.assertRaises(ssl.SSLCertVerificationError):
            await self.tls_request(b'GET http://fixture.invalid/ HTTP/1.1\r\n\r\n',
                                   context=ssl.create_default_context())

    async def test_tls_close_delimited_response_finishes_without_idle_timeout(self):
        async def close_delimited(reader, writer):
            try:
                await reader.readuntil(b'\r\n\r\n')
                writer.write(b'HTTP/1.1 200 OK\r\nConnection: close\r\n\r\nclose-delimited')
                await writer.drain()
            finally:
                await close_writer(writer)
        server = await asyncio.start_server(close_delimited, '127.0.0.1', 0)
        self.remote.servers.append(server)
        self.remote.upstream['http_port'] = server.sockets[0].getsockname()[1]
        response = await asyncio.wait_for(self.tls_request(
            b'GET http://fixture.invalid/ HTTP/1.1\r\n\r\n'), .75)
        self.assertTrue(response.endswith(b'close-delimited'))

    async def test_fragmented_clienthello(self):
        tasks = set()
        async def bridge(reader, writer):
            task = asyncio.current_task()
            tasks.add(task)
            remote_writer = None
            try:
                remote_reader, remote_writer = await asyncio.open_connection('127.0.0.1', self.relay.port)
                initial = await reader.read(65536)
                for i in range(0, len(initial), 7):
                    remote_writer.write(initial[i:i+7])
                    await remote_writer.drain()
                    await asyncio.sleep(.001)
                await self.relay._duplex(reader, writer, remote_reader, remote_writer)
            except (OSError, asyncio.CancelledError):
                pass
            finally:
                await close_writer(remote_writer)
                await close_writer(writer)
                tasks.discard(task)
        server = await asyncio.start_server(bridge, '127.0.0.1', 0)
        try:
            response = await self.tls_request(b'GET http://fixture.invalid/ HTTP/1.1\r\n\r\n',
                                             port=server.sockets[0].getsockname()[1])
            self.assertTrue(response.endswith(b'203.0.113.10'))
        finally:
            server.close()
            await server.wait_closed()
            remaining = list(tasks)
            for task in remaining:
                task.cancel()
            await asyncio.gather(*remaining, return_exceptions=True)

    async def test_tls_connect_propagates_upstream_eof(self):
        async def tunnel(reader, writer):
            await reader.readuntil(b'\r\n\r\n')
            writer.write(b'HTTP/1.1 200 OK\r\nConnection: close\r\n\r\nclosed')
            await writer.drain()
        self.remote.tunnel = tunnel
        reader, writer = await asyncio.open_connection('127.0.0.1', self.relay.port,
                                                       ssl=self.client_context)
        try:
            writer.write(b'CONNECT fixture.invalid:80 HTTP/1.1\r\n\r\n')
            await writer.drain()
            self.assertIn(b'200', await reader.readuntil(b'\r\n\r\n'))
            writer.write(b'GET / HTTP/1.1\r\n\r\n')
            await writer.drain()
            response = await asyncio.wait_for(reader.read(), .75)
            self.assertTrue(response.endswith(b'closed'))
        finally:
            await close_writer(writer)

    async def test_truncated_tls_and_shutdown_release_connections(self):
        _, writer = await asyncio.open_connection('127.0.0.1', self.relay.port)
        writer.write(b'\x16\x03')
        await writer.drain()
        await asyncio.sleep(.02)
        await self.relay.close()
        self.assertEqual(self.relay.connections, 0)
        self.assertFalse(self.relay.tasks)
        await close_writer(writer)

    async def test_management_certificate_configuration_is_used(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = ManagementApp({'tls_cert_file': str(self.cert),
                                 'tls_key_file': str(self.root / 'gateway_tls.key')}, root,
                                client=FakeClient(), config_path=root / 'config.json')
            app.manager._probe = AsyncMock(return_value=GOOD)
            await asyncio.to_thread(app.start)
            try:
                row = await asyncio.to_thread(app.call, app.manager.create_gateway(dict(
                    host='127.0.0.1', port=free_port(), enabled=True, auto_heal=False,
                    username='local', password='secret')))
                self.assertEqual(row['status'], 'running')
                response = await self.tls_request(b'GET http://fixture.invalid/ HTTP/1.1\r\n\r\n', port=row['port'])
                self.assertIn(b'407', response)
                self.assertFalse((root / 'gateway_tls.crt').exists())
            finally:
                await asyncio.to_thread(app.close)

    async def test_bad_certificate_is_not_reported_as_port_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = FakeClient()
            app = ManagementApp({'tls_cert_file': str(root / 'missing.crt'),
                                 'tls_key_file': str(root / 'missing.key')}, root,
                                client=client, config_path=root / 'config.json')
            await asyncio.to_thread(app.start)
            try:
                row = await asyncio.to_thread(app.call, app.manager.create_gateway(dict(
                    host='127.0.0.1', port=free_port(), enabled=True, auto_heal=False)))
                self.assertEqual(row['status'], 'error')
                self.assertEqual(row['last_error'], 'local_tls_failed')
                self.assertEqual(client.created, [])
            finally:
                await asyncio.to_thread(app.close)

    async def test_https_upstream_connect_and_no_plaintext_fallback(self):
        server = await asyncio.start_server(self.remote.http, '127.0.0.1', 0, ssl=self.context)
        self.remote.servers.append(server)
        self.remote.upstream['https_port'] = server.sockets[0].getsockname()[1]
        with patch.dict(os.environ, SSL_CERT_FILE=str(self.cert)):
            response = await self.tls_request(b'GET http://fixture.invalid/ HTTP/1.1\r\n\r\n')
            self.assertIn(b'200 OK', response)
            reader, writer = await open_tunnel(self.remote.upstream, 'fixture.invalid', 443, protocol='https')
            try:
                writer.write(b'ping')
                await writer.drain()
                self.assertEqual(await asyncio.wait_for(reader.readexactly(4), 2), b'ping')
            finally:
                await close_writer(writer)
        upstream = dict(self.remote.upstream)
        upstream.pop('https_port')
        with self.assertRaises(ProxyError):
            await open_tunnel(upstream, 'fixture.invalid', 443, protocol='https')

    async def test_plain_protocol_half_close_with_https_upstream_available(self):
        async def eof_reply(reader, writer):
            body = await reader.read()
            writer.write(b'reply:' + body)
            await writer.drain()
        self.remote.tunnel = eof_reply
        server = await asyncio.start_server(self.remote.http, '127.0.0.1', 0, ssl=self.context)
        self.remote.servers.append(server)
        self.remote.upstream['https_port'] = server.sockets[0].getsockname()[1]
        with patch.dict(os.environ, SSL_CERT_FILE=str(self.cert)):
            for protocol in ('http', 'socks'):
                with self.subTest(protocol=protocol):
                    reader, writer = await asyncio.open_connection('127.0.0.1', self.relay.port)
                    try:
                        if protocol == 'http':
                            writer.write(b'CONNECT fixture.invalid:80 HTTP/1.1\r\n\r\n')
                            await writer.drain()
                            self.assertIn(b'200', await reader.readuntil(b'\r\n\r\n'))
                        else:
                            writer.write(b'\x05\x01\x00')
                            await writer.drain()
                            self.assertEqual(await reader.readexactly(2), b'\x05\x00')
                            host = b'fixture.invalid'
                            writer.write(b'\x05\x01\x00\x03' + bytes([len(host)]) + host + b'\x00\x50')
                            await writer.drain()
                            self.assertEqual((await reader.readexactly(10))[:2], b'\x05\x00')
                        writer.write(b'upload')
                        await writer.drain()
                        writer.write_eof()
                        self.assertEqual(await asyncio.wait_for(reader.read(), .75), b'reply:upload')
                    finally:
                        await close_writer(writer)

    async def test_rebind_bad_certificate_reports_tls_and_preserves_old_listener(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cert, key = root / 'cert.pem', root / 'key.pem'
            cert.write_bytes(self.cert.read_bytes())
            key.write_bytes((self.root / 'gateway_tls.key').read_bytes())
            key.chmod(0o600)
            app = ManagementApp({'tls_cert_file': str(cert), 'tls_key_file': str(key)}, root,
                                client=FakeClient(), config_path=root / 'config.json')
            app.manager._probe = AsyncMock(return_value=GOOD)
            await asyncio.to_thread(app.start)
            try:
                row = await asyncio.to_thread(app.call, app.manager.create_gateway(dict(
                    port=free_port(), enabled=True, username='local', password='secret')))
                self.assertEqual(row['status'], 'running')
                cert.unlink()
                changed = await asyncio.to_thread(app.call, app.manager.update_gateway(row['id'], {'port': free_port()}))
                self.assertEqual(changed['last_error'], 'local_tls_failed')
                self.assertEqual(changed['port'], row['port'])
                self.assertEqual(changed['status'], 'running')
                response = await self.tls_request(b'GET http://fixture.invalid/ HTTP/1.1\r\n\r\n', port=row['port'])
                self.assertIn(b'407', response)
            finally:
                await asyncio.to_thread(app.close)

    async def test_checker_https_target_over_https_proxy(self):
        async def origin(reader, writer):
            try:
                await reader.readuntil(b'\r\n\r\n')
                writer.write(b'HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 12\r\n\r\n203.0.113.10')
                await writer.drain()
            finally:
                await close_writer(writer)
        server = await asyncio.start_server(origin, '127.0.0.1', 0, ssl=self.context)
        port = server.sockets[0].getsockname()[1]
        async def tunnel(reader, writer):
            target_r, target_w = await asyncio.open_connection('127.0.0.1', port)
            try:
                await self.relay._duplex(reader, writer, target_r, target_w)
            finally:
                await close_writer(target_w)
        self.remote.tunnel = tunnel
        self.relay.username, self.relay.password = 'local', 'secret'
        try:
            with patch.dict(os.environ, SSL_CERT_FILE=str(self.cert)):
                result = await asyncio.to_thread(checker.test_https_proxy, '127.0.0.1', self.relay.port,
                                                'local', 'secret', 'localhost', 3,
                                                target_port=port, use_tls=True)
            self.assertTrue(result[0], result)
            self.assertEqual(result[2], '203.0.113.10')
        finally:
            server.close()
            await server.wait_closed()


class CertificateTests(unittest.TestCase):
    def test_explicit_paths_are_validated_and_cache_is_per_instance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = get_ssl_context(state_dir=root / 'one')
            second = get_ssl_context(state_dir=root / 'two')
            self.assertIsNot(first, second)
            self.assertIs(first, get_ssl_context(state_dir=root / 'one'))
            self.assertEqual((root / 'one/gateway_tls.key').stat().st_mode & 0o777, 0o600)
            with self.assertRaises(OSError):
                get_ssl_context(cert_file=str(root / 'missing.crt'), key_file=str(root / 'missing.key'))
            with self.assertRaises(ConfigError):
                validate_config({'tls_cert_file': '/missing.crt'})
            cert, key = root / 'explicit.crt', root / 'explicit.key'
            generate_self_signed_cert(cert, key)
            explicit = get_ssl_context(cert_file=str(cert), key_file=str(key))
            self.assertIsNot(first, explicit)
            self.assertIs(explicit, get_ssl_context(cert_file=str(cert), key_file=str(key)))
