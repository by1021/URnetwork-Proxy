import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from core.gateway import GatewayManager


class FixtureClient:
    def __init__(self):
        self.creates = []
        self.removed = []

    def create_proxy(self, **kwargs):
        self.creates.append(kwargs)
        return {"client_id": f"fixture-{len(self.creates)}", "host": "proxy.example.test",
                "http_port": 80, "socks_port": 1080, "auth_token": "fixture"}

    def remove_proxy(self, client_id):
        self.removed.append(client_id)
        return {"ok": True}


class FixtureRelay:
    def __init__(self, host, port, get_upstream, **kwargs):
        self.get_upstream = get_upstream
        self.connections = 0
        self.drains = 0

    async def start(self):
        pass

    async def close(self):
        self.drain_active_connections()

    def drain_active_connections(self):
        self.drains += 1


def sample(country, ip="203.0.113.1", residential=True, **kwargs):
    return {"ok": True, "http_ok": True, "socks_ok": True, "exit_ip": ip, "latency_ms": 5,
            "country_code": country, "country_source": "fixture", "is_residential": residential, **kwargs}


class GatewayCountryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.client = FixtureClient()
        self.samples = [sample("US")]

        async def probe(upstream):
            return self.samples.pop(0) if len(self.samples) > 1 else dict(self.samples[0])

        self.probe = probe
        self.mgr = GatewayManager(self.client, Path(self.tmp.name) / "gateways.json",
                                  probe=probe, relay_factory=FixtureRelay)
        await self.mgr.initialize()

    async def asyncTearDown(self):
        await self.mgr.close()
        self.tmp.cleanup()

    async def create(self, **kwargs):
        return await self.mgr.create_gateway({"name": "US fixture", "mode": "country", "country_code": "US",
                                              "location_id": "us-country", "enabled": True, **kwargs})

    async def test_same_country_is_exposed_separately_from_requested_country(self):
        gateway = await self.create()
        self.assertEqual(gateway["status"], "running")
        self.assertEqual(gateway.get("exit_country_code"), "US")
        self.assertEqual(gateway.get("country_source"), "fixture")

    async def test_vietnam_candidate_is_discarded_before_same_country_candidate(self):
        self.samples = [sample("VN"), sample("us", "203.0.113.2")]
        gateway = await self.create()
        self.assertEqual(gateway["exit_ip"], "203.0.113.2")
        self.assertEqual(gateway.get("exit_country_code"), "US")
        self.assertIn("fixture-1", self.client.removed)
        self.assertEqual(len(self.client.creates), 2)
        self.assertTrue(all(c["target"] == "US" and c["location_id"] == "us-country" for c in self.client.creates))

    async def test_vietnam_never_becomes_residential_fallback(self):
        self.samples = [sample("VN", residential=False)]
        gateway = await self.create()
        self.assertNotEqual(gateway["status"], "running")
        self.assertEqual(gateway["last_error"], "exit_country_mismatch")
        self.assertIsNone(self.mgr._gateways[gateway["id"]].upstream)
        self.assertLessEqual(len(self.client.creates), 4)
        self.assertEqual(len(self.client.creates), len(self.client.removed))
        self.assertGreater(self.mgr._gateways[gateway["id"]].next_create, self.mgr._clock())

    async def test_unknown_country_fails_closed_without_creation_storm(self):
        self.samples = [sample("")]
        gateway = await self.create(residential_only=False)
        self.assertEqual(gateway["last_error"], "exit_country_unverified")
        self.assertNotEqual(gateway["status"], "running")
        self.assertEqual(len(self.client.creates), 1)

    async def test_http_mismatch_cannot_hide_behind_us_socks_exit(self):
        self.samples = [sample("US", country_codes={"http": "VN", "socks": "US", "https": "US"})]
        gateway = await self.create(residential_only=False)
        self.assertEqual(gateway["last_error"], "exit_country_mismatch")
        self.assertIsNone(self.mgr._gateways[gateway["id"]].upstream)

    async def test_missing_http_country_is_not_treated_as_us(self):
        self.samples = [sample("US", country_codes={"socks": "US"})]
        gateway = await self.create(residential_only=False)
        self.assertEqual(gateway["last_error"], "exit_country_unverified")
        self.assertNotEqual(gateway["status"], "running")

    async def test_failed_rotation_preserves_verified_old_upstream(self):
        gateway = await self.create()
        self.samples = [sample("VN", "203.0.113.2")]
        rotated = await self.mgr.rotate_gateway(gateway["id"])
        self.assertEqual(rotated["exit_ip"], gateway["exit_ip"])
        self.assertEqual(rotated["country_code"], "US")
        self.assertEqual(rotated["last_error"], "exit_country_mismatch")
        self.assertEqual(self.mgr._gateways[gateway["id"]].upstream["client_id"], "fixture-1")
        self.assertNotIn("fixture-1", self.client.removed)

    async def test_failed_country_edit_keeps_old_binding(self):
        gateway = await self.create()
        changed = await self.mgr.update_gateway(gateway["id"], {"country_code": "VN", "location_id": "vn-country"})
        self.assertEqual(changed["country_code"], "US")
        self.assertEqual(changed["location_id"], "us-country")
        self.assertEqual(changed["last_error"], "exit_country_mismatch")

    async def test_country_drift_unpublishes_and_drains_without_auto_creation(self):
        gateway = await self.create(auto_heal=False)
        runtime = self.mgr._gateways[gateway["id"]]
        relay = runtime.relay
        old_drains = relay.drains
        self.samples = [sample("VN")]
        checked = await self.mgr.check_gateway(gateway["id"])
        self.assertIsNone(runtime.upstream)
        self.assertIsNone(relay.get_upstream())
        self.assertGreater(relay.drains, old_drains)
        self.assertEqual(checked["last_error"], "exit_country_mismatch")
        self.assertEqual(checked.get("exit_country_code"), "VN")
        self.assertEqual(len(self.client.creates), 1)
        self.assertIn("fixture-1", self.client.removed)

    async def test_country_drift_auto_recovers_only_to_same_country(self):
        gateway = await self.create()
        self.samples = [sample("VN"), sample("US", "203.0.113.2")]
        checked = await self.mgr.check_gateway(gateway["id"])
        self.assertEqual(checked["status"], "running")
        self.assertEqual(checked["exit_ip"], "203.0.113.2")
        self.assertEqual(checked.get("exit_country_code"), "US")
        self.assertIn("fixture-1", self.client.removed)

    async def test_partial_protocol_failure_cannot_hide_country_drift(self):
        gateway = await self.create(auto_heal=False)
        runtime = self.mgr._gateways[gateway["id"]]
        relay = runtime.relay
        old_drains = relay.drains
        self.samples = [sample("VN", ok=False, socks_ok=False, country_codes={"http": "VN"})]
        with patch.object(self.mgr, "_control_available", new=AsyncMock(return_value=True)):
            checked = await self.mgr.check_gateway(gateway["id"])
        self.assertIsNone(runtime.upstream)
        self.assertIsNone(relay.get_upstream())
        self.assertGreater(relay.drains, old_drains)
        self.assertEqual(checked["last_error"], "exit_country_mismatch")
        self.assertIn("fixture-1", self.client.removed)
        self.assertEqual(len(self.client.creates), 1)

    async def test_partial_protocol_failure_with_unknown_country_withdraws_exit(self):
        gateway = await self.create(auto_heal=False)
        runtime = self.mgr._gateways[gateway["id"]]
        self.samples = [sample("", ok=False, socks_ok=False, country_codes={"http": ""})]
        with patch.object(self.mgr, "_control_available", new=AsyncMock(return_value=True)):
            checked = await self.mgr.check_gateway(gateway["id"])
        self.assertIsNone(runtime.upstream)
        self.assertEqual(checked["last_error"], "exit_country_unverified")

    async def test_partial_same_country_failure_keeps_connectivity_recovery_semantics(self):
        gateway = await self.create(auto_heal=False)
        runtime = self.mgr._gateways[gateway["id"]]
        previous = runtime.upstream
        self.samples = [sample("US", ok=False, socks_ok=False, country_codes={"http": "US"})]
        with patch.object(self.mgr, "_control_available", new=AsyncMock(return_value=True)):
            checked = await self.mgr.check_gateway(gateway["id"])
        self.assertIs(runtime.upstream, previous)
        self.assertEqual(checked["last_error"], "upstream_probe_failed")
        self.assertEqual(checked["failures"], 1)
        self.assertNotIn("fixture-1", self.client.removed)

    async def test_total_connectivity_failure_keeps_existing_failure_threshold(self):
        gateway = await self.create(auto_heal=False)
        runtime = self.mgr._gateways[gateway["id"]]
        previous = runtime.upstream
        self.samples = [sample("", ip="", ok=False, http_ok=False, socks_ok=False, country_codes={})]
        with patch.object(self.mgr, "_control_available", new=AsyncMock(return_value=True)):
            checked = await self.mgr.check_gateway(gateway["id"])
        self.assertIs(runtime.upstream, previous)
        self.assertEqual(checked["last_error"], "upstream_probe_failed")
        self.assertEqual(checked["failures"], 1)

    async def test_restart_does_not_restore_wrong_country_candidate(self):
        await self.create()
        await self.mgr.close()
        self.samples = [sample("VN")]
        self.mgr = GatewayManager(self.client, Path(self.tmp.name) / "gateways.json",
                                  probe=self.probe, relay_factory=FixtureRelay)
        await self.mgr.initialize()
        gateway = (await self.mgr.list_gateways())[0]
        self.assertEqual(gateway["country_code"], "US")
        self.assertEqual(gateway["last_error"], "exit_country_mismatch")
        self.assertNotEqual(gateway["status"], "running")

    async def test_best_mode_keeps_global_selection(self):
        self.samples = [sample("VN")]
        gateway = await self.create(mode="best", country_code="", location_id=None)
        self.assertEqual(gateway["status"], "running")
        self.assertEqual(gateway.get("exit_country_code"), "VN")
