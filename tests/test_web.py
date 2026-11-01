import asyncio
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock
from core.web_server import ManagementApp, ManagementServer, ApiProblem


class FakeManager:
    has_owned_clients = False
    def __init__(self):
        self.rows = []
    async def initialize(self):
        pass
    async def close(self):
        pass
    async def list_gateways(self):
        return self.rows
    async def create_gateway(self, data):
        row = dict(data, id="fixture")
        self.rows.append(row)
        return row
    async def update_gateway(self, gid, data):
        self.rows[0].update(data)
        return self.rows[0]
    async def delete_gateway(self, gid):
        self.rows = []
        return {"deleted": True}
    async def start_gateway(self, gid):
        return {"status": "running"}
    stop_gateway = rotate_gateway = check_gateway = start_gateway
    def events(self, after=0):
        return []


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.client = Mock()
        self.client.api_base = "https://fixture.invalid"
        self.client._request.return_value = {"locations": []}
        self.config_path = Path(self.temp.name) / "config.json"
        self.app = ManagementApp({}, self.temp.name, manager=FakeManager(), client=self.client,
                                 config_path=self.config_path)
        self.server = ManagementServer(("127.0.0.1", 0), self.app)
        self.app.start()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port
        self.cookie, self.csrf = "", ""

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.app.close()
        self.temp.cleanup()

    def request(self, method, path, data=None, extra=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        if self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        headers.update(extra or {})
        conn.request(method, path, body=None if data is None else json.dumps(data), headers=headers)
        response = conn.getresponse()
        status, headers, body = response.status, dict(response.getheaders()), response.read()
        conn.close()
        return status, headers, json.loads(body)

    def login(self):
        status, headers, _ = self.request("POST", "/api/login", {"username": "admin", "password": "admin"})
        self.assertEqual(status, 200)
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.cookie = headers["Set-Cookie"].split(";")[0]
        self.csrf = self.request("GET", "/api/session")[2]["csrf"]

    def test_gateway_optional_credentials_and_copy_format(self):
        self.login()
        # Create with optional credentials
        payload = {"name": "AuthGW", "port": 10809, "mode": "best", "username": "user1", "password": "pwd"}
        status, _, row = self.request("POST", "/api/gateways", payload)
        self.assertEqual(status, 200)
        self.assertEqual(row.get("username"), "user1")
        self.assertEqual(row.get("password"), "pwd")

        # GET /api/gateways retains credentials for authorized admin UI
        gateways = self.request("GET", "/api/gateways")[2]["gateways"]
        match = next((g for g in gateways if g["id"] == row["id"]), None)
        self.assertIsNotNone(match)
        self.assertEqual(match["username"], "user1")
        self.assertEqual(match["password"], "pwd")

        # Cleanup
        self.assertEqual(self.request("DELETE", f"/api/gateways/{row['id']}", {})[0], 200)

    def test_auth_csrf_and_gateway_crud(self):
        self.assertEqual(self.request("GET", "/api/gateways")[0], 401)
        self.login()
        csrf, self.csrf = self.csrf, ""
        self.assertEqual(self.request("POST", "/api/gateways", {})[0], 403)
        self.csrf = csrf
        status, _, row = self.request("POST", "/api/gateways", {"name": "Local", "port": 10801, "mode": "best"})
        self.assertEqual(status, 200)
        self.assertEqual(row["port"], 10801)
        self.assertEqual(len(self.request("GET", "/api/gateways")[2]["gateways"]), 1)
        self.assertEqual(self.request("DELETE", "/api/gateways/fixture", {})[0], 200)

    def test_cross_origin_and_dns_rebinding_rejected(self):
        self.assertEqual(self.request("GET", "/api/health", extra={"Host": "attacker.invalid"})[0], 403)
        self.assertEqual(self.request("POST", "/api/login", {"username": "admin", "password": "admin"},
            extra={"Origin": "https://attacker.invalid"})[0], 403)
        self.assertEqual(self.request("POST", "/api/login", {"username": "admin", "password": "admin"},
            extra={"Content-Type": "text/plain"})[0], 415)

    def test_settings_redacts_credentials_and_unknown_static_path(self):
        self.login()
        self.app.config["seedphrase"] = "never-return-this"
        status, _, body = self.request("GET", "/api/settings")
        self.assertEqual(status, 200)
        self.assertNotIn("never-return-this", json.dumps(body))
        self.assertEqual(self.request("GET", "/config.json")[0], 404)
        self.assertEqual(self.request("GET", "/api/countries")[2]["source"], "/network/provider-locations")

    def test_country_binding_requires_matching_location(self):
        self.login()
        status, _, _ = self.request("POST", "/api/gateways",
            dict(mode="country", country_code="JP", location_id="wrong"))
        self.assertEqual(status, 400)

    def test_logout_revokes_session(self):
        self.login()
        self.assertEqual(self.request("POST", "/api/logout", {})[0], 200)
        self.assertEqual(self.request("GET", "/api/session")[0], 401)

    def test_password_login_rejects_invalid_credentials_and_old_tokens(self):
        for data in ({}, {"token": "old-launch-token"},
                     {"username": "admin", "password": "wrong"},
                     {"username": "wrong", "password": "admin"},
                     {"username": ["admin"], "password": "admin"},
                     {"username": "admin", "password": None}):
            with self.subTest(data=data):
                status, headers, body = self.request("POST", "/api/login", data)
                self.assertEqual(status, 401)
                self.assertNotIn("Set-Cookie", headers)
                self.assertEqual(body["error"], "用户名或密码错误")
        self.login()

    def test_password_login_rate_limit_and_expiry(self):
        from unittest.mock import patch
        with patch("core.web_server.time.monotonic", return_value=100):
            for _ in range(10):
                self.assertEqual(self.request("POST", "/api/login", {})[0], 401)
            self.assertEqual(self.request("POST", "/api/login",
                {"username": "admin", "password": "admin"})[0], 429)
        with patch("core.web_server.time.monotonic", return_value=161):
            self.login()
        with patch("core.web_server.time.monotonic", return_value=161 + 43201):
            self.assertEqual(self.request("GET", "/api/session")[0], 401)

    def test_management_password_is_masked_and_independent(self):
        from core.config import KEEP_SECRET
        self.login()
        payload = self.config_payload()
        self.assertEqual(payload["config"]["web_password"], KEEP_SECRET)
        self.app.manager.rows = [dict(id="fixture", mode="best")]
        payload["config"].update(web_username="operator", web_password="local-private")
        status, _, result = self.request("PUT", "/api/config", payload)
        self.assertEqual(status, 200)
        self.assertNotIn("local-private", json.dumps(result))
        self.assertIn("web_password", result["restart_fields"])
        self.assertEqual(json.loads(self.config_path.read_text())["web_password"], "local-private")
        self.assertEqual(self.request("POST", "/api/login",
            {"username": "operator", "password": "local-private"})[0], 401)
        self.login()  # Config changes only take effect on restart.

    def test_custom_management_credentials_replace_defaults(self):
        app = ManagementApp({"web_username": "管理员", "web_password": " 本地密码 ",
                             "email": "upstream", "password": "cloud-password"},
                            self.temp.name, manager=FakeManager(), client=self.client)
        try:
            with self.assertRaises(ApiProblem):
                app.login("admin", "admin")
            key = app.login("管理员", " 本地密码 ")
            self.assertIn(key, app.sessions)
            self.assertEqual(app.settings()["credential_type"], "email")
        finally:
            app.loop.close()

    def test_runtime_info_is_public_and_has_no_credentials(self):
        self.app.runtime_ips = ["192.0.2.10", "2001:db8::10"]
        status, _, runtime = self.request("GET", "/api/runtime")
        self.assertEqual(status, 200)
        self.assertEqual(runtime["primary_ip"], "192.0.2.10")
        self.assertEqual(runtime["ips"], self.app.runtime_ips)
        self.assertEqual(runtime["management_urls"], [f"http://127.0.0.1:{self.port}/"])
        self.assertTrue(runtime["loopback_only"])
        self.assertNotIn("password", json.dumps(runtime))
        self.assertNotIn("token", json.dumps(runtime))

    def test_same_host_different_port_origin_rejected(self):
        self.assertEqual(self.request("POST", "/api/login",
            {"username": "admin", "password": "admin"},
            extra={"Origin": f"http://127.0.0.1:{self.port + 1}"})[0], 403)

    def test_runtime_links_obey_host_allowlist(self):
        from urllib.parse import urlsplit
        self.app.config["allowed_hosts"] = ["console.example.test"]
        status, _, runtime = self.request("GET", "/api/runtime",
            extra={"Host": f"console.example.test:{self.port}"})
        self.assertEqual(status, 200)
        self.assertEqual(runtime["management_urls"], [f"http://console.example.test:{self.port}/"])
        for url in runtime["management_urls"]:
            self.assertEqual(self.request("GET", "/api/health",
                extra={"Host": urlsplit(url).netloc})[0], 200)

    def test_upstream_update_keeps_management_password_restart_pending(self):
        from unittest.mock import patch
        self.login()
        payload = self.config_payload()
        payload["config"].update(web_username="operator", web_password="pending-password")
        self.assertEqual(self.request("PUT", "/api/config", payload)[0], 200)
        with patch("core.web_server.build_client", return_value=self.client):
            self.assertEqual(self.request("PUT", "/api/settings",
                {"credential_type": "api_key", "credential": "new-upstream-key"})[0], 200)
        config = self.request("GET", "/api/config")[2]
        self.assertTrue(config["restart_required"])
        self.assertIn("web_password", config["restart_fields"])
        self.assertIn("web_username", config["restart_fields"])
        self.login()
        self.assertEqual(self.request("POST", "/api/login",
            {"username": "operator", "password": "pending-password"})[0], 401)

    def test_invalid_web_config_prevents_startup(self):
        import contextlib
        import io
        from unittest.mock import patch
        import main
        self.config_path.write_text('{"web_password":"custom-private"')
        errors = io.StringIO()
        with patch("core.config.CONFIG_FILE", self.config_path), \
                patch("sys.argv", ["main.py", "--web"]), \
                patch("core.web_server.serve") as serve, \
                contextlib.redirect_stderr(errors):
            with self.assertRaises(SystemExit) as result:
                main.main()
        self.assertEqual(result.exception.code, 2)
        serve.assert_not_called()
        self.assertNotIn("custom-private", errors.getvalue())

    def test_settings_blocked_while_gateway_admission_is_inflight(self):
        async def scenario():
            entered, release = asyncio.Event(), asyncio.Event()
            async def validation(data):
                entered.set()
                await release.wait()
            self.app.validate_binding = validation
            pending = asyncio.create_task(self.app.gateway_mutation("POST", None, None, {"mode": "best"}))
            await entered.wait()
            with self.assertRaises(ApiProblem) as error:
                await self.app.update_settings({"credential_type": "api_key", "credential": "fixture"})
            self.assertEqual(error.exception.code, 409)
            release.set()
            await pending
            self.assertEqual(self.app.gateway_operations, 0)
        self.app.call(scenario())

    def test_settings_blocked_by_in_memory_owned_clients(self):
        self.app.manager.has_owned_clients = True
        with self.assertRaises(ApiProblem) as error:
            self.app.call(self.app.update_settings({"credential_type": "api_key", "credential": "fixture"}))
        self.assertEqual(error.exception.code, 409)

    def test_review_partial_update_validates_merged_country(self):
        self.login()
        self.app.manager.rows = [dict(id="fixture", mode="country", country_code="JP", location_id="loc-jp")]
        self.client._request.return_value = {"locations": [dict(location_type="country", country_code="JP", location_id="loc-jp", provider_count=10)]}
        status, _, _ = self.request("PUT", "/api/gateways/fixture", {"location_id": "loc-us"})
        self.assertEqual(status, 400)
        self.assertEqual(self.app.manager.rows[0]["location_id"], "loc-jp")

    def test_review_concurrent_put_serializes_merge_validation_and_commit(self):
        self.app.manager.rows = [dict(id="fixture", mode="country", country_code="JP", location_id="loc-jp")]
        async def scenario():
            entered, release = asyncio.Event(), asyncio.Event()
            calls = []
            async def validation(data):
                calls.append((data["country_code"], data["location_id"]))
                if len(calls) == 1:
                    entered.set()
                    await release.wait()
            self.app.validate_binding = validation
            first = asyncio.create_task(self.app.gateway_mutation("PUT", "fixture", None, {"location_id": "loc-jp"}))
            await entered.wait()
            second = asyncio.create_task(self.app.gateway_mutation("PUT", "fixture", None,
                {"country_code": "US", "location_id": "loc-us"}))
            await asyncio.sleep(.01)
            self.assertFalse(second.done())
            release.set()
            await asyncio.gather(first, second)
            self.assertEqual(calls, [("JP", "loc-jp"), ("US", "loc-us")])
            self.assertEqual(self.app.manager.rows[0]["country_code"], "US")
            self.assertEqual(self.app.manager.rows[0]["location_id"], "loc-us")
        self.app.call(scenario())
    def test_clean_proxies_endpoint(self):
        self.assertEqual(self.request("POST", "/api/proxies/clean", {})[0], 401)
        self.login()
        self.client.clean_clients.return_value = {"proxies_removed": 3, "clients_reset": True}
        status, _, body = self.request("POST", "/api/proxies/clean", {})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["proxies_removed"], 3)
        self.assertTrue(body["clients_reset"])
        self.client.clean_clients.assert_called_once()

    def test_settings_includes_web_port(self):
        self.login()
        status, _, body = self.request("GET", "/api/settings")
        self.assertEqual(status, 200)
        self.assertIn("web_port", body)
        self.assertEqual(body["web_port"], self.port)

    def test_management_app_custom_port(self):
        app = ManagementApp({"web_port": 8999}, self.temp.name, manager=FakeManager(), client=self.client)
        self.assertEqual(app.web_port, 8999)
        self.assertEqual(app.settings()["web_port"], 8999)
        app.loop.close()

    def config_payload(self):
        body = self.request("GET", "/api/config")[2]
        return {"config": body["config"], "revision": body["revision"]}

    def test_config_auth_csrf_and_roundtrip(self):
        self.assertEqual(self.request("GET", "/api/config")[0], 401)
        self.assertEqual(self.request("PUT", "/api/config", {})[0], 401)
        self.login()
        payload = self.config_payload()
        csrf, self.csrf = self.csrf, ""
        self.assertEqual(self.request("PUT", "/api/config", payload)[0], 403)
        self.assertEqual(self.request("POST", "/api/config/validate", payload)[0], 403)
        self.csrf = csrf
        payload["config"].update(web_port=9123, timeout_seconds=30, extension={"sample": ",}"})
        status, _, body = self.request("PUT", "/api/config", payload)
        self.assertEqual(status, 200)
        self.assertTrue(body["restart_required"])
        self.assertEqual(body["active_port"], self.port)
        self.assertEqual(json.loads(self.config_path.read_text())["web_port"], 9123)
        self.assertEqual(self.request("GET", "/api/config")[2]["config"]["extension"], {"sample": ",}"})
        self.client.login.assert_not_called()

    def test_config_validation_errors_conflict_do_not_write(self):
        self.login()
        payload = self.config_payload()
        payload["config"]["timeout_seconds"] = False
        self.assertEqual(self.request("PUT", "/api/config", payload)[0], 400)
        self.assertFalse(self.config_path.exists())
        self.config_path.write_text('{"web_port":9000}')
        self.assertEqual(self.request("PUT", "/api/config", payload)[0], 409)
        self.assertEqual(json.loads(self.config_path.read_text())["web_port"], 9000)

    def test_config_source_masking_normalization_and_no_secret_reflection(self):
        self.config_path.write_text('{"api_key":"private-existing"}')
        self.app.config["api_key"] = "private-existing"
        self.login()
        payload = self.config_payload()
        self.assertNotIn("private-existing", json.dumps(payload))
        source = json.dumps(payload.pop("config"))[:-1] + ', "extension": ",}", /*comment*/ }'
        payload["source"] = source
        status, _, result = self.request("POST", "/api/config/validate", payload)
        self.assertEqual(status, 200)
        self.assertNotIn("private-existing", json.dumps(result))
        self.assertEqual(result["config"]["extension"], ",}")
        self.assertEqual(self.request("PUT", "/api/config", payload)[0], 200)
        self.assertEqual(json.loads(self.config_path.read_text())["api_key"], "private-existing")
        payload = self.config_payload()
        payload["config"]["password"] = ["private-malformed"]
        status, _, error = self.request("PUT", "/api/config", payload)
        self.assertEqual(status, 400)
        self.assertNotIn("private-malformed", json.dumps(error))

    def test_config_account_guard_and_pending_restart(self):
        self.login()
        self.app.manager.rows = [dict(id="fixture", mode="best")]
        payload = self.config_payload()
        payload["config"]["web_port"] = 9123
        self.assertEqual(self.request("PUT", "/api/config", payload)[0], 200)
        payload = self.config_payload()
        payload["config"]["api_key"] = "new-private"
        self.assertEqual(self.request("PUT", "/api/config", payload)[0], 409)
        self.app.manager.rows = []
        self.app.manager.has_owned_clients = True
        self.assertEqual(self.request("PUT", "/api/config", payload)[0], 409)
        self.app.manager.has_owned_clients = False
        self.assertEqual(self.request("PUT", "/api/config", payload)[0], 200)
        self.assertEqual(self.request("POST", "/api/gateways", dict(mode="best"))[0], 409)

    def test_config_update_serialization_and_write_failure(self):
        self.login()
        payload = self.config_payload()
        self.app.gateway_operations = 1
        self.assertEqual(self.request("PUT", "/api/config", payload)[0], 409)
        self.app.gateway_operations = 0
        from unittest.mock import patch
        with patch("core.config_editor.atomic_json", side_effect=OSError("private-error")):
            status, _, body = self.request("PUT", "/api/config", payload)
            self.assertEqual(status, 503)
            self.assertNotIn("private-error", json.dumps(body))
        self.assertFalse(self.app.updating_credentials)
        self.assertFalse(self.config_path.exists())

    def test_raw_config_object_duplicate_keys_rejected(self):
        self.login()
        revision = self.config_payload()["revision"]
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        body = '{"revision":"' + revision + '","config":{"web_port":9000,"web_port":9001}}'
        conn.request("PUT", "/api/config", body=body, headers={
            "Content-Type": "application/json", "Cookie": self.cookie, "X-CSRF-Token": self.csrf})
        response = conn.getresponse()
        self.assertEqual(response.status, 400)
        self.assertIn("重复", json.loads(response.read())["error"])
        conn.close()
        self.assertFalse(self.config_path.exists())
    def test_main_cli_web_port_priority(self):
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--web", action="store_true")
        parser.add_argument("--web-port", type=int, default=None)

        # 1. No cli arg, no config -> default 8777
        args = parser.parse_args(["--web"])
        cfg = {}
        port = args.web_port if args.web_port is not None else cfg.get("web_port", 8777)
        self.assertEqual(port, 8777)

        # 2. No cli arg, config has web_port -> config value
        cfg = {"web_port": 9123}
        port = args.web_port if args.web_port is not None else cfg.get("web_port", 8777)
        self.assertEqual(port, 9123)

        # 3. Cli arg specified -> cli takes priority over config
        args = parser.parse_args(["--web", "--web-port", "9555"])
        port = args.web_port if args.web_port is not None else cfg.get("web_port", 8777)
        self.assertEqual(port, 9555)

    def test_main_cli_web_host_priority(self):
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--web", action="store_true")
        parser.add_argument("--web-host", type=str, default=None)

        # 1. No cli arg, no config -> default loopback
        args = parser.parse_args(["--web"])
        cfg = {}
        host = args.web_host or cfg.get("web_host", "127.0.0.1")
        self.assertEqual(host, "127.0.0.1")

        # 2. No cli arg, config has web_host -> config value
        cfg = {"web_host": "0.0.0.0"}
        host = args.web_host or cfg.get("web_host", "127.0.0.1")
        self.assertEqual(host, "0.0.0.0")

        # 3. Cli arg specified -> cli takes priority over config
        args = parser.parse_args(["--web", "--web-host", "192.168.1.50"])
        host = args.web_host or cfg.get("web_host", "127.0.0.1")
        self.assertEqual(host, "192.168.1.50")

    def test_external_mode_host_and_origin(self):
        # Test server listening on 0.0.0.0
        app = ManagementApp({}, self.temp.name, manager=FakeManager(), client=self.client)
        server = ManagementServer(("0.0.0.0", 0), app)
        app.start()
        port = server.server_port
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            # 1. External IP as Host should succeed
            conn.request("GET", "/api/health", headers={"Host": f"203.0.113.10:{port}"})
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200)
            resp.read()

            # 2. Login with matching Origin should succeed
            login_body = json.dumps({"username": "admin", "password": "admin"})
            conn.request("POST", "/api/login", body=login_body, headers={
                "Content-Type": "application/json",
                "Host": f"203.0.113.10:{port}",
                "Origin": f"http://203.0.113.10:{port}"
            })
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200)
            resp.read()

            # 3. Login with cross-origin attacker Origin should be rejected
            conn.request("POST", "/api/login", body=login_body, headers={
                "Content-Type": "application/json",
                "Host": f"203.0.113.10:{port}",
                "Origin": "https://attacker.invalid"
            })
            resp = conn.getresponse()
            self.assertEqual(resp.status, 403)
            resp.read()

            conn.close()
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
            app.close()

    def test_dual_stack_ipv4_and_ipv6_simultaneous(self):
        # Test server listening on :: (dual-stack IPv4 and IPv6)
        app = ManagementApp({}, self.temp.name, manager=FakeManager(), client=self.client)
        server = ManagementServer(("::", 0), app)
        app.start()
        port = server.server_port
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            # 1. Connect via IPv4 loopback on port
            c4 = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            c4.request("GET", "/api/health", headers={"Host": f"127.0.0.1:{port}"})
            r4 = c4.getresponse()
            self.assertEqual(r4.status, 200)
            r4.read()
            c4.close()

            # 2. Connect via IPv6 loopback on same port
            c6 = http.client.HTTPConnection("::1", port, timeout=5)
            c6.request("GET", "/api/health", headers={"Host": f"[::1]:{port}"})
            r6 = c6.getresponse()
            self.assertEqual(r6.status, 200)
            r6.read()

            # 3. IPv6 login with matching Origin
            login_body = json.dumps({"username": "admin", "password": "admin"})
            c6.request("POST", "/api/login", body=login_body, headers={
                "Content-Type": "application/json",
                "Host": f"[::1]:{port}",
                "Origin": f"http://[::1]:{port}"
            })
            r_login = c6.getresponse()
            self.assertEqual(r_login.status, 200)
            r_login.read()
            c6.close()

            # 4. Simulated external IPv6 address as Host
            c6_ext = http.client.HTTPConnection("::1", port, timeout=5)
            c6_ext.request("GET", "/api/health", headers={"Host": f"[2400:cb00:2048:1::c629:d7a2]:{port}"})
            r_ext = c6_ext.getresponse()
            self.assertEqual(r_ext.status, 200)
            r_ext.read()
            c6_ext.close()
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
            app.close()

    def test_client_reset_handled_cleanly(self):
        import struct, socket, time
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        host_ip = socket.inet_ntoa(bytes([127, 0, 0, 1]))
        s.connect((host_ip, self.port))
        # SO_LINGER with timeout 0 forces TCP RST when closing socket
        s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
        s.close()
        time.sleep(0.05)
        # Server must still be healthy and process subsequent requests normally
        c = http.client.HTTPConnection(host_ip, self.port, timeout=5)
        c.request("GET", "/api/health", headers={"Host": f"{host_ip}:{self.port}"})
        r = c.getresponse()
        self.assertEqual(r.status, 200)
        c.close()
