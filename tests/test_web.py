import asyncio
import http.client
import json
import tempfile
import threading
import unittest
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
        self.app = ManagementApp({}, self.temp.name, manager=FakeManager(), client=self.client)
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
        status, headers, _ = self.request("POST", "/api/login", {"token": self.app.token})
        self.assertEqual(status, 200)
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.cookie = headers["Set-Cookie"].split(";")[0]
        self.csrf = self.request("GET", "/api/session")[2]["csrf"]

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
        self.assertEqual(self.request("POST", "/api/login", {"token": self.app.token},
            extra={"Origin": "https://attacker.invalid"})[0], 403)
        self.assertEqual(self.request("POST", "/api/login", {"token": self.app.token},
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
