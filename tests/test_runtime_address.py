import contextlib
import io
import errno
import socket
import tempfile
import unittest
from unittest.mock import Mock, patch

from core.runtime_address import detect_runtime_ips, runtime_info
from core.web_server import ManagementApp, ManagementServer, serve
from tests.test_web import FakeManager


class RuntimeAddressTests(unittest.TestCase):
    def test_cli_reports_bind_failure_without_traceback_or_credentials(self):
        import main
        config = {"web_password": "private-local-password"}
        errors = io.StringIO()
        with patch("main.load_config", return_value=config), \
                patch("sys.argv", ["main.py", "--web"]), \
                patch("core.web_server.ManagementServer", side_effect=OSError(errno.EADDRINUSE, "port busy")), \
                patch("core.web_server.ProcessLock") as lock, \
                contextlib.redirect_stderr(errors):
            with self.assertRaises(SystemExit) as result:
                main.main()
        self.assertNotEqual(result.exception.code, 0)
        self.assertIn("监听失败", errors.getvalue())
        self.assertIn("--web-port", errors.getvalue())
        self.assertNotIn("private-local-password", errors.getvalue())
        self.assertNotIn("Traceback", errors.getvalue())
        lock.return_value.close.assert_called_once()

    def test_dual_stack_option_failure_is_not_silently_ignored(self):
        server = ManagementServer.__new__(ManagementServer)
        server.address_family = socket.AF_INET6
        server.is_dual_stack = True
        server.socket = Mock()
        server.socket.setsockopt.side_effect = OSError(errno.ENOPROTOOPT, "IPv6 option unsupported")
        with patch("http.server.ThreadingHTTPServer.server_bind") as bind:
            with self.assertRaises(OSError):
                server.server_bind()
        bind.assert_not_called()

    def test_ipv6_only_socket_triggers_ipv4_fallback(self):
        server = ManagementServer.__new__(ManagementServer)
        server.address_family = socket.AF_INET6
        server.is_dual_stack = True
        server.socket = Mock()
        server.socket.getsockopt.return_value = 1
        with patch("http.server.ThreadingHTTPServer.server_bind") as bind:
            with self.assertRaises(OSError):
                server.server_bind()
        bind.assert_not_called()

    def test_ipv6_unavailable_falls_back_to_real_ipv4_listener(self):
        with tempfile.TemporaryDirectory() as directory:
            app = ManagementApp({}, directory, manager=FakeManager(), client=Mock())
            original_bind = ManagementServer.server_bind
            def bind(server):
                if server.address_family == socket.AF_INET6:
                    raise OSError(errno.EAFNOSUPPORT, "IPv6 disabled")
                original_bind(server)
            try:
                with patch.object(ManagementServer, "server_bind", bind):
                    server = ManagementServer(("::", 0), app)
                try:
                    self.assertEqual(server.address_family, socket.AF_INET)
                    self.assertEqual(app.web_host, "0.0.0.0")
                    self.assertFalse(app.dual_stack)
                    self.assertIn(f"http://127.0.0.1:{server.server_port}/", app.runtime()["management_urls"])
                finally:
                    server.server_close()
            finally:
                app.loop.close()

    def test_route_detection_ipv4_ipv6_without_sending_data(self):
        v4, v6 = Mock(), Mock()
        v4.__enter__ = Mock(return_value=v4)
        v4.__exit__ = Mock(return_value=False)
        v6.__enter__ = Mock(return_value=v6)
        v6.__exit__ = Mock(return_value=False)
        v4.getsockname.return_value = ("192.0.2.10", 4567)
        v6.getsockname.return_value = ("2001:db8::10", 4567, 0, 0)
        with patch("core.runtime_address.socket.socket", side_effect=[v4, v6]):
            self.assertEqual(detect_runtime_ips(), ["192.0.2.10", "2001:db8::10"])
        v4.send.assert_not_called()
        v6.send.assert_not_called()

    def test_offline_and_specific_binding_fallback(self):
        with patch("core.runtime_address.socket.socket", side_effect=OSError("offline")):
            self.assertEqual(detect_runtime_ips(), ["127.0.0.1"])
            self.assertEqual(detect_runtime_ips("192.0.2.5"), ["192.0.2.5"])
            self.assertEqual(detect_runtime_ips("::1"), ["::1"])

    def test_ipv4_only_and_loopback_route_filtered(self):
        probe = Mock()
        probe.__enter__ = Mock(return_value=probe)
        probe.__exit__ = Mock(return_value=False)
        probe.getsockname.return_value = ("192.0.2.9", 1234)
        with patch("core.runtime_address.socket.socket", side_effect=[probe, OSError("no ipv6")]):
            self.assertEqual(detect_runtime_ips(), ["192.0.2.9"])
        probe.getsockname.return_value = ("0.0.0.0", 1234)
        with patch("core.runtime_address.socket.socket", side_effect=[probe, OSError("no ipv6")]):
            self.assertEqual(detect_runtime_ips(), ["127.0.0.1"])

    def test_urls_match_real_listener_and_ipv6_brackets(self):
        ips = ["192.0.2.10", "2001:db8::10"]
        result = runtime_info(ips, "::", 9123, dual_stack=True)
        self.assertIn("http://192.0.2.10:9123/", result["management_urls"])
        self.assertIn("http://[2001:db8::10]:9123/", result["management_urls"])
        self.assertFalse(result["loopback_only"])
        v4 = runtime_info(ips, "0.0.0.0", 9123)
        self.assertNotIn("http://[2001:db8::10]:9123/", v4["management_urls"])
        local = runtime_info(ips, "::1", 9123)
        self.assertEqual(local["management_urls"], ["http://[::1]:9123/"])
        self.assertTrue(local["loopback_only"])

    def test_host_allowlist_url_formats_and_invalid_entries(self):
        for allowed, expected in ((["console.example.test"], ["http://console.example.test:9123/"]),
                                  (["[::1]"], ["http://[::1]:9123/"]),
                                  (["::1"], ["http://[::1]:9123/"]),
                                  (["console.example.test:8080"], ["http://console.example.test:8080/"]),
                                  (["http://bad/path", "user:password@host", "host:bad", "bad name"], [])):
            with self.subTest(allowed=allowed):
                result = runtime_info(["127.0.0.1"], "::", 9123, dual_stack=True, allowed_hosts=allowed)
                self.assertEqual(result["management_urls"], expected)
        result = runtime_info(["192.0.2.10"], "0.0.0.0", 9123, allowed_hosts=["*"])
        self.assertIn("http://192.0.2.10:9123/", result["management_urls"])

    def test_allowlist_never_adds_unbound_ip_or_unsupported_family(self):
        for host, ips, allowed, expected in (
                ("127.0.0.1", ["192.0.2.10"], ["127.0.0.1", "::1"], ["http://127.0.0.1:9123/"]),
                ("::1", ["192.0.2.10"], ["127.0.0.1", "::1"], ["http://[::1]:9123/"]),
                ("192.0.2.10", ["192.0.2.10", "192.0.2.20"], ["192.0.2.10", "192.0.2.20"], ["http://192.0.2.10:9123/"]),
                ("0.0.0.0", ["192.0.2.10", "2001:db8::10"], ["192.0.2.10", "2001:db8::10"], ["http://192.0.2.10:9123/"]),
                ("127.0.0.1", ["127.0.0.1"], ["[::1]:9123"], [])):
            with self.subTest(host=host, allowed=allowed):
                self.assertEqual(runtime_info(ips, host, 9123, allowed_hosts=allowed)["management_urls"], expected)

    def test_startup_prints_snapshot_urls_without_login_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            config = {"web_username": "private-user", "web_password": "private-pass"}
            app = ManagementApp(config, directory, manager=FakeManager(), client=Mock())
            server = ManagementServer(("127.0.0.1", 0), app)
            app.runtime_ips = ["192.0.2.10", "2001:db8::10"]
            output = io.StringIO()
            with patch("core.web_server.ManagementApp", return_value=app), \
                    patch("core.web_server.ManagementServer", return_value=server), \
                    patch.object(server, "serve_forever", side_effect=KeyboardInterrupt), \
                    patch("core.web_server.webbrowser.open") as browser, \
                    contextlib.redirect_stdout(output):
                serve(config, port=server.server_port, state_dir=directory, open_browser=True)
            log = output.getvalue()
            self.assertIn("运行环境 IP：192.0.2.10、2001:db8::10", log)
            self.assertIn(app.runtime()["management_urls"][0], log)
            browser.assert_called_once_with(app.runtime()["management_urls"][0])
            self.assertIn("仅本机可访问", log)
            for secret in ("private-user", "private-pass", "#token", "[IP]", "<服务器"):
                self.assertNotIn(secret, log)

    def test_invalid_credentials_release_startup_lock(self):
        from core.config import ConfigError
        with patch("core.web_server.ProcessLock") as lock:
            with self.assertRaises(ConfigError):
                serve({"web_password": ""})
            lock.return_value.close.assert_called_once()
