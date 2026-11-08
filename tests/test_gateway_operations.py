import asyncio
import tempfile
import shutil
import unittest
from core.gateway import GatewayManager
from core.web_server import ManagementApp
from core.client import URClient

async def fake_probe(upstream):
    return {'ok': True, 'http_ok': True, 'socks_ok': True, 'latency_ms': 5.0, 'exit_ip': '1.2.3.4'}

class TestGatewayOperations(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.mkdtemp()
        self.client = URClient(api_base='http://127.0.0.1:9999')
        self.client.create_proxy = lambda **kw: {'client_id': 'c_' + kw.get('gateway_id', 'x'), 'host': '127.0.0.1', 'port': 9999}
        self.client.delete_proxy = lambda **kw: True
        self.client.remove_proxy = lambda cid: {'ok': True}
        self.mgr = GatewayManager(self.client, self.tmp + '/gateways.json', probe=fake_probe)
        await self.mgr.initialize()

    async def asyncTearDown(self):
        await self.mgr.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    async def test_modify_running_gateway(self):
        # Create a gateway
        g = await self.mgr.create_gateway({'name': 'GW1', 'host': '127.0.0.1', 'port': 20881, 'mode': 'best', 'enabled': True})
        self.assertEqual(g['status'], 'running')
        self.assertEqual(g['name'], 'GW1')

        # Update name and interval while running on same port
        u = await self.mgr.update_gateway(g['id'], {'name': 'GW1_Modified', 'host': '127.0.0.1', 'port': 20881, 'interval': 60})
        self.assertEqual(u['name'], 'GW1_Modified')
        self.assertEqual(u['interval'], 60)
        self.assertEqual(u['status'], 'running')

        # Update credentials on same port while running
        u2 = await self.mgr.update_gateway(g['id'], {'name': 'GW1_Modified', 'host': '127.0.0.1', 'port': 20881, 'username': 'usr', 'password': 'pwd'})
        self.assertEqual(u2['username'], 'usr')
        self.assertEqual(u2['password'], 'pwd')
        self.assertEqual(u2['status'], 'running')

    async def test_modify_gateway_port(self):
        g = await self.mgr.create_gateway({'name': 'GW2', 'host': '127.0.0.1', 'port': 20882, 'mode': 'best', 'enabled': True})
        self.assertEqual(g['status'], 'running')

        # Change port to 20883
        u = await self.mgr.update_gateway(g['id'], {'name': 'GW2', 'host': '127.0.0.1', 'port': 20883})
        self.assertEqual(u['port'], 20883)
        self.assertEqual(u['status'], 'running')

    async def test_delete_and_recreate_same_port(self):
        g = await self.mgr.create_gateway({'name': 'GW3', 'host': '127.0.0.1', 'port': 20884, 'mode': 'best', 'enabled': True})
        self.assertEqual(g['status'], 'running')

        # Delete
        d = await self.mgr.delete_gateway(g['id'])
        self.assertEqual(d['status'], 'deleted')

        # Verify not in list
        active = await self.mgr.list_gateways()
        self.assertFalse(any(item['id'] == g['id'] for item in active))

        # Recreate on the exact same port
        g_new = await self.mgr.create_gateway({'name': 'GW3_Recreated', 'host': '127.0.0.1', 'port': 20884, 'mode': 'best', 'enabled': True})
        self.assertEqual(g_new['status'], 'running')
        self.assertEqual(g_new['port'], 20884)

    async def test_rotate_discards_duplicate_ip(self):
        probe_ips = ['1.1.1.1', '1.1.1.1', '2.2.2.2']
        idx = 0
        async def custom_probe(upstream):
            nonlocal idx
            ip = probe_ips[min(idx, len(probe_ips) - 1)]
            idx += 1
            return {'ok': True, 'http_ok': True, 'socks_ok': True, 'latency_ms': 5.0, 'exit_ip': ip, 'is_residential': True, 'isp': 'MockISP'}

        self.mgr._probe = custom_probe
        counter = 0
        def make_proxy(**kw):
            nonlocal counter
            counter += 1
            return {'client_id': f'c_{counter}', 'host': '127.0.0.1', 'http_port': 9999, 'socks_port': 9998}
        self.client.create_proxy = make_proxy

        g = await self.mgr.create_gateway({'name': 'GW_Rotate', 'host': '127.0.0.1', 'port': 20886, 'mode': 'best', 'enabled': True})
        self.assertEqual(g['exit_ip'], '1.1.1.1')

        rotated = await self.mgr.rotate_gateway(g['id'])
        self.assertEqual(rotated['exit_ip'], '2.2.2.2')
        self.assertEqual(rotated['rotations'], 1)
        self.assertEqual(rotated['isp'], 'MockISP')

    async def test_residential_filter_rejects_datacenter_ip(self):
        probe_res = [
            {'ok': True, 'http_ok': True, 'socks_ok': True, 'latency_ms': 5.0, 'exit_ip': '207.174.105.95', 'is_residential': False, 'isp': 'GoCodeIT'},
            {'ok': True, 'http_ok': True, 'socks_ok': True, 'latency_ms': 5.0, 'exit_ip': '67.173.122.141', 'is_residential': True, 'isp': 'Comcast'}
        ]
        idx = 0
        async def custom_probe(upstream):
            nonlocal idx
            res = probe_res[min(idx, len(probe_res) - 1)]
            idx += 1
            return res

        self.mgr._probe = custom_probe
        counter = 0
        def make_proxy(**kw):
            nonlocal counter
            counter += 1
            return {'client_id': f'c_{counter}', 'host': '127.0.0.1', 'http_port': 9999, 'socks_port': 9998}
        self.client.create_proxy = make_proxy

        g = await self.mgr.create_gateway({'name': 'GW_Residential', 'host': '127.0.0.1', 'port': 20887, 'mode': 'best', 'residential_only': True, 'enabled': True})
        self.assertEqual(g['exit_ip'], '67.173.122.141')
        self.assertTrue(g['is_residential'])
        self.assertEqual(g['isp'], 'Comcast')

class TestWebAppGatewayEndpoints(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.client = URClient(api_base='http://127.0.0.1:9999')
        self.client.create_proxy = lambda **kw: {'client_id': 'c1', 'host': '127.0.0.1', 'port': 9999}
        self.client.delete_proxy = lambda **kw: True
        self.client.remove_proxy = lambda cid: {'ok': True}
        self.app = ManagementApp({'web_username': 'admin', 'web_password': 'password123'}, client=self.client, state_dir=self.tmp)
        self.app.manager._probe = fake_probe
        self.app.start()

    def tearDown(self):
        self.app.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_web_api_modify_delete_recreate(self):
        # 1. Create
        created = self.app.dispatch('POST', '/api/gateways', '', {
            'name': 'WebGW', 'host': '127.0.0.1', 'port': 20885, 'mode': 'best', 'enabled': True, 'interval': 30, 'failures_before_rotate': 3
        })
        gid = created['id']
        self.assertEqual(created['status'], 'running')

        # 2. Modify via PUT (simulating web form)
        updated = self.app.dispatch('PUT', f'/api/gateways/{gid}', '', {
            'name': 'WebGW_Renamed', 'host': '127.0.0.1', 'port': 20885, 'mode': 'best', 'interval': 45, 'failures_before_rotate': 5,
            'enabled': True, 'username': 'user1', 'password': 'pass1'
        })
        self.assertEqual(updated['name'], 'WebGW_Renamed')
        self.assertEqual(updated['username'], 'user1')
        self.assertEqual(updated['password'], 'pass1')

        # 3. Delete
        deleted = self.app.dispatch('DELETE', f'/api/gateways/{gid}', '', {})
        self.assertEqual(deleted['status'], 'deleted')

        # List gateways - should be empty
        listed = self.app.dispatch('GET', '/api/gateways', '', None)
        self.assertEqual(len(listed['gateways']), 0)

        # 4. Recreate on port 20885
        recreated = self.app.dispatch('POST', '/api/gateways', '', {
            'name': 'WebGW_Recreated', 'host': '127.0.0.1', 'port': 20885, 'mode': 'best', 'enabled': True, 'interval': 30, 'failures_before_rotate': 3
        })
        self.assertEqual(recreated['name'], 'WebGW_Recreated')
        self.assertEqual(recreated['port'], 20885)
        self.assertEqual(recreated['status'], 'running')



if __name__ == '__main__':
    unittest.main()
