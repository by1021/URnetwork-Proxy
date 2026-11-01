"""AC1/2/3/6/7: deterministic lifecycle tests, no external network."""

import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from core.gateway import GatewayManager, PROBE_TIMEOUT


GOOD = dict(ok=True, http_ok=True, socks_ok=True, exit_ip="203.0.113.4",
            latency_ms=12.5, error="")
BAD = dict(ok=False, http_ok=False, socks_ok=False, exit_ip="",
           latency_ms=0.0, error="test-fake-key-secret https://secret.invalid/")


class FakeClient:
    def __init__(self):
        self.created = []
        self.removed = []
        self.threads = []
        self.create_error = False
        self.remove_error = False
        self.create_exception = None
        self.remove_exception = None
        self.before_create = None

    def create_proxy(self, mode="best", target="", location_id=None):
        self.threads.append(threading.get_ident())
        if self.before_create:
            self.before_create()
        self.created.append((mode, target, location_id))
        if self.create_exception is not None:
            raise self.create_exception
        if self.create_error:
            raise RuntimeError("test-fake-key-secret https://secret.invalid/")
        return dict(client_id=f"owned-{len(self.created)}", host="upstream.invalid",
                    http_port=80, socks_port=1080, auth_token="test-fake-key-secret",
                    password="test-fake-password", standard_http="http://secret.invalid/")

    def remove_proxy(self, client_id):
        self.threads.append(threading.get_ident())
        self.removed.append(client_id)
        if self.remove_exception is not None:
            raise self.remove_exception
        if self.remove_error:
            raise RuntimeError("test-fake-key-secret")
        return {}


