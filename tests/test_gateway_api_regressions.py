"""Real validation behind the management HTTP API, not a permissive fake manager."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from core.gateway import GatewayManager
from core.web_server import ManagementApp, ManagementServer
from tests.test_gateway import FakeClient, FakeRelay, GOOD
from tests import test_web


class GatewayApiRegressions(unittest.TestCase):
    request = test_web.WebTests.request
    login = test_web.WebTests.login

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.client = FakeClient()
        self.client._request = Mock(return_value={"locations": [dict(
            location_type="country", country_code="us", location_id="loc-us", name="US")]})
        self.client.api_base = "https://fixture.invalid"
        self.manager = GatewayManager(self.client, Path(self.temp.name) / "gateways.json",
                                      probe=AsyncMock(return_value=GOOD), relay_factory=FakeRelay)
        self.app = ManagementApp({}, self.temp.name, manager=self.manager, client=self.client,
                                 config_path=Path(self.temp.name) / "config.json")
        self.server = ManagementServer(("127.0.0.1", 0), self.app)
        self.app.start()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port
        self.cookie = self.csrf = ""
        self.login()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.app.close()
        self.temp.cleanup()

    def payload(self, **changes):
        return dict(dict(name="Test", host="::", port=19083, mode="country",
                         country_code="US", location_id="loc-us", auto_heal=True,
                         interval=30, failures_before_rotate=3, enabled=True,
                         username="", password=""), **changes)

    def test_ui_create_edit_and_listener_aliases(self):
        for i, host in enumerate(("::", "0.0.0.0", "[::]", "127.0.0.1", "[::1]")):
            with self.subTest(host=host):
                status, _, row = self.request("POST", "/api/gateways", self.payload(host=host, port=19100+i))
                self.assertEqual(status, 200, row)
                self.assertEqual(row["status"], "running")
                status, _, edited = self.request("PUT", "/api/gateways/"+row["id"], {"interval": 5})
                self.assertEqual(status, 200, edited)
                self.assertEqual(edited["interval"], 5)

    def test_normalize_country_before_catalog_validation(self):
        status, _, row = self.request("POST", "/api/gateways", self.payload(country_code=" us "))
        self.assertEqual(status, 200, row)
        self.assertEqual(row["country_code"], "US")

    def test_duplicate_port_returns_specific_field(self):
        self.assertEqual(self.request("POST", "/api/gateways", self.payload())[0], 200)
        status, _, row = self.request("POST", "/api/gateways", self.payload(name="Second"))
        self.assertEqual(status, 400)
        self.assertEqual(row.get("field"), "port")
        self.assertIn("占用", row["error"])

    def test_invalid_fields_do_not_return_generic_error_or_secrets(self):
        cases = [("port", 0), ("port", True), ("interval", 4), ("interval", 1.5),
                 ("failures_before_rotate", 0), ("host", "secret.invalid"),
                 ("name", ""), ("username", "secret-user"), ("location_id", "mismatch")]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                status, _, row = self.request("POST", "/api/gateways", self.payload(**{field: value}))
                self.assertEqual(status, 400, row)
                self.assertEqual(row.get("field"), field)
                self.assertNotIn("参数无效，请检查端口", row["error"])
                self.assertNotIn("secret", row["error"])
        self.assertEqual(self.app.call(self.manager.list_gateways()), [])
