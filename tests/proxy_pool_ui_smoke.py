"""Real management HTTP/browser acceptance for pool and precise-location forms.

Use a test environment with Playwright: python tests/proxy_pool_ui_smoke.py
The UI test manager is an explicit control-plane fixture; data-plane failover is
verified separately by proxy_pool_runtime_smoke.py. Add --integrated to use the
production GatewayManager as well. No account or saved data.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from playwright.sync_api import expect, sync_playwright
from core.config import DEFAULT_CONFIG
from core.client import URClient
from core.gateway import GatewayManager
from test_gateway_country import FixtureRelay
import core.web_server as web_server
from core.web_server import ManagementApp, ManagementServer


class UIManager:
    """Deterministic management snapshots, not a proxy/data-plane implementation."""
    def __init__(self):
        self.rows = {}
        self.client = None
        self.has_owned_clients = False
    async def initialize(self): return {}
    async def close(self): return {}
    async def list_gateways(self): return [dict(row) for row in self.rows.values()]
    def events(self, after=0): return []
    @staticmethod
    def upstreams(size):
        return [{'client_id':f'upstream-{i+1}', 'status':'active' if i==0 else 'ready',
                 'exit_ip':f'203.0.113.{i+1}', 'latency_ms':5} for i in range(size)]
    async def create_gateway(self, data):
        row = dict(data, id=f'fixture-{len(self.rows)+1}', status='running', exit_ip='203.0.113.1',
                   exit_country_code=data['country_code'], country_source='fixture', is_residential=True,
                   pool_ready=data['pool_size'], pool_total=data['pool_size'], pool_status='ready',
                   pool_error='', active_client_id='upstream-1', connections=0, latency_ms=5,
                   last_check=None, failures=0, rotations=0,
                   exit_city=data.get('city', ''), exit_region=data.get('region', ''),
                   upstreams=self.upstreams(data['pool_size']))
        self.rows[row['id']] = row
        return dict(row)
    async def update_gateway(self, gid, data):
        members = self.upstreams(data['pool_size'])
        ready_ids = {member['client_id'] for member in members}
        members.extend(dict(member, status='draining') for member in self.rows[gid]['upstreams']
                       if member['client_id'] not in ready_ids)
        self.rows[gid].update(data, pool_ready=data['pool_size'], pool_total=len(members), upstreams=members)
        return dict(self.rows[gid])


def main():
    # Optional baseline asset directory lets the identical browser test capture red
    # before the new UI is introduced, without changing the worktree under test.
    if os.environ.get('URN_UI_ASSETS_DIR'):
        web_server.ROOT = Path(os.environ['URN_UI_ASSETS_DIR'])
    with tempfile.TemporaryDirectory(prefix='ur-pool-ui-') as tmp:
        config = {**DEFAULT_CONFIG, 'web_username':'fixture-user', 'web_password':'SAMPLE_PASSWORD'}
        config_path = Path(tmp)/'config.json'
        config_path.write_text(json.dumps(config))
        client = URClient()
        client.get_account_status = lambda: {}
        allocations = {}
        creation_bindings = {}
        directory_empty = False
        serial = 0
        def request(path, method='GET', body=None, **kw):
            nonlocal serial
            if path == '/subscription/balance': return {'active_transfer_balances':[{'pro':True,'start_time':'2026-01-01T00:00:00Z','end_time':'2099-01-01T00:00:00Z'}]}
            if path == '/network/auth-client':
                serial += 1
                cid=f'fixture-upstream-{serial}'
                proxy={'proxy_host':'proxy.example.test','auth_token':'test-fake-token', 'http_proxy_port':80,'https_proxy_port':443,'socks_proxy_port':1080, 'http_proxy_url':'http://proxy.example.test','https_proxy_url':'https://proxy.example.test','socks_proxy_url':'socks5://proxy.example.test'}
                allocations[cid]=proxy
                creation_bindings[cid]=body['proxy_config']['initial_device_state']
                return {'client_id':cid,'proxy_config_result':proxy}
            if path == '/network/proxies': return {'clients':[{'client_id':cid,'proxy_client':pc} for cid,pc in allocations.items()]}
            if path == '/network/remove-client':
                allocations.pop(body['client_id'], None); return {'ok':True}
            if path == '/network/provider-locations':
                if directory_empty: return {'locations':[]}
                return {'locations': [{'location_id':'us-country','location_type':'country','name':'United States','country_code':'us','provider_count':10}]}
            query = (body or {}).get('query', '')
            if query == 'empty': return {'locations':[]}
            if query == 'failure': raise OSError('fixture catalog failure')
            kind = 'region' if query in ('California', 'United States') else 'city'
            name = 'California' if query == 'United States' else query
            return {'locations':[
                {'location_id':'mx-wrong','location_type':kind,'name':query,'country_code':'mx','region':'Baja California','region_location_id':'mx-bc','provider_count':50},
                {'location_id':'us-ma-city','location_type':'city','name':'Springfield','country_code':'us','region':'Massachusetts','region_location_id':'us-ma','provider_count':10},
                {'location_id':'us-ca' if kind == 'region' else 'us-city','location_type':kind,'name':name,'city':name if kind == 'city' else '', 'country_code':'us','region':'California','region_location_id':'us-ca','provider_count':5}]}
        client._request = request
        integrated = '--integrated' in sys.argv
        async def probe(upstream):
            suffix=upstream['client_id'].rsplit('-',1)[-1]
            return {'ok':True,'http_ok':True,'socks_ok':True,'https_ok':True,'exit_ip':f'203.0.113.{suffix}', 'latency_ms':5,'country_code':'US','country_source':'fixture','is_residential':True,'city':'Springfield','region':'California'}
        manager = GatewayManager(client,Path(tmp)/'gateways.json',probe=probe,relay_factory=FixtureRelay) if integrated else UIManager()
        app = ManagementApp(config, tmp, manager=manager, client=client, config_path=config_path,
                            web_host='127.0.0.1', web_port=0)
        # The separately owned manager schema is copied explicitly in this UI
        # component test. Final integration also tests the production validator.
        original_validate = web_server._validate
        def validate(data, base=None):
            metadata = ('pool_size','location_name','region','city','region_location_id')
            new = dict(base or {}); new.update(data)
            standard = {k:v for k,v in new.items() if k not in metadata}
            if standard.get('mode') in ('city','region'):
                # Validate common fields using the established country contract.
                standard['mode'] = 'country'
            checked = original_validate(standard)
            checked.update({k:new.get(k, 3 if k=='pool_size' else '') for k in metadata})
            checked['mode'] = new.get('mode', checked['mode'])
            return checked
        if not integrated: web_server._validate = validate
        app.start()
        server = ManagementServer(('127.0.0.1',0), app)
        thread=threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True)
                page=browser.new_page()
                errors=[]; page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_port}')
                page.locator('#login-username').fill('fixture-user'); page.locator('#login-password').fill('SAMPLE_PASSWORD')
                page.locator('#login-submit').click(); expect(page.locator('#login-dialog')).not_to_be_visible()
                page.locator('#add-gateway').click()
                expect(page.get_by_label('上游代理数量',exact=True)).to_have_value('3')
                page.get_by_label('网关名称',exact=True).fill('City pool fixture')
                page.locator('#gateway-mode').select_option('city')
                page.locator('#gateway-country').select_option('us-country')
                page.get_by_role('button',name='创建并启动',exact=True).click()
                expect(page.locator('#location-picker-error')).to_contain_text('精确位置')
                page.get_by_label('搜索地区 / 城市',exact=True).fill('Springfield')
                page.get_by_role('button',name='搜索位置',exact=True).click()
                expect(page.locator('#gateway-location')).to_contain_text('Massachusetts')
                expect(page.locator('#gateway-location')).not_to_contain_text('Baja')
                page.locator('#gateway-region-filter').select_option('us-ca')
                expect(page.locator('#gateway-location')).not_to_contain_text('Massachusetts')
                page.locator('#gateway-location').select_option('us-city')
                page.get_by_role('button',name='创建并启动',exact=True).click()
                expect(page.locator('#gateway-dialog')).not_to_be_visible()
                card=page.locator('.gateway-card').filter(has=page.get_by_role('heading',name='City pool fixture',exact=True))
                expect(card).to_contain_text('上游可用 3/3')
                expect(card.locator('.gateway-upstream-list li[data-state="active"]')).to_have_count(1)
                expect(card.locator('.gateway-upstream-list li[data-state="ready"]')).to_have_count(2)
                row=app.call(manager.list_gateways())[0]
                assert (row['mode'],row['location_id'],row['country_code']) == ('city','us-city','US')
                if integrated:
                    assert row['pool_ready'] == 3 and len(row['upstreams']) == 3
                    for info in row['upstreams']:
                        binding = creation_bindings[info['client_id']]
                        assert binding['location']['connect_location_id'] == {'location_id':'us-city'}
                        assert binding['location']['location_type'] == 'city'
                        assert binding['country_code'] == 'us'
                card.get_by_role('button',name='编辑 City pool fixture',exact=True).click()
                expect(page.locator('#gateway-location')).to_have_value('us-city')
                expect(page.locator('#gateway-pool-size')).to_have_value('3')
                page.get_by_role('button',name='搜索位置',exact=True).click()
                page.locator('#gateway-region-filter').select_option('us-ca')
                expect(page.locator('#gateway-location')).to_have_value('us-city')
                page.get_by_role('button',name='搜索位置',exact=True).click()
                expect(page.locator('#gateway-region-filter')).to_have_value('us-ca')
                expect(page.locator('#gateway-location')).to_have_value('us-city')
                page.get_by_label('搜索地区 / 城市',exact=True).fill('failure')
                page.get_by_role('button',name='搜索位置',exact=True).click()
                expect(page.locator('#location-picker-note')).to_contain_text('过期')
                expect(page.locator('#gateway-location')).to_have_value('us-city')
                page.locator('#gateway-pool-size').fill('2')
                page.get_by_role('button',name='保存更改',exact=True).click()
                expect(page.locator('#gateway-dialog')).not_to_be_visible()
                expect(card).to_contain_text('上游可用 2/2')
                expect(card.locator('.gateway-upstream-list li[data-state="active"]')).to_have_count(1)
                expect(card.locator('.gateway-upstream-list li[data-state="ready"]')).to_have_count(1)
                expect(card.locator('.gateway-upstream-list li[data-state="draining"]')).to_have_count(1)
                expect(card.locator('.gateway-upstream-list li[data-state="draining"]')).to_contain_text('回收中')
                if integrated:
                    row=app.call(manager.list_gateways())[0]
                    assert row['pool_size'] == 2 and row['pool_ready'] == 2
                    assert row['pool_total'] == 3
                    draining = [info for info in row['upstreams'] if info['status'] == 'draining']
                    assert len(draining) == 1 and draining[0]['client_id'] in allocations
                page.locator('#add-gateway').click()
                page.get_by_label('网关名称',exact=True).fill('Region pool fixture')
                page.locator('#gateway-mode').select_option('region')
                page.locator('#gateway-country').select_option('us-country')
                expect(page.locator('#gateway-location-query')).not_to_be_visible()
                expect(page.locator('#gateway-location-search')).not_to_be_visible()
                expect(page.locator('#gateway-location')).to_contain_text('California')
                page.locator('#gateway-location').select_option('us-ca')
                page.get_by_role('button',name='创建并启动',exact=True).click()
                expect(page.locator('#gateway-dialog')).not_to_be_visible()
                region_card=page.locator('.gateway-card').filter(has=page.get_by_role('heading',name='Region pool fixture',exact=True))
                expect(region_card).to_contain_text('上游可用 3/3')
                rows=app.call(manager.list_gateways())
                row=next(row for row in rows if row['name']=='Region pool fixture')
                assert row['mode']=='region'
                assert row['location_id']=='us-ca'
                if integrated:
                    assert row['pool_ready'] == 3 and len(row['upstreams']) == 3
                    for info in row['upstreams']:
                        binding = creation_bindings[info['client_id']]
                        assert binding['location']['connect_location_id'] == {'location_id':'us-ca'}
                        assert binding['location']['location_type'] == 'region'
                        assert binding['country_code'] == 'us'
                # Closing an in-flight search must not leave a later dialog
                # disabled or allow the old response to replace its options.
                pending_routes = []
                page.route('**/api/locations?**', lambda route: pending_routes.append(route))
                page.locator('#add-gateway').click()
                page.locator('#gateway-mode').select_option('region')
                page.locator('#gateway-country').select_option('us-country')
                expect(page.locator('#gateway-regions-refresh')).to_be_disabled()
                page.locator('#gateway-cancel').click()
                page.locator('#add-gateway').click()
                expect(page.locator('#gateway-save')).to_be_enabled()
                for route in pending_routes:
                    route.fulfill(json={'locations':[], 'source':'fixture', 'stale':False, 'error':None})
                page.unroute('**/api/locations?**')
                expect(page.locator('#gateway-location')).to_have_value('')
                page.locator('#gateway-cancel').click()
                # A country temporarily absent from the current directory must
                # not erase an existing precise binding during an unrelated edit.
                directory_empty = True
                page.locator('.nav-item[data-view="countries"]').click()
                page.locator('#refresh-countries').click()
                expect(page.locator('#stat-countries')).to_have_text('0')
                page.locator('.nav-item[data-view="gateways"]').click()
                card.get_by_role('button',name='编辑 City pool fixture',exact=True).click()
                expect(page.locator('#gateway-country')).not_to_have_value('')
                expect(page.locator('#gateway-location')).to_have_value('us-city')
                page.locator('#gateway-pool-size').fill('1')
                page.get_by_role('button',name='保存更改',exact=True).click()
                expect(page.locator('#gateway-dialog')).not_to_be_visible()
                row=next(row for row in app.call(manager.list_gateways()) if row['name']=='City pool fixture')
                assert (row['location_id'],row['country_code'],row['pool_size']) == ('us-city','US',1)
                assert not errors, errors
                browser.close()
            print('PASS: pool-size default/edit, exact city/region, country/parent filtering, empty/stale/failure UI, pool telemetry')
        finally:
            web_server._validate=original_validate
            server.shutdown();server.server_close();thread.join(5);app.close()


if __name__=='__main__':main()