class FakeRelay:
    busy = set()
    instances = []

    def __init__(self, host, port, get_upstream, *, username="", password="", on_result=None):
        self.address = (host, port)
        self.get_upstream = get_upstream
        self.on_result = on_result
        self.username = username
        self.password = password
        self.connections = 0
        self.started = False
        self.closed = False
        self.instances.append(self)

    async def start(self):
        if self.address in self.busy:
            raise OSError(98, "Address already in use")
        self.busy.add(self.address)
        self.started = True

    async def close(self):
        if self.started:
            self.busy.discard(self.address)
        self.closed = True


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "gateways.json"
        self.client = FakeClient()
        self.probe = AsyncMock(return_value=GOOD.copy())
        self.control = AsyncMock(return_value=True)
        self.control_patch = patch("core.gateway.probe_control", self.control)
        self.control_patch.start()
        FakeRelay.busy = set()
        FakeRelay.instances = []
        self.manager = self.make_manager()
        await self.manager.initialize()

    def make_manager(self):
        return GatewayManager(self.client, self.path, probe=self.probe, relay_factory=FakeRelay)

    async def asyncTearDown(self):
        await self.manager.close()
        self.control_patch.stop()
        self.temp.cleanup()

    async def create(self, **kwargs):
        return await self.manager.create_gateway(dict(port=19080, enabled=False, **kwargs))

    async def running(self, **kwargs):
        gateway = await self.create(**kwargs)
        return await self.manager.start_gateway(gateway["id"])

    async def test_ac2_independent_crud_and_country_binding(self):
        first = await self.running(mode="country", country_code="jp", location_id="loc-jp")
        second = await self.manager.create_gateway(dict(port=19081, enabled=True))
        self.assertEqual(self.client.created, [("country", "JP", "loc-jp"), ("best", "", None)])
        await self.manager.stop_gateway(first["id"])
        self.assertEqual(self.client.removed, ["owned-1"])
        self.assertEqual((await self.manager.list_gateways())[1]["status"], "running")
        deleted = await self.manager.delete_gateway(first["id"])
        self.assertEqual(deleted["status"], "deleted")
        self.assertEqual([g["id"] for g in await self.manager.list_gateways()], [second["id"]])
        with self.assertRaises(KeyError):
            await self.manager.start_gateway(first["id"])

    async def test_ac1_bind_before_remote_and_retry_after_bind_failure(self):
        FakeRelay.busy.add(("127.0.0.1", 19080))
        gateway = await self.create()
        failed = await self.manager.start_gateway(gateway["id"])
        self.assertEqual(failed["status"], "error")
        self.assertIn("bind", failed["last_error"])
        self.assertEqual(self.client.created, [])
        FakeRelay.busy.clear()
        self.client.before_create = lambda: self.assertIn(("127.0.0.1", 19080), FakeRelay.busy)
        self.assertEqual((await self.manager.start_gateway(gateway["id"]))["status"], "running")

    async def test_ac2_duplicate_start_and_duplicate_port(self):
        gateway = await self.create()
        await asyncio.gather(*(self.manager.start_gateway(gateway["id"]) for _ in range(8)))
        self.assertEqual(len(self.client.created), 1)
        with self.assertRaises(ValueError):
            await self.create()

    async def test_ac6_validation(self):
        for data in [dict(port=0), dict(port=True), dict(port=65536), dict(port="8080"),
                     dict(host="example.com"),
                     dict(host="192.168.1.1", username="user", password="test-fake-password"),
                     dict(mode="country", country_code="JP"), dict(mode="city"),
                     dict(interval=4), dict(interval=3601), dict(interval=float("nan")),
                     dict(failures_before_rotate=0), dict(enabled="yes"),
                     dict(unknown="field"), dict(username="only")]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                await self.manager.create_gateway(dict(port=19080, **{k: v for k, v in data.items() if k != "port"})
                                                  if "port" not in data else data)
        self.assertEqual(await self.manager.list_gateways(), [])

    async def test_ac2_running_port_update_transaction(self):
        gateway = await self.running()
        old = FakeRelay.instances[-1]
        FakeRelay.busy.add(("127.0.0.1", 19081))
        failed = await self.manager.update_gateway(gateway["id"], dict(port=19081))
        self.assertEqual(failed["port"], 19080)
        self.assertEqual(failed["status"], "running")
        self.assertFalse(old.closed)
        self.assertEqual(old.get_upstream()["client_id"], "owned-1")
        FakeRelay.busy.remove(("127.0.0.1", 19081))
        changed = await self.manager.update_gateway(gateway["id"], dict(port=19081))
        self.assertEqual(changed["port"], 19081)
        self.assertTrue(old.closed)
        self.assertEqual(len(self.client.created), 1)

    async def test_ac3_candidate_verified_before_swap_and_old_drains(self):
        gateway = await self.running()
        relay = FakeRelay.instances[-1]
        entered, release = asyncio.Event(), asyncio.Event()

        async def blocking_probe(upstream):
            entered.set()
            await release.wait()
            return GOOD.copy()

        self.probe.side_effect = blocking_probe
        rotate = asyncio.create_task(self.manager.rotate_gateway(gateway["id"]))
        await entered.wait()
        self.assertEqual(relay.get_upstream()["client_id"], "owned-1")
        release.set()
        result = await rotate
        self.assertEqual(result["rotations"], 1)
        self.assertEqual(relay.get_upstream()["client_id"], "owned-2")
        self.assertNotIn("owned-1", self.client.removed)
        await self.manager._cleanup_due()
        self.assertNotIn("owned-1", self.client.removed)
        with patch.object(self.manager, "_clock", return_value=self.manager._clock() + 31):
            await self.manager._cleanup_due()
        self.assertIn("owned-1", self.client.removed)

    async def test_ac3_bad_candidate_keeps_current_and_cleans_owned(self):
        gateway = await self.running()
        self.probe.return_value = BAD.copy()
        result = await self.manager.rotate_gateway(gateway["id"])
        self.assertEqual(result["rotations"], 0)
        self.assertEqual(FakeRelay.instances[0].get_upstream()["client_id"], "owned-1")
        self.assertEqual(self.client.removed, ["owned-2"])
        self.assertNotIn("secret", json.dumps(result))
        self.assertNotIn("secret", json.dumps(self.manager.events()))

    async def test_ac3_repeated_failure_has_backoff_and_no_country_fallback(self):
        gateway = await self.create(mode="country", country_code="JP", location_id="loc-jp")
        self.client.create_error = True
        await self.manager.start_gateway(gateway["id"])
        for _ in range(20):
            await self.manager.start_gateway(gateway["id"])
            await self.manager.rotate_gateway(gateway["id"])
        self.assertEqual(self.client.created, [("country", "JP", "loc-jp")])
        self.assertNotIn("secret", self.path.read_text())

    async def test_ac3_failed_creation_retries_after_backoff(self):
        gateway = await self.create()
        self.client.create_error = True
        await self.manager.start_gateway(gateway["id"])
        self.client.create_error = False
        with patch.object(self.manager, "_clock", return_value=self.manager._clock() + 301):
            self.assertEqual((await self.manager.start_gateway(gateway["id"]))["status"], "running")

    async def test_ac3_health_threshold_and_control_offline(self):
        gateway = await self.running()
        self.probe.return_value = BAD.copy()
        self.control.return_value = False
        for _ in range(4):
            result = await self.manager.check_gateway(gateway["id"])
        self.assertEqual(len(self.client.created), 1)
        self.assertEqual(result["status"], "offline")
        self.assertGreaterEqual(result["failures"], 3)
        self.assertEqual(self.control.await_count, 1)

    async def test_ac3_three_failures_rotate_and_success_resets(self):
        gateway = await self.running()
        self.probe.side_effect = [BAD.copy(), BAD.copy(), BAD.copy(), GOOD.copy()]
        await self.manager.check_gateway(gateway["id"])
        await self.manager.check_gateway(gateway["id"])
        self.assertEqual(len(self.client.created), 1)
        result = await self.manager.check_gateway(gateway["id"])
        self.assertEqual(len(self.client.created), 2)
        self.assertEqual(result["failures"], 0)
        self.assertEqual(result["status"], "running")

    async def test_ac3_cleanup_failure_is_persisted_and_blocks_growth(self):
        gateway = await self.running()
        self.client.remove_error = True
        self.probe.return_value = BAD.copy()
        await self.manager.rotate_gateway(gateway["id"])
        self.assertEqual(len(json.loads(self.path.read_text())["owned"]), 2)
        with patch.object(self.manager, "_clock", return_value=self.manager._clock() + 301):
            for _ in range(5):
                await self.manager.rotate_gateway(gateway["id"])
        self.assertEqual(len(self.client.created), 2)
        self.client.remove_error = False
        with patch.object(self.manager, "_clock", return_value=self.manager._clock() + 601):
            await self.manager._cleanup_due()
        self.assertEqual(len(json.loads(self.path.read_text())["owned"]), 1)

    async def test_ac3_stop_during_candidate_never_resurrects(self):
        gateway = await self.running()
        entered, release = asyncio.Event(), asyncio.Event()

        async def blocking_probe(upstream):
            entered.set()
            await release.wait()
            return GOOD.copy()

        self.probe.side_effect = blocking_probe
        rotate = asyncio.create_task(self.manager.rotate_gateway(gateway["id"]))
        await entered.wait()
        stop = asyncio.create_task(self.manager.stop_gateway(gateway["id"]))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(rotate, stop)
        result = (await self.manager.list_gateways())[0]
        self.assertFalse(result["enabled"])
        self.assertEqual(result["status"], "stopped")
        self.assertCountEqual(self.client.removed, ["owned-1", "owned-2"])
        self.assertTrue(FakeRelay.instances[0].closed)

    async def test_ac6_close_preserves_enabled_and_restart_cleans_owned_first(self):
        gateway = await self.running()
        self.client.remove_error = True
        await self.manager.close()
        state = json.loads(self.path.read_text())
        self.assertTrue(state["gateways"][0]["enabled"])
        self.assertEqual(len(state["owned"]), 1)
        self.client.remove_error = False
        self.client.before_create = lambda: self.assertEqual(self.client.removed[-1], "owned-1")
        self.manager = self.make_manager()
        await self.manager.initialize()
        result = (await self.manager.list_gateways())[0]
        self.assertEqual(result["id"], gateway["id"])
        self.assertEqual(result["status"], "running")

    async def test_ac6_atomic_state_and_public_data_never_contain_credentials(self):
        gateway = await self.running(username="local", password="test-fake-local-password")
        persisted = self.path.read_text()
        for secret in ["test-fake", "upstream.invalid", "standard_http", "auth_token", "password"]:
            self.assertNotIn(secret, persisted)
            self.assertNotIn(secret, json.dumps(gateway))
        self.assertEqual(set(gateway), {
            "id", "name", "host", "port", "mode", "country_code", "location_id", "enabled",
            "auto_heal", "interval", "failures_before_rotate", "status", "exit_ip", "latency_ms",
            "last_check", "failures", "rotations", "last_error", "connections", "created_at"})
        before = self.path.read_bytes()
        with patch("core.gateway.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                await self.manager.update_gateway(gateway["id"], dict(name="not committed"))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual((await self.manager.list_gateways())[0]["name"], gateway["name"])
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    async def test_ac6_events_are_ordered_copies_and_threaded_client(self):
        await self.running()
        events = self.manager.events()
        self.assertTrue(events)
        self.assertEqual(set(events[0]), {"id", "time", "gateway_id", "level", "message"})
        self.assertEqual(self.manager.events(after=events[-1]["id"]), [])
        events[0]["message"] = "tampered"
        self.assertNotEqual(self.manager.events()[0]["message"], "tampered")
        self.assertTrue(all(t != threading.get_ident() for t in self.client.threads))
        json.dumps(await self.manager.list_gateways(), allow_nan=False)

    async def test_ac6_invalid_state_does_not_overwrite_or_start(self):
        await self.manager.close()
        self.path.write_text('{"version": 1, "gateways": "invalid", "owned": []}')
        self.manager = self.make_manager()
        with self.assertRaises(ValueError):
            await self.manager.initialize()
        self.assertEqual(self.client.created, [])
        self.assertIn("invalid", self.path.read_text())

    async def test_ac6_has_owned_clients_includes_failed_cleanup(self):
        self.assertFalse(self.manager.has_owned_clients)
        gateway = await self.running()
        self.assertTrue(self.manager.has_owned_clients)
        self.client.remove_error = True
        await self.manager.stop_gateway(gateway["id"])
        self.assertTrue(self.manager.has_owned_clients)
        self.client.remove_error = False
        with patch.object(self.manager, "_clock", return_value=self.manager._clock() + 301):
            await self.manager._cleanup_due()
        self.assertFalse(self.manager.has_owned_clients)

    async def test_ac3_probe_timeout_exceeds_parent_budget(self):
        self.assertGreaterEqual(PROBE_TIMEOUT, 35)

    async def test_ac3_creation_errors_include_only_http_status(self):
        error = RuntimeError("test-fake-key-secret remote response body")
        error.status = 429
        self.client.create_exception = error
        result = await self.running()
        self.assertIn("429", result["last_error"])
        self.assertIn("429", json.dumps(self.manager.events()))
        self.assertNotIn("secret", json.dumps(self.manager.events()))

    async def test_ac3_explicit_not_found_removal_releases_journal(self):
        for status in (404, 410):
            gateway = await self.running()
            error = RuntimeError("test-fake-key-secret")
            error.status = status
            self.client.remove_exception = error
            await self.manager.delete_gateway(gateway["id"])
            self.assertFalse(self.manager.has_owned_clients)
            self.assertEqual(json.loads(self.path.read_text())["owned"], [])

    async def test_ac3_message_containing_404_is_not_success(self):
        gateway = await self.running()
        self.client.remove_exception = RuntimeError("404 string, not HTTP status")
        await self.manager.stop_gateway(gateway["id"])
        self.assertTrue(self.manager.has_owned_clients)

    async def test_ac2_concurrent_rotations_coalesce(self):
        gateway = await self.running()
        results = await asyncio.gather(*(self.manager.rotate_gateway(gateway["id"]) for _ in range(8)))
        self.assertEqual(len(self.client.created), 2)
        self.assertTrue(all(result["rotations"] == 1 for result in results))

    async def test_ac2_country_change_failure_rolls_back_then_can_retry(self):
        gateway = await self.running(mode="country", country_code="JP", location_id="loc-jp")
        self.probe.return_value = BAD.copy()
        result = await self.manager.update_gateway(gateway["id"], dict(country_code="US", location_id="loc-us"))
        self.assertEqual((result["country_code"], result["location_id"]), ("JP", "loc-jp"))
        self.assertEqual(self.client.created[-1], ("country", "US", "loc-us"))
        self.probe.return_value = GOOD.copy()
        with patch.object(self.manager, "_clock", return_value=self.manager._clock() + 301):
            result = await self.manager.update_gateway(gateway["id"], dict(country_code="US", location_id="loc-us"))
        self.assertEqual((result["country_code"], result["location_id"]), ("US", "loc-us"))
        self.assertEqual(result["rotations"], 1)

    async def test_ac2_port_and_country_failure_preserves_listener_and_config(self):
        gateway = await self.running()
        old = FakeRelay.instances[0]
        self.probe.return_value = BAD.copy()
        result = await self.manager.update_gateway(gateway["id"], dict(
            port=19081, mode="country", country_code="US", location_id="loc-us"))
        self.assertEqual((result["port"], result["mode"]), (19080, "best"))
        self.assertFalse(old.closed)
        self.assertTrue(FakeRelay.instances[-1].closed)
        self.assertEqual(self.client.removed, ["owned-2"])
        state = json.loads(self.path.read_text())
        self.assertEqual(state["gateways"][0]["port"], 19080)

    async def test_ac6_port_transaction_rolls_back_on_storage_error(self):
        gateway = await self.running()
        old = FakeRelay.instances[0]
        with patch("core.gateway.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                await self.manager.update_gateway(gateway["id"], dict(port=19081))
        self.assertEqual((await self.manager.list_gateways())[0]["port"], 19080)
        self.assertFalse(old.closed)
        self.assertTrue(FakeRelay.instances[-1].closed)

    async def test_ac3_cancelled_create_is_owned_and_close_waits_for_it(self):
        gateway = await self.create()
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()

        def blocking_create():
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(5):
                raise RuntimeError("fixture timed out")

        self.client.before_create = blocking_create
        start = asyncio.create_task(self.manager.start_gateway(gateway["id"]))
        await asyncio.wait_for(entered.wait(), 2)
        try:
            self.assertTrue(self.manager.has_owned_clients)
            start.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await start
            closing = asyncio.create_task(self.manager.close())
            await asyncio.sleep(0)
            release.set()
            await asyncio.wait_for(closing, 3)
        finally:
            release.set()
            await asyncio.gather(start, return_exceptions=True)
        self.assertEqual(self.client.removed, ["owned-1"])
        self.assertFalse(self.manager.has_owned_clients)
        self.assertTrue(json.loads(self.path.read_text())["gateways"][0]["enabled"])

    async def test_ac3_delete_during_start_cleans_candidate_and_never_revives(self):
        gateway = await self.create()
        entered, release = asyncio.Event(), asyncio.Event()

        async def blocking_probe(upstream):
            entered.set()
            await release.wait()
            return GOOD.copy()

        self.probe.side_effect = blocking_probe
        start = asyncio.create_task(self.manager.start_gateway(gateway["id"]))
        await asyncio.wait_for(entered.wait(), 2)
        deletion = asyncio.create_task(self.manager.delete_gateway(gateway["id"]))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(start, deletion)
        self.assertEqual(await self.manager.list_gateways(), [])
        self.assertEqual(self.client.removed, ["owned-1"])
        self.assertFalse(self.manager.has_owned_clients)

    async def test_ac3_hourly_limit_counts_creation_attempts(self):
        gateway = await self.running()
        clock = self.manager._clock()
        with patch.object(self.manager, "_clock") as now:
            for index in range(1, 20):
                now.return_value = clock + index * 31
                await self.manager._cleanup_due()
                result = await self.manager.rotate_gateway(gateway["id"])
        self.assertEqual(len(self.client.created), 12)
        self.assertEqual(result["last_error"], "hourly_creation_limit")

    async def test_ac3_global_auth_failure_blocks_other_gateway_creation(self):
        first = await self.create()
        second = await self.manager.create_gateway(dict(port=19081))
        self.client.create_error = True
        await asyncio.gather(self.manager.start_gateway(first["id"]),
                             self.manager.start_gateway(second["id"]))
        self.assertEqual(len(self.client.created), 1)

    async def test_ac3_offline_guard_is_shared_across_gateways(self):
        first = await self.running()
        second = await self.manager.create_gateway(dict(port=19081, enabled=True))
        self.probe.return_value = BAD.copy()
        self.control.return_value = False
        for _ in range(3):
            await asyncio.gather(self.manager.check_gateway(first["id"]),
                                 self.manager.check_gateway(second["id"]))
        self.assertEqual(len(self.client.created), 2)
        self.assertEqual(self.control.await_count, 1)
        self.assertTrue(all(g["status"] == "offline" for g in await self.manager.list_gateways()))

    async def test_ac3_auto_heal_false_still_checks_without_creating(self):
        gateway = await self.running(auto_heal=False)
        self.probe.return_value = BAD.copy()
        for _ in range(5):
            result = await self.manager.check_gateway(gateway["id"])
        self.assertEqual(result["failures"], 5)
        self.assertEqual(len(self.client.created), 1)

    async def test_ac3_background_worker_probes_due_gateways(self):
        gateway = await self.running()
        checked = asyncio.Event()

        async def record_probe(upstream):
            checked.set()
            return GOOD.copy()

        self.probe.side_effect = record_probe
        with patch.object(self.manager, "_clock", return_value=self.manager._clock() + 31):
            await asyncio.wait_for(checked.wait(), 3)
        self.assertEqual((await self.manager.list_gateways())[0]["id"], gateway["id"])

    async def test_ac6_initialize_is_idempotent(self):
        await self.running()
        await asyncio.gather(self.manager.initialize(), self.manager.initialize())
        self.assertEqual(len(self.client.created), 1)
        self.assertEqual(self.client.removed, [])

    async def test_ac6_other_event_loop_is_rejected(self):
        def other_loop():
            return asyncio.run(self.manager.list_gateways())
        with self.assertRaises(RuntimeError):
            await asyncio.to_thread(other_loop)

    async def test_ac6_create_storage_failure_does_not_add_gateway(self):
        with patch("core.gateway.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                await self.create()
        self.assertEqual(await self.manager.list_gateways(), [])
        self.assertEqual(self.client.created, [])

    async def test_ac6_probe_nonfinite_result_is_not_public_json(self):
        self.probe.return_value = dict(GOOD, latency_ms=float("inf"))
        result = await self.running()
        self.assertEqual(result["status"], "error")
        json.dumps(result, allow_nan=False)
        self.assertEqual(self.client.removed, ["owned-1"])

    async def test_ac6_failed_orphan_cleanup_cannot_accumulate_new_ids(self):
        gateway = await self.running()
        self.client.remove_error = True
        await self.manager.delete_gateway(gateway["id"])
        await self.running()
        self.assertEqual(len(self.client.created), 1)

    async def test_ac6_candidate_journal_write_failure_closes_listener(self):
        gateway = await self.create()
        persist, writes = self.manager._persist, 0

        def fail_registration():
            nonlocal writes
            writes += 1
            if writes == 2:
                raise OSError("disk full")
            persist()

        with patch.object(self.manager, "_persist", side_effect=fail_registration):
            with self.assertRaises(OSError):
                await self.manager.start_gateway(gateway["id"])
        self.assertTrue(FakeRelay.instances[0].closed)
        self.assertEqual((await self.manager.list_gateways())[0]["status"], "error")
        self.assertEqual(self.client.removed, ["owned-1"])

    async def test_ac6_failed_disable_update_preserves_health_monitoring(self):
        gateway = await self.running()
        with patch("core.gateway.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                await self.manager.update_gateway(gateway["id"], dict(enabled=False))
        self.probe.return_value = BAD.copy()
        result = await self.manager.check_gateway(gateway["id"])
        self.assertTrue(result["enabled"])
        self.assertEqual(result["failures"], 1)

    async def test_ac6_failed_delete_is_visible_and_retryable(self):
        gateway = await self.running()
        with patch("core.gateway.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                await self.manager.delete_gateway(gateway["id"])
        self.assertEqual(len(await self.manager.list_gateways()), 1)
        result = await self.manager.delete_gateway(gateway["id"])
        self.assertEqual(result["status"], "deleted")

    async def test_ac6_cancelled_initialize_still_finishes_restoration(self):
        await self.running()
        await self.manager.close()
        self.manager = self.make_manager()
        entered, release = asyncio.Event(), asyncio.Event()

        async def blocking_probe(upstream):
            entered.set()
            await release.wait()
            return GOOD.copy()

        self.probe.side_effect = blocking_probe
        initializing = asyncio.create_task(self.manager.initialize())
        await asyncio.wait_for(entered.wait(), 2)
        initializing.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await initializing
        release.set()
        await self.manager.initialize()
        self.assertIsNotNone(self.manager._worker)
        self.assertEqual((await self.manager.list_gateways())[0]["status"], "running")

    async def test_review_authenticated_restart_fails_closed(self):
        row = await self.running(username="local", password="secret")
        await self.manager.close()
        self.manager = self.make_manager()
        await self.manager.initialize()
        current = (await self.manager.list_gateways())[0]
        self.assertEqual(current["last_error"], "local_credentials_required")
        self.assertNotIn(("127.0.0.1", 19080), FakeRelay.busy)
        self.assertEqual(len(self.client.created), 1)
        await self.manager.update_gateway(row["id"], {"username": "local", "password": "secret"})
        current = (await self.manager.list_gateways())[0]
        self.assertEqual(current["status"], "running")
        self.assertEqual(FakeRelay.instances[-1].password, "secret")

    async def test_review_new_listener_has_no_upstream_before_commit(self):
        row = await self.running(mode="country", country_code="US", location_id="us")
        entered, release = asyncio.Event(), asyncio.Event()
        async def paused_probe(upstream):
            entered.set()
            await release.wait()
            return GOOD.copy()
        self.manager._probe = paused_probe
        operation = asyncio.create_task(self.manager.update_gateway(row["id"],
            dict(port=19081, country_code="JP", location_id="jp")))
        await entered.wait()
        self.assertIsNone(FakeRelay.instances[-1].get_upstream())
        self.assertEqual(FakeRelay.instances[-2].get_upstream()["client_id"], "owned-1")
        release.set()
        await operation
        self.assertEqual(FakeRelay.instances[-1].get_upstream()["client_id"], "owned-2")

    async def test_review_stop_write_failure_keeps_consistent_running_state(self):
        row = await self.running()
        with patch("core.gateway.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                await self.manager.stop_gateway(row["id"])
        current = (await self.manager.list_gateways())[0]
        self.assertTrue(current["enabled"])
        self.assertEqual(current["status"], "running")
        self.assertTrue(self.manager._gateways[row["id"]].wanted)
        self.assertIn(("127.0.0.1", 19080), FakeRelay.busy)
        calls = self.probe.await_count
        await self.manager.check_gateway(row["id"])
        self.assertGreater(self.probe.await_count, calls)


    async def test_dual_stack_gateway_creation_and_reservation(self):
        # Dual-stack host normalization (::, empty, dual)
        gw1 = await self.manager.create_gateway(dict(name="Dual1", host="::", port=19090, enabled=False))
        self.assertEqual(gw1["host"], "::")
        # Same port on wildcard or loopback collides
        with self.assertRaises(ValueError):
            await self.manager.create_gateway(dict(name="Dual2", host="::", port=19090, enabled=False))
        with self.assertRaises(ValueError):
            await self.manager.create_gateway(dict(name="Loop", host="127.0.0.1", port=19090, enabled=False))


if __name__ == "__main__":
    unittest.main()
