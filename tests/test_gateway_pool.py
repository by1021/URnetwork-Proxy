"""Pool lifecycle contracts; fixtures never connect to an external account."""
import asyncio
import json
import socket
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, patch

from core.client import ApiError
from core.gateway import GatewayManager, _validate
from core.multiplexer import MixedProxyServer
from core.protocol import close_writer
from core.tls_helper import TLSConfigurationError
from test_gateway_country import FixtureRelay, sample


class PoolClient:
    def __init__(self):
        self.creates, self.removed, self.rotated = [], [], []
        self.block_after = None
        self.entered, self.release = threading.Event(), threading.Event()
        self.capabilities = {"active_limit": 1000, "creates_per_minute": 20, "is_pro": True, "unique_exit": True}
        self.error = None

    def get_proxy_capabilities(self):
        return dict(self.capabilities)

    def create_proxy(self, **kwargs):
        if self.error:
            raise self.error
        self.creates.append(kwargs)
        if self.block_after and len(self.creates) >= self.block_after:
            self.entered.set()
            self.release.wait(3)
        return {"client_id": f"pool-{len(self.creates)}", "host": "127.0.0.1", "http_port": 1,
                "socks_port": 2, "auth_token": "test-fake-key-pool", "password": "test-fake-password"}

    def remove_proxy(self, cid):
        self.removed.append(cid)
        return {"ok": True}


class PoolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.client, self.failed = PoolClient(), set()

        async def probe(upstream):
            cid = upstream["client_id"]
            if cid in self.failed:
                return {"ok": False, "http_ok": False, "socks_ok": False}
            number = int(cid.split("-")[-1])
            return sample("US", f"203.0.113.{number}", city="Fixture City", region="Fixture Region")

        self.manager = GatewayManager(self.client, Path(self.tmp.name) / "gateways.json",
                                      probe=probe, relay_factory=FixtureRelay)
        await self.manager.initialize()

    async def asyncTearDown(self):
        self.client.release.set()
        await self.manager.close()
        self.tmp.cleanup()

    async def create(self, **kwargs):
        return await self.manager.create_gateway({"name": "Pool fixture", "enabled": True,
                                                  "residential_only": False, **kwargs})

    async def filled(self, gid, size=3):
        for _ in range(100):
            row = next(g for g in await self.manager.list_gateways() if g["id"] == gid)
            if row.get("pool_ready") == size:
                return row
            await asyncio.sleep(.01)
        self.fail("pool did not reach expected ready count")

    async def test_defaults_and_first_ready_then_three_without_secret_snapshot(self):
        self.assertEqual(_validate({})["pool_size"], 3)
        row = await self.create()
        self.assertEqual(row["status"], "running")
        self.assertGreaterEqual(row["pool_ready"], 1)
        row = await self.filled(row["id"])
        self.assertEqual(row["pool_status"], "ready")
        self.assertEqual(len(row["upstreams"]), 3)
        self.assertNotIn("test-fake", json.dumps(row))
        self.assertNotIn("auth_token", json.dumps(row))
        self.assertEqual({c["location_id"] for c in self.client.creates}, {None})
        self.assertEqual(row["exit_city"], "Fixture City")

    async def test_two_failed_primaries_choose_ready_backup_without_waiting_create(self):
        row = await self.create()
        row = await self.filled(row["id"])
        runtime = self.manager._gateways[row["id"]]
        count = len(self.client.creates)
        self.client.block_after = 4
        with patch.object(self.manager, "_control_available", new=AsyncMock(return_value=True)):
            self.failed.add("pool-1")
            row = await self.manager.check_gateway(row["id"])
            self.assertEqual(row["active_client_id"], "pool-2")
            self.failed.add("pool-2")
            row = await self.manager.check_gateway(row["id"])
            self.assertEqual(row["active_client_id"], "pool-3")
            self.assertEqual(row["status"], "running")
        self.assertEqual(runtime.upstream["client_id"], "pool-3")
        self.assertLessEqual(len(self.client.creates), count + 1)

    async def test_stale_node_feedback_and_destination_error_do_not_eject_successor(self):
        row = await self.create()
        await self.filled(row["id"])
        runtime = self.manager._gateways[row["id"]]
        epoch = runtime.generation
        self.manager._on_upstream_result(runtime, "pool-1", False, "dial", epoch)
        self.assertEqual(runtime.upstream["client_id"], "pool-2")
        self.manager._on_upstream_result(runtime, "pool-1", False, "dial", epoch)
        self.manager._on_upstream_result(runtime, "pool-2", False, "destination", epoch)
        self.manager._on_upstream_result(runtime, "pool-2", False, "dial", epoch - 1)
        self.assertEqual(runtime.upstream["client_id"], "pool-2")

    async def test_slow_background_create_does_not_hold_failover(self):
        self.client.block_after = 3
        row = await self.create()
        for _ in range(100):
            if self.client.entered.is_set():
                break
            await asyncio.sleep(.01)
        self.assertTrue(self.client.entered.is_set())
        runtime = self.manager._gateways[row["id"]]
        self.manager._on_upstream_result(runtime, "pool-1", False, "dial", runtime.generation)
        self.assertEqual(runtime.upstream["client_id"], "pool-2")

    async def test_resize_and_delete_clean_all_owned_not_foreign(self):
        row = await self.create()
        await self.filled(row["id"])
        small = await self.manager.update_gateway(row["id"], {"pool_size": 1})
        self.assertEqual(small["active_client_id"], "pool-1")
        self.assertEqual(small["pool_ready"], 1)
        await self.manager.delete_gateway(row["id"])
        await self.manager._cleanup_due()
        self.assertEqual(set(self.client.removed), {"pool-1", "pool-2", "pool-3"})

    async def test_pool_limit_error_keeps_active_service(self):
        self.client.capabilities["active_limit"] = 1
        row = await self.create()
        await asyncio.sleep(.05)
        row = (await self.manager.list_gateways())[0]
        self.assertEqual(row["status"], "running")
        self.assertEqual(row["pool_ready"], 1)
        self.assertEqual(row["last_error"], "")
        self.assertEqual(row["pool_error"], "account_proxy_limit")
        self.assertEqual(len(self.client.creates), 1)

    async def test_region_and_city_use_exact_id_and_country_constraint(self):
        for mode in ("region", "city"):
            row = await self.create(mode=mode, country_code="US", location_id=f"exact-{mode}",
                                    location_name="Fixture", region="Fixture Region", city="Fixture City")
            await self.filled(row["id"])
            requests = [c for c in self.client.creates if c.get("gateway_id") == row["id"]]
            self.assertEqual({c["location_id"] for c in requests}, {f"exact-{mode}"})
            self.assertEqual({c["target"] for c in requests}, {"US"})
            await self.manager.delete_gateway(row["id"])
            await self.manager._cleanup_due()

    async def test_binding_commit_retires_entire_old_pool_and_refills_exact_id(self):
        row = await self.create()
        await self.filled(row["id"])
        changed = await self.manager.update_gateway(row["id"], {
            "mode": "city", "country_code": "US", "location_id": "new-exact-city",
            "city": "Requested City", "location_name": "Requested City"})
        self.assertEqual(changed["location_id"], "new-exact-city")
        runtime = self.manager._gateways[row["id"]]
        self.assertFalse(set(runtime.members) & {"pool-1", "pool-2", "pool-3"})
        await self.manager._cleanup_due()
        self.manager._ensure_fill(runtime, runtime.generation, force=True)
        ready = await self.filled(row["id"])
        self.assertEqual(ready["city"], "Requested City")
        self.assertEqual(ready["exit_city"], "Fixture City")
        self.assertTrue(all(c["location_id"] == "new-exact-city" for c in self.client.creates[3:]))

    async def test_failed_binding_keeps_all_old_members_and_auth(self):
        row = await self.create()
        await self.filled(row["id"])
        runtime = self.manager._gateways[row["id"]]
        before = set(runtime.members)
        changed = await self.manager.update_gateway(row["id"], {
            "mode": "country", "country_code": "VN", "location_id": "exact-vn",
            "username": "test-fake-user", "password": "test-fake-pass"})
        self.assertEqual(changed["mode"], "best")
        self.assertEqual(set(runtime.members), before)
        self.assertEqual(changed["username"], "")
        self.assertEqual(changed["active_client_id"], "pool-1")

    async def test_stop_discards_late_background_create_and_clears_members(self):
        self.client.block_after = 3
        row = await self.create()
        for _ in range(100):
            if self.client.entered.is_set():
                break
            await asyncio.sleep(.01)
        stopped = await self.manager.stop_gateway(row["id"])
        self.assertEqual(stopped["pool_ready"], 0)
        self.assertEqual(stopped["active_client_id"], "")
        self.client.release.set()
        runtime = self.manager._gateways[row["id"]]
        await runtime.filler
        await self.manager._cleanup_due()
        self.assertEqual(set(self.client.removed), {"pool-1", "pool-2", "pool-3"})
        self.assertFalse(runtime.members)
        self.assertEqual(runtime.status, "stopped")

    async def test_shrink_invalidates_late_create_and_increase_refills(self):
        self.client.block_after = 3
        row = await self.create()
        for _ in range(100):
            if self.client.entered.is_set():
                break
            await asyncio.sleep(.01)
        runtime = self.manager._gateways[row["id"]]
        filler = runtime.filler
        small = await self.manager.update_gateway(row["id"], {"pool_size": 1})
        self.assertEqual(small["pool_ready"], 1)
        self.client.release.set()
        await filler
        self.assertEqual(set(runtime.members), {"pool-1"})
        self.assertIn("pool-3", self.client.removed)
        await self.manager.update_gateway(row["id"], {"pool_size": 3})
        await self.filled(row["id"])

    async def test_late_candidate_probe_after_stop_does_not_restore_service(self):
        entered, release = asyncio.Event(), asyncio.Event()
        original_probe = self.manager._probe

        async def blocking_probe(upstream):
            if upstream["client_id"] == "pool-2":
                entered.set()
                await release.wait()
            return await original_probe(upstream)

        self.manager._probe = blocking_probe
        row = await self.create()
        await entered.wait()
        await self.manager.stop_gateway(row["id"])
        release.set()
        runtime = self.manager._gateways[row["id"]]
        await runtime.filler
        self.assertEqual(runtime.status, "stopped")
        self.assertIsNone(runtime.upstream)
        self.assertFalse(runtime.members)
        self.assertIn("pool-2", self.client.removed)

    async def test_late_primary_probe_does_not_overwrite_live_successor(self):
        row = await self.create()
        await self.filled(row["id"])
        entered, release = asyncio.Event(), asyncio.Event()
        original_probe = self.manager._probe

        async def blocking_probe(upstream):
            if upstream["client_id"] == "pool-1":
                entered.set()
                await release.wait()
            return await original_probe(upstream)

        self.manager._probe = blocking_probe
        checking = asyncio.create_task(self.manager.check_gateway(row["id"]))
        await entered.wait()
        runtime = self.manager._gateways[row["id"]]
        self.manager._on_upstream_result(runtime, "pool-1", False, "dial", runtime.generation)
        self.assertEqual(runtime.upstream["client_id"], "pool-2")
        release.set()
        await checking
        self.assertEqual(runtime.upstream["client_id"], "pool-2")
        self.assertEqual(runtime.exit_ip, "203.0.113.2")

    async def test_single_member_feedback_keeps_original_probe_threshold(self):
        row = await self.create(pool_size=1)
        runtime = self.manager._gateways[row["id"]]
        self.manager._on_upstream_result(runtime, "pool-1", False, "dial", runtime.generation)
        self.assertEqual(runtime.upstream["client_id"], "pool-1")
        self.failed.add("pool-1")
        with patch.object(self.manager, "_control_available", new=AsyncMock(return_value=True)):
            await self.manager.check_gateway(row["id"])
            await self.manager.check_gateway(row["id"])
            self.assertEqual(runtime.upstream["client_id"], "pool-1")
            await self.manager.check_gateway(row["id"])
            self.assertEqual(runtime.upstream["client_id"], "pool-2")

    async def test_create_failure_after_stop_does_not_write_backoff_to_stopped(self):
        original_create = self.client.create_proxy
        entered, release = threading.Event(), threading.Event()

        def create(**kwargs):
            if self.client.creates:
                entered.set()
                release.wait(3)
                raise RuntimeError("test-fake-key-failure")
            return original_create(**kwargs)

        self.client.create_proxy = create
        row = await self.create()
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(.01)
        self.assertTrue(entered.is_set())
        await self.manager.stop_gateway(row["id"])
        release.set()
        runtime = self.manager._gateways[row["id"]]
        await runtime.filler
        self.assertEqual(runtime.status, "stopped")
        self.assertEqual(runtime.pool_error, "")
        self.assertNotIn("test-fake-key-failure", json.dumps(self.manager.events()))

    async def test_late_rate_limit_after_stop_preserves_account_deadline_only(self):
        original_create = self.client.create_proxy
        entered, release = threading.Event(), threading.Event()

        class RateLimited(RuntimeError):
            code, retry_after, status = "rate_limited", 120, 429

        def create(**kwargs):
            if self.client.creates:
                entered.set()
                release.wait(3)
                raise RateLimited("test-fake-key-late-rate-limit")
            return original_create(**kwargs)

        self.client.create_proxy = create
        row = await self.create()
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(.01)
        self.assertTrue(entered.is_set())
        runtime = self.manager._gateways[row["id"]]
        await self.manager.stop_gateway(row["id"])
        release.set()
        await runtime.filler
        deadline = self.manager._global_next_create
        self.assertGreaterEqual(deadline, self.manager._clock() + 119)
        self.assertEqual(runtime.status, "stopped")
        self.assertEqual(runtime.pool_error, "")
        self.client.create_proxy = original_create
        now = self.manager._clock()
        self.manager._clock = lambda: now + 11
        other = await self.create(port=31001, pool_size=1)
        self.assertNotEqual(other["status"], "running")
        self.assertGreaterEqual(self.manager._global_next_create, deadline)
        self.assertEqual(len(self.client.creates), 1)
        self.assertNotIn("test-fake-key-late-rate-limit", json.dumps(self.manager.events()))

    async def test_http_retry_after_without_known_code_limits_entire_account(self):
        primary = await self.create(pool_size=1)
        now = self.manager._clock()
        for index, (status, code) in enumerate(((429, ""), (429, "rejected"),
                                               (429, "test-fake-unknown-code"), (503, ""))):
            with self.subTest(status=status, code=code):
                self.manager._clock = lambda: now
                error = ApiError(status)
                error.code, error.retry_after = code, 120
                self.client.error = error
                await self.create(port=31001 + index * 2, pool_size=1)
                deadline = self.manager._global_next_create
                self.assertGreaterEqual(deadline, now + 120)
                self.client.error = None
                self.manager._clock = lambda: now + 11
                other = await self.create(port=31002 + index * 2, pool_size=1)
                self.assertNotEqual(other["status"], "running")
                self.assertGreaterEqual(self.manager._global_next_create, deadline)
                self.assertEqual(len(self.client.creates), 1)
                row = next(g for g in await self.manager.list_gateways() if g["id"] == primary["id"])
                self.assertEqual(row["status"], "running")
                self.assertNotIn("test-fake-unknown-code", json.dumps(self.manager.events()))
            now = self.manager._global_next_create + 1

    async def test_http_retry_after_is_bounded_and_503_without_header_keeps_general_backoff(self):
        now = self.manager._clock()
        cases = ((503, 0, 5, 10), (429, 0, 60, 60), (429, float("nan"), 60, 60),
                 (429, 6000, 3600, 3600), (503, 30, 60, 60))
        for index, (status, retry, minimum, maximum) in enumerate(cases):
            with self.subTest(status=status, retry=retry):
                self.manager._clock = lambda: now
                self.manager._global_attempts = 0
                error = ApiError(status)
                error.code, error.retry_after = "", retry
                self.client.error = error
                await self.create(port=31000 + index, pool_size=1)
                delay = self.manager._global_next_create - now
                self.assertGreaterEqual(delay, minimum)
                self.assertLessEqual(delay, maximum)
                self.assertEqual(len(self.client.creates), 0)
            now = self.manager._global_next_create + 1

    async def test_late_uncoded_http_retry_after_after_stop_is_account_wide(self):
        original_create = self.client.create_proxy
        entered, release = threading.Event(), threading.Event()
        error = ApiError(429)
        error.code, error.retry_after = "", 120

        def create(**kwargs):
            if self.client.creates:
                entered.set()
                release.wait(3)
                raise error
            return original_create(**kwargs)

        self.client.create_proxy = create
        row = await self.create()
        try:
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(.01)
            self.assertTrue(entered.is_set())
            runtime = self.manager._gateways[row["id"]]
            await self.manager.stop_gateway(row["id"])
            release.set()
            await runtime.filler
            deadline = self.manager._global_next_create
            self.assertGreaterEqual(deadline, self.manager._clock() + 119)
            self.assertEqual(runtime.status, "stopped")
            self.assertEqual(runtime.pool_error, "")
            self.client.create_proxy = original_create
            now = self.manager._clock()
            self.manager._clock = lambda: now + 11
            other = await self.create(port=31001, pool_size=1)
            self.assertNotEqual(other["status"], "running")
            self.assertGreaterEqual(self.manager._global_next_create, deadline)
            self.assertEqual(len(self.client.creates), 1)
        finally:
            release.set()

    async def test_uncoded_http_retry_after_never_shortens_newer_account_deadline(self):
        original_create = self.client.create_proxy
        entered, release = threading.Event(), threading.Event()
        error = ApiError(503)
        error.code, error.retry_after = "", 120

        def create(**kwargs):
            entered.set()
            release.wait(3)
            raise error

        self.client.create_proxy = create
        pending = asyncio.create_task(self.create(pool_size=1))
        try:
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(.01)
            self.assertTrue(entered.is_set())
            deadline = self.manager._clock() + 300
            self.manager._global_next_create = deadline
            release.set()
            await pending
            self.assertGreaterEqual(self.manager._global_next_create, deadline)
            self.client.create_proxy = original_create
            now = self.manager._clock()
            self.manager._clock = lambda: now + 121
            other = await self.create(port=31001, pool_size=1)
            self.assertNotEqual(other["status"], "running")
            self.assertEqual(len(self.client.creates), 0)
        finally:
            release.set()
            await pending

    async def test_runtime_create_typeerror_is_not_replayed(self):
        original_create, calls = self.client.create_proxy, []

        def create(**kwargs):
            calls.append(kwargs)
            if len(calls) > 1:
                raise TypeError("test-fake-key-runtime-error")
            return original_create(**kwargs)

        self.client.create_proxy = create
        row = await self.create()
        runtime = self.manager._gateways[row["id"]]
        await runtime.filler
        self.assertEqual(len(calls), 2)
        self.assertEqual(runtime.status, "running")
        self.assertEqual(runtime.pool_error, "remote_create_failed")

    async def test_unique_exit_rotates_after_grace_before_rebuilding(self):
        row = await self.create()
        await self.filled(row["id"])
        runtime = self.manager._gateways[row["id"]]
        member = runtime.members["pool-1"]
        member.upstream.update(unique_exit=True, exit={"replace_mode": "auto"})
        self.failed.add("pool-1")
        self.manager._on_upstream_result(runtime, "pool-1", False, "dial", runtime.generation)
        await runtime.filler
        member.failed_at -= 151
        member.next_repair = 0

        def rotate(cid):
            self.client.rotated.append(cid)
            self.failed.remove(cid)
            return {"ok": True}

        self.client.rotate_proxy_exit = rotate
        await self.manager._fill_pool(runtime, runtime.generation)
        self.assertEqual(self.client.rotated, ["pool-1"])
        self.assertEqual(len(self.client.creates), 3)
        self.assertEqual(member.state, "ready")
        self.assertEqual(runtime.upstream["client_id"], "pool-2")

    async def test_late_control_failure_after_stop_does_not_restore_backoff(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def control():
            entered.set()
            await release.wait()
            return False

        self.failed.add("pool-2")
        with patch.object(self.manager, "_control_available", new=control):
            row = await self.create()
            await entered.wait()
            runtime = self.manager._gateways[row["id"]]
            await self.manager.stop_gateway(row["id"])
            release.set()
            await runtime.filler
        self.assertEqual(runtime.status, "stopped")
        self.assertEqual(runtime.pool_error, "")

    async def test_no_failed_member_is_counted_ready_when_all_three_fail(self):
        row = await self.create(auto_heal=False)
        await self.filled(row["id"])
        self.failed.update({"pool-1", "pool-2", "pool-3"})
        with patch.object(self.manager, "_control_available", new=AsyncMock(return_value=True)):
            checked = await self.manager.check_gateway(row["id"])
        self.assertEqual(checked["pool_ready"], 0)
        self.assertEqual(checked["pool_status"], "depleted")
        self.assertEqual(checked["active_client_id"], "")

    async def test_unique_auto_repair_grace_keeps_backups_without_immediate_create(self):
        row = await self.create()
        await self.filled(row["id"])
        runtime = self.manager._gateways[row["id"]]
        runtime.members["pool-1"].upstream.update(unique_exit=True, exit={"replace_mode": "auto"})
        self.failed.add("pool-1")
        self.manager._on_upstream_result(runtime, "pool-1", False, "dial", runtime.generation)
        await runtime.filler
        self.assertEqual(runtime.upstream["client_id"], "pool-2")
        self.assertEqual(len(self.client.creates), 3)
        self.assertEqual(runtime.pool_error, "exit_replacing")

    def real_relay(self):
        self.manager._relay_factory = lambda *args, **kwargs: MixedProxyServer(
            *args, **kwargs, tls_config={"state_dir": Path(self.tmp.name)})
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            return reservation.getsockname()[1]

    async def assert_listening(self, row):
        self.assertEqual(row["status"], "running")
        self.assertEqual(row["pool_status"], "ready")
        runtime = self.manager._gateways[row["id"]]
        self.assertIsNotNone(runtime.relay)
        reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", row["port"]), 1)
        await close_writer(writer)

    async def test_background_recovery_after_create_failure_rebinds_real_listener(self):
        port = self.real_relay()
        self.client.error = RuntimeError("test-fake-key-create-failed")
        row = await self.create(port=port)
        runtime = self.manager._gateways[row["id"]]
        self.assertIsNone(runtime.relay)
        self.client.error = None
        runtime.next_create = self.manager._global_next_create = 0
        self.manager._ensure_fill(runtime, runtime.generation, force=True)
        await runtime.filler
        row = (await self.manager.list_gateways())[0]
        await self.assert_listening(row)

    async def test_background_bind_failure_never_claims_ready_or_allocates(self):
        port = self.real_relay()
        holder = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", port)
        try:
            row = await self.create(port=port)
            runtime = self.manager._gateways[row["id"]]
            self.assertEqual(row["last_error"], "local_bind_failed")
            self.manager._ensure_fill(runtime, runtime.generation, force=True)
            await runtime.filler
            failed = (await self.manager.list_gateways())[0]
            self.assertNotEqual(failed["status"], "running")
            self.assertNotEqual(failed["pool_status"], "ready")
            self.assertEqual(failed["pool_ready"], 0)
            self.assertEqual(len(self.client.creates), 0)
            with patch.object(self.manager, "_bind", new=AsyncMock(side_effect=TLSConfigurationError("test-fake-tls-failure"))):
                self.manager._ensure_fill(runtime, runtime.generation, force=True)
                await runtime.filler
                failed = (await self.manager.list_gateways())[0]
                self.assertEqual(failed["last_error"], "local_tls_failed")
                self.assertNotEqual(failed["status"], "running")
                self.assertEqual(len(self.client.creates), 0)
        finally:
            holder.close()
            await holder.wait_closed()
        runtime.next_create = self.manager._global_next_create = 0
        self.manager._ensure_fill(runtime, runtime.generation, force=True)
        await runtime.filler
        await self.assert_listening((await self.manager.list_gateways())[0])

    async def test_start_and_check_restore_listener_for_existing_verified_pool(self):
        row = await self.create(port=self.real_relay())
        await self.filled(row["id"])
        runtime = self.manager._gateways[row["id"]]
        for operation in (self.manager.start_gateway, self.manager.check_gateway):
            await runtime.relay.close()
            runtime.relay = None
            restored = await operation(row["id"])
            await self.assert_listening(restored)
            self.assertEqual(restored["active_client_id"], "pool-1")
        self.assertEqual(len(self.client.creates), 3)

    async def test_older_successful_probe_does_not_clear_newer_account_retry_after(self):
        entered, release = asyncio.Event(), asyncio.Event()
        original_probe = self.manager._probe

        async def probe(upstream):
            if upstream["client_id"] == "pool-1":
                entered.set()
                await release.wait()
            return await original_probe(upstream)

        class RateLimited(RuntimeError):
            code, retry_after, status = "rate_limited", 120, 429

        self.manager._probe = probe
        first = asyncio.create_task(self.create(pool_size=1))
        await entered.wait()
        self.client.error = RateLimited("test-fake-key-rate-limit")
        await self.create(port=31001, pool_size=1)
        deadline = self.manager._global_next_create
        self.assertGreaterEqual(deadline, self.manager._clock() + 119)
        self.client.error = None
        release.set()
        await first
        third = await self.create(port=31002, pool_size=1)
        self.assertNotEqual(third["status"], "running")
        self.assertGreaterEqual(self.manager._global_next_create, deadline)
        self.assertEqual(len(self.client.creates), 1)

    async def test_restart_missing_local_credentials_cannot_fill_into_anonymous_service(self):
        row = await self.create(username="test-fake-user", password="test-fake-password")
        await self.filled(row["id"])
        path = self.manager.state_path
        await self.manager.close()
        self.manager = GatewayManager(self.client, path, probe=lambda _: asyncio.sleep(0, result=sample("US")),
                                      relay_factory=FixtureRelay)
        await self.manager.initialize()
        runtime = self.manager._gateways[row["id"]]
        self.assertTrue(runtime.auth_missing)
        self.manager._ensure_fill(runtime, runtime.generation, force=True)
        await runtime.filler
        snapshot = (await self.manager.list_gateways())[0]
        self.assertIsNone(runtime.relay)
        self.assertIsNone(runtime.upstream)
        self.assertEqual(snapshot["pool_ready"], 0)
        self.assertEqual(snapshot["last_error"], "local_credentials_required")
        self.assertEqual(len(self.client.creates), 3)

    async def test_full_free_account_reclaims_failed_owned_nodes_before_rebuilding(self):
        self.client.capabilities.update(active_limit=5, creates_per_minute=5)
        row = await self.create(pool_size=5)
        await self.filled(row["id"], 5)
        runtime = self.manager._gateways[row["id"]]
        now = self.manager._clock()
        self.manager._clock = lambda: now + 61
        for cid in list(runtime.members):
            self.manager._on_upstream_result(runtime, cid, False, "dial", runtime.generation)
        await runtime.filler
        snapshot = (await self.manager.list_gateways())[0]
        self.assertEqual(snapshot["pool_ready"], 5)
        self.assertEqual(set(self.client.removed), {f"pool-{i}" for i in range(1, 6)})
        self.assertEqual(len(self.manager._owned), 5)
        self.assertEqual(len(self.client.creates), 10)

    async def test_full_account_reclaim_never_removes_healthy_or_foreign_nodes(self):
        self.client.capabilities.update(active_limit=5, creates_per_minute=20)
        self.client.list_proxies = lambda: [{"client_id": "foreign-1"}, {"client_id": "foreign-2"}]
        row = await self.create()
        await self.filled(row["id"])
        runtime = self.manager._gateways[row["id"]]
        self.manager._on_upstream_result(runtime, "pool-1", False, "dial", runtime.generation)
        await runtime.filler
        snapshot = (await self.manager.list_gateways())[0]
        self.assertEqual(snapshot["pool_ready"], 3)
        self.assertEqual(self.client.removed, ["pool-1"])
        self.assertEqual(runtime.upstream["client_id"], "pool-2")
        self.assertTrue({"pool-2", "pool-3"}.issubset(runtime.members))

    async def test_failed_quota_reclaim_keeps_journal_and_stops_spending(self):
        self.client.capabilities.update(active_limit=3, creates_per_minute=20)
        row = await self.create()
        await self.filled(row["id"])
        runtime = self.manager._gateways[row["id"]]

        def remove(cid):
            self.client.removed.append(cid)
            raise OSError("test-fake-key-cleanup-failure")

        self.client.remove_proxy = remove
        self.manager._on_upstream_result(runtime, "pool-1", False, "dial", runtime.generation)
        await runtime.filler
        self.assertIn("pool-1", self.manager._owned)
        self.assertGreater(self.manager._owned["pool-1"].attempts, 0)
        self.assertGreater(runtime.next_create, self.manager._clock())
        self.assertEqual(len(self.client.creates), 3)
        self.assertEqual(runtime.upstream["client_id"], "pool-2")
        self.assertEqual(runtime.status, "running")
        self.assertEqual(runtime.pool_error, "owned_cleanup_pending")
        await self.manager._fill_pool(runtime, runtime.generation)
        self.assertEqual(self.client.removed, ["pool-1"])

    async def test_unique_auto_grace_preserves_owned_resources_while_using_recovery_buffer(self):
        row = await self.create()
        await self.filled(row["id"])
        runtime = self.manager._gateways[row["id"]]
        for cid, member in runtime.members.items():
            member.upstream.update(unique_exit=True, exit={"replace_mode": "auto"})
            self.failed.add(cid)
        for cid in list(runtime.members):
            self.manager._on_upstream_result(runtime, cid, False, "dial", runtime.generation)
        await runtime.filler
        self.assertEqual(self.client.removed, [])
        self.assertTrue({"pool-1", "pool-2", "pool-3"}.issubset(runtime.members))
        self.assertLessEqual(len(self.manager._owned), runtime.config["pool_size"] + 1)
        self.assertEqual(runtime.upstream["client_id"], "pool-4")

    async def test_pending_bind_cannot_publish_stale_endpoint_or_anonymous_auth(self):
        for change in ("endpoint", "auth"):
            with self.subTest(change=change):
                old_port = self.real_relay()
                new_port = self.real_relay() if change == "endpoint" else old_port
                entered, release = asyncio.Event(), asyncio.Event()
                binds = []

                class DelayedRelay(MixedProxyServer):
                    async def start(relay):
                        binds.append(relay)
                        if len(binds) == 2:
                            entered.set()
                            await release.wait()
                        await super().start()

                self.manager._relay_factory = lambda *args, **kwargs: DelayedRelay(
                    *args, **kwargs, tls_config={"state_dir": Path(self.tmp.name)})
                self.client.error = RuntimeError("test-fake-key-first-create-failed")
                row = await self.create(port=old_port)
                runtime = self.manager._gateways[row["id"]]
                self.assertIsNone(runtime.relay)
                self.client.error = None
                runtime.next_create = self.manager._global_next_create = 0
                self.manager._ensure_fill(runtime, runtime.generation, force=True)
                await asyncio.wait_for(entered.wait(), 1)
                data = {"port": new_port} if change == "endpoint" else {
                    "username": "test-fake-new-user", "password": "test-fake-new-password"}
                updating = asyncio.create_task(self.manager.update_gateway(row["id"], data))
                try:
                    for _ in range(100):
                        if all(runtime.config[key] == value for key, value in data.items()):
                            break
                        await asyncio.sleep(.01)
                    self.assertTrue(all(runtime.config[key] == value for key, value in data.items()))
                finally:
                    release.set()
                updated = await asyncio.wait_for(updating, 2)
                await self.filled(row["id"])
                self.assertEqual(updated["status"], "running")
                reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", new_port), 1)
                try:
                    if change == "auth":
                        writer.write(b"\x05\x01\x00")
                        await writer.drain()
                        self.assertEqual(await asyncio.wait_for(reader.readexactly(2), 1), b"\x05\xff")
                        self.assertEqual(runtime.relay.username, "test-fake-new-user")
                        self.assertEqual(runtime.relay.password, "test-fake-new-password")
                finally:
                    await close_writer(writer)
                if change == "endpoint":
                    with self.assertRaises(OSError):
                        await asyncio.wait_for(asyncio.open_connection("127.0.0.1", old_port), 1)
                self.assertIsNot(runtime.relay, binds[1])
                self.assertIsNone(binds[1].server)

    async def test_state_without_pool_size_defaults_to_three(self):
        row = await self.create(pool_size=1)
        await self.manager.close()
        path = Path(self.tmp.name) / "gateways.json"
        state = json.loads(path.read_text())
        del state["gateways"][0]["pool_size"]
        path.write_text(json.dumps(state))
        self.manager = GatewayManager(self.client, path, probe=lambda u: asyncio.sleep(0, result=sample("US")),
                                      relay_factory=FixtureRelay)
        await self.manager.initialize()
        self.assertEqual((await self.manager.list_gateways())[0]["pool_size"], 3)
