"""Real local listeners + lifecycle watchdog, with deterministic cloud fixtures."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from core.checker import test_http_proxy, test_socks5_proxy
from core.gateway import GatewayManager
from core.protocol import close_writer
from tests.fixtures import FakeUpstream


def free_port():
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class LocalCloud:
    def __init__(self, remotes):
        self.remotes, self.created, self.removed = remotes, [], []
        self.us_rotated = False

    def create_proxy(self, mode="best", target="", location_id=None):
        self.created.append((mode, target, location_id))
        index = 1 if target == "JP" else 2 if self.us_rotated else 0
        if target == "US":
            self.us_rotated = True
        return dict(self.remotes[index].upstream, client_id=f"owned-{len(self.created)}")

    def remove_proxy(self, client_id):
        self.removed.append(client_id)
        return {}


async def local_probe(upstream):
    results = await asyncio.gather(*(asyncio.to_thread(fn, upstream["host"], upstream[key],
        upstream["auth_token"], target_host="fixture.invalid", target_port=80, use_tls=False, timeout=1)
        for fn, key in ((test_http_proxy, "http_port"), (test_socks5_proxy, "socks_port"))))
    return dict(ok=all(r[0] for r in results), http_ok=results[0][0], socks_ok=results[1][0],
                exit_ip=results[1][2] if results[1][0] else "",
                latency_ms=results[1][1] or 0, error="")


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.remotes = [await FakeUpstream(f"203.0.113.{n}").start() for n in (10, 20, 30)]
        self.client = LocalCloud(self.remotes)
        self.path = Path(self.temp.name) / "gateways.json"
        self.manager = GatewayManager(self.client, self.path, probe=local_probe)
        self.control = patch("core.gateway.probe_control", AsyncMock(return_value=True))
        self.control.start()
        await self.manager.initialize()

    async def asyncTearDown(self):
        await self.manager.close()
        for remote in self.remotes:
            await remote.close()
        self.control.stop()
        self.temp.cleanup()

    async def create(self, country):
        return await self.manager.create_gateway(dict(name=country, mode="country", country_code=country,
            location_id=country + "-location", port=free_port(), enabled=True, interval=5, failures_before_rotate=1))

    async def request(self, port):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            writer.write(b"GET http://fixture.invalid/ HTTP/1.1\r\n\r\n")
            await writer.drain()
            return await asyncio.wait_for(reader.read(), 2)
        finally:
            await close_writer(writer)

    async def test_autonomous_recovery_keeps_port_and_other_country(self):
        us, jp = await self.create("US"), await self.create("JP")
        self.assertEqual(us["status"], "running")
        self.assertEqual(jp["status"], "running")
        self.assertTrue((await self.request(us["port"])).endswith(b".10"))
        self.assertTrue((await self.request(jp["port"])).endswith(b".20"))
        self.remotes[0].healthy = False
        async def wait_rotation():
            while True:
                rows = await self.manager.list_gateways()
                row = next(g for g in rows if g["id"] == us["id"])
                if row["rotations"]:
                    return row
                await asyncio.sleep(.1)
        recovered = await asyncio.wait_for(wait_rotation(), 9)
        self.assertEqual(recovered["port"], us["port"])
        self.assertEqual(recovered["country_code"], "US")
        self.assertTrue((await self.request(us["port"])).endswith(b".30"))
        self.assertTrue((await self.request(jp["port"])).endswith(b".20"))
        self.assertNotIn("owned-2", self.client.removed)

    async def test_restart_restores_enabled_listener(self):
        us = await self.create("US")
        await self.manager.close()
        self.manager = GatewayManager(self.client, self.path, probe=local_probe)
        await self.manager.initialize()
        row = (await self.manager.list_gateways())[0]
        self.assertEqual(row["port"], us["port"])
        self.assertEqual(row["status"], "running")
        self.assertTrue((await self.request(row["port"])).endswith(b".30"))
