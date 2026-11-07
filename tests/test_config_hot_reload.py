import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from core.config import ConfigError, DEFAULT_CONFIG, KEEP_SECRET, load_config, validate_config
from core.config_editor import ConfigEditor
from core.web_server import ManagementApp, ApiProblem


class FakeClient:
    def __init__(self, **kwargs):
        import threading
        self._lock = threading.RLock()
        self.api_key = kwargs.get("api_key", "")
        self.jwt_token = kwargs.get("jwt_token", "")
        self.seedphrase = kwargs.get("seedphrase", "")
        self.auth_code = kwargs.get("auth_code", "")
        self.email = kwargs.get("email", "")
        self.password = kwargs.get("password", "")
        self.api_base = kwargs.get("api_base", "https://api.bringyour.com")
        self.timeout = kwargs.get("timeout", 15)
        self.jwt = "fake-jwt"
        self.jwt_payload = {}
        self.network_info = {}
        self.auth_method = "key"


class FakeManager:
    def __init__(self, client):
        self.client = client
        self.has_owned_clients = False

    async def list_gateways(self):
        return []


class ConfigHotReloadTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.config_file = self.root / "config.json"
        self.state_dir = self.root / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        # Create initial config
        self.initial_config = {
            **DEFAULT_CONFIG,
            "web_username": "admin",
            "web_password": "initial_password",
            "api_key": "urn_initial_key",
            "timeout_seconds": 15,
            "web_port": 8777,
            "web_host": "127.0.0.1",
        }
        self.config_file.write_text(json.dumps(self.initial_config), encoding="utf-8")
        self.client = FakeClient(api_key="urn_initial_key", timeout=15)
        self.manager = FakeManager(self.client)
        self.app = ManagementApp(
            self.initial_config,
            self.state_dir,
            manager=self.manager,
            client=self.client,
            web_port=8777,
            web_host="127.0.0.1",
            config_path=self.config_file,
        )

    def tearDown(self):
        try:
            self.app.loop.call_soon_threadsafe(self.app.loop.stop)
        except Exception:
            pass
        self.temp_dir.cleanup()

    def test_hot_reload_applies_immediately_without_restart(self):
        # 1. Read initial config
        res = self.app.configuration()
        self.assertFalse(res["restart_required"])
        self.assertEqual(res["config"]["web_password"], KEEP_SECRET)
        self.assertEqual(res["config"]["api_key"], KEEP_SECRET)

        # 2. Update timeout and web_password and api_key
        revision = res["revision"]
        update_payload = {
            "revision": revision,
            "config": {
                **res["config"],
                "web_password": "new_hot_password",
                "api_key": "urn_new_hot_key",
                "timeout_seconds": 30,
            }
        }
        update_res = asyncio.run(self.app.update_configuration(update_payload))

        # restart_required must be False because web_port and web_host did not change!
        self.assertFalse(update_res["restart_required"])
        self.assertEqual(update_res["restart_fields"], [])

        # In-memory values must be updated immediately!
        self.assertEqual(self.app.web_password, "new_hot_password")
        self.assertEqual(self.app.client.api_key, "urn_new_hot_key")
        self.assertEqual(self.app.client.timeout, 30)
        self.assertIsNone(self.app.client.jwt)  # cached jwt cleared

        # Test login succeeds with new password immediately
        session = self.app.login("admin", "new_hot_password")
        self.assertTrue(bool(session))

    def test_restart_required_only_when_port_or_host_changes(self):
        res = self.app.configuration()
        revision = res["revision"]
        update_payload = {
            "revision": revision,
            "config": {
                **res["config"],
                "web_port": 9999,
            }
        }
        update_res = asyncio.run(self.app.update_configuration(update_payload))
        self.assertTrue(update_res["restart_required"])
        self.assertEqual(update_res["restart_fields"], ["web_port"])

    def test_reveal_endpoint_and_param(self):
        # By default, secrets are redacted with KEEP_SECRET
        redacted = self.app.configuration(reveal=False)
        self.assertEqual(redacted["config"]["api_key"], KEEP_SECRET)
        self.assertEqual(redacted["config"]["web_password"], KEEP_SECRET)

        # When reveal=True, real secrets are returned
        revealed = self.app.configuration(reveal=True)
        self.assertEqual(revealed["config"]["api_key"], "urn_initial_key")
        self.assertEqual(revealed["config"]["web_password"], "initial_password")

        # Test dispatch /api/config/reveal for single field
        single = self.app.dispatch("GET", "/api/config/reveal", "field=api_key", {})
        self.assertEqual(single, {"field": "api_key", "value": "urn_initial_key"})

        single_pwd = self.app.dispatch("GET", "/api/config/reveal", "field=web_password", {})
        self.assertEqual(single_pwd, {"field": "web_password", "value": "initial_password"})

    def test_keep_secret_preserves_existing_values(self):
        res = self.app.configuration()
        revision = res["revision"]
        # Submit KEEP_SECRET for api_key and web_password
        update_payload = {
            "revision": revision,
            "config": {
                **res["config"],
                "api_key": KEEP_SECRET,
                "web_password": KEEP_SECRET,
                "timeout_seconds": 25,
            }
        }
        update_res = asyncio.run(self.app.update_configuration(update_payload))
        self.assertFalse(update_res["restart_required"])
        # Original secrets must remain unchanged on disk and in memory
        saved_on_disk = json.loads(self.config_file.read_text(encoding="utf-8"))
        self.assertEqual(saved_on_disk["api_key"], "urn_initial_key")
        self.assertEqual(saved_on_disk["web_password"], "initial_password")
        self.assertEqual(self.app.web_password, "initial_password")
        self.assertEqual(self.app.client.api_key, "urn_initial_key")
        self.assertEqual(self.app.client.timeout, 25)

    def test_literal_keep_secret_in_file_raises_clear_error(self):
        broken = {**DEFAULT_CONFIG, "api_key": KEEP_SECRET}
        with self.assertRaises(ConfigError) as ctx:
            validate_config(broken)
        self.assertIn("包含占位符", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
