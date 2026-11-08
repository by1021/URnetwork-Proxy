"""Region cascade acceptance through the real management HTTP app and browser.

All countries/providers and saved gateways are temporary fixtures; no live account.
Run with the project's existing Playwright environment.
"""
import json
from pathlib import Path
import sys
import tempfile
import threading
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from playwright.sync_api import expect, sync_playwright
from core.client import URClient
from core.config import DEFAULT_CONFIG
from core.web_server import ManagementApp, ManagementServer
from proxy_pool_ui_smoke import UIManager


def location(cid, name, country='us', kind='region', count=10):
    return dict(location_id=cid, name=name, country_code=country,
                location_type=kind, region=name if kind == 'region' else 'California',
                city=name if kind == 'city' else '', region_location_id=cid if kind == 'region' else 'us-ca',
                provider_count=count)


def main():
    availability = {'US': 'ok', 'JP': 'ok'}
    directory_empty = False
    us_country_id = 'us-country'
    incomplete_us_region = False
    queries = []
    with tempfile.TemporaryDirectory(prefix='ur-region-cascade-') as tmp:
        config = {**DEFAULT_CONFIG, 'web_username': 'fixture-user', 'web_password': 'SAMPLE_PASSWORD'}
        config_path = Path(tmp) / 'config.json'
        config_path.write_text(json.dumps(config))
        client = URClient()
        client.get_account_status = lambda: {}

        def request(path, method='GET', body=None, **kwargs):
            if path == '/network/provider-locations':
                return {'locations': [] if directory_empty else [
                    location(us_country_id, 'United States', kind='country'),
                    location('jp-country', 'Japan', 'jp', 'country'),
                    location('nz-country', 'New Zealand', 'nz', 'country')]}
            if path != '/network/find-provider-locations':
                return {}
            query = (body or {}).get('query', '')
            queries.append(query)
            country = 'JP' if query in ('Japan', 'Osaka') else 'US'
            if availability[country] == 'failure':
                raise OSError('fixture directory failure')
            if availability[country] == 'empty' or query == 'New Zealand':
                return {'locations': []}
            if query in ('Japan', 'Osaka'):
                return {'locations': [location('jp-osaka', 'Osaka', 'jp'), location('us-ca', 'California')]}
            if query == 'Springfield':
                return {'locations': [location('us-city', query, kind='city')]}
            california = location('us-ca', 'California', count=20)
            if incomplete_us_region:
                california.pop('region_location_id')
                california.update(name='California renamed', region='California renamed')
            return {'locations': [california,
                location('us-tx', 'Texas'), location('us-zero', 'Ohio', count=0),
                location('mx-ca', 'Baja California', 'mx', count=50),
                location('us-city', 'Springfield', kind='city')]}

        client._request = request
        manager = UIManager()
        app = ManagementApp(config, tmp, manager=manager, client=client, config_path=config_path,
                            web_host='127.0.0.1', web_port=0)
        app.start()
        server = ManagementServer(('127.0.0.1', 0), app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                page = browser.new_page()
                page.set_default_timeout(10000)
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_port}')
                page.locator('#login-username').fill('fixture-user')
                page.locator('#login-password').fill('SAMPLE_PASSWORD')
                page.locator('#login-submit').click()
                expect(page.locator('#login-dialog')).not_to_be_visible()
                page.locator('#add-gateway').click()
                expect(page.locator('#gateway-mode')).to_have_value('country')
                page.locator('#gateway-name').fill('Region cascade fixture')
                page.locator('#gateway-mode').select_option('region')
                expect(page.locator('#gateway-location-query')).not_to_be_visible()
                expect(page.locator('#gateway-location-search')).not_to_be_visible()
                expect(page.locator('#gateway-regions-refresh')).to_be_visible()
                expect(page.locator('#gateway-regions-refresh')).to_be_disabled()
                expect(page.locator('#location-picker-note')).to_contain_text('选择国家')
                assert not queries, queries
                page.locator('#gateway-country').select_option('us-country')
                expect(page.locator('#gateway-location')).to_contain_text('加利福尼亚州 / California')
                expect(page.locator('#gateway-location')).to_contain_text('Texas')
                expect(page.locator('#gateway-location')).not_to_contain_text('Ohio')
                expect(page.locator('#gateway-location')).not_to_contain_text('Baja')
                expect(page.locator('#gateway-location')).not_to_contain_text('Springfield')
                assert queries == ['United States'], queries
                page.locator('#gateway-location').select_option('us-ca')
                page.locator('#gateway-save').click()
                expect(page.locator('#gateway-dialog')).not_to_be_visible()
                row = app.call(manager.list_gateways())[0]
                assert (row['mode'], row['country_code'], row['location_id']) == ('region', 'US', 'us-ca')
                card = page.locator('.gateway-card').filter(has=page.get_by_role('heading', name='Region cascade fixture', exact=True))

                # Existing binding remains editable even if the same cached
                # country query fails to refresh; other stale regions do not.
                card.get_by_role('button', name='编辑 Region cascade fixture', exact=True).click()
                expect(page.locator('#gateway-location')).to_have_value('us-ca')
                expect(page.locator('#gateway-regions-refresh')).to_be_enabled()
                # Same official location ID with incomplete/changed metadata
                # remains the original binding even if its next refresh fails.
                incomplete_us_region = True
                page.locator('#gateway-regions-refresh').click()
                expect(page.locator('#gateway-regions-refresh')).to_be_enabled()
                expect(page.locator('#gateway-location')).to_have_value('us-ca')
                incomplete_us_region = False
                availability['US'] = 'failure'
                page.locator('#gateway-regions-refresh').click()
                expect(page.locator('#location-picker-note')).to_contain_text('过期')
                expect(page.locator('#gateway-location')).to_have_value('us-ca')
                page.locator('#gateway-location').select_option('us-tx')
                page.locator('#gateway-save').click()
                expect(page.locator('#location-picker-error')).to_contain_text('刷新')
                expect(page.locator('#gateway-dialog')).to_be_visible()
                page.locator('#gateway-location').select_option('us-ca')
                page.locator('#gateway-pool-size').fill('2')
                page.locator('#gateway-save').click()
                expect(page.locator('#gateway-dialog')).not_to_be_visible()
                row = app.call(manager.list_gateways())[0]
                assert row['pool_size'] == 2
                assert (row['location_id'], row['region_location_id'], row['location_name'], row['region'], row['city']) == ('us-ca', 'us-ca', 'California', 'California', '')
                availability['US'] = 'ok'

                # Empty countries and failed lists never silently become a
                # country binding. Explicit refresh can recover the list.
                page.locator('#add-gateway').click()
                page.locator('#gateway-name').fill('No fallback fixture')
                page.locator('#gateway-mode').select_option('region')
                page.locator('#gateway-country').select_option('nz-country')
                expect(page.locator('#location-picker-error')).to_contain_text('无可用地区')
                expect(page.locator('#gateway-location option')).to_have_count(1)
                page.locator('#gateway-save').click()
                expect(page.locator('#gateway-dialog')).to_be_visible()
                assert len(app.call(manager.list_gateways())) == 1
                availability['JP'] = 'failure'
                page.locator('#gateway-country').select_option('jp-country')
                expect(page.locator('#location-picker-error')).to_contain_text('失败')
                expect(page.locator('#gateway-regions-refresh')).to_be_enabled()
                availability['JP'] = 'ok'
                page.locator('#gateway-regions-refresh').click()
                expect(page.locator('#gateway-location')).to_contain_text('Osaka')
                expect(page.locator('#gateway-location')).not_to_contain_text('California')
                page.locator('#gateway-cancel').click()

                # A country already chosen in country mode is automatically
                # loaded when switching to region mode.
                page.locator('#add-gateway').click()
                page.locator('#gateway-country').select_option('us-country')
                page.locator('#gateway-mode').select_option('region')
                expect(page.locator('#gateway-location')).to_contain_text('Texas')
                page.locator('#gateway-location').select_option('us-tx')
                page.locator('#gateway-country-search').fill('Japan')
                expect(page.locator('#gateway-country')).to_have_value('')
                expect(page.locator('#gateway-location')).to_have_value('')
                expect(page.locator('#gateway-location')).not_to_contain_text('Texas')
                page.locator('#gateway-country').select_option('jp-country')
                expect(page.locator('#gateway-location')).to_contain_text('Osaka')
                page.locator('#gateway-cancel').click()

                # Hold US requests in the browser to reproduce country, mode
                # and dialog races without timers or live network dependencies.
                pending = []
                def hold_us(route):
                    params = parse_qs(urlparse(route.request.url).query)
                    if params.get('country_code') == ['US'] and params.get('type') == ['region']:
                        pending.append(route)
                    else:
                        route.continue_()
                page.route('**/api/locations?**', hold_us)
                def release_old():
                    assert pending, 'automatic US region request was not sent'
                    for route in pending:
                        route.fulfill(json={'locations': [location('us-late', 'Late US Region')],
                            'source': 'fixture', 'stale': False, 'error': None})
                    pending.clear()

                page.locator('#add-gateway').click()
                page.locator('#gateway-mode').select_option('region')
                page.locator('#gateway-country').select_option('us-country')
                expect(page.locator('#gateway-save')).to_be_disabled()
                page.locator('#gateway-country').select_option('jp-country')
                expect(page.locator('#gateway-location')).to_contain_text('Osaka')
                release_old()
                expect(page.locator('#gateway-location')).not_to_contain_text('Late US')
                expect(page.locator('#gateway-save')).to_be_enabled()

                page.locator('#gateway-country').select_option('us-country')
                expect(page.locator('#gateway-save')).to_be_disabled()
                page.locator('#gateway-mode').select_option('city')
                expect(page.locator('#gateway-location-query')).to_be_visible()
                expect(page.locator('#gateway-location-search')).to_be_visible()
                expect(page.locator('#gateway-regions-refresh')).not_to_be_visible()
                release_old()
                page.locator('#gateway-location-query').fill('Springfield')
                page.locator('#gateway-location-search').click()
                expect(page.locator('#gateway-location')).to_contain_text('Springfield')
                expect(page.locator('#gateway-location')).not_to_contain_text('Late US')

                page.locator('#gateway-mode').select_option('region')
                expect(page.locator('#gateway-save')).to_be_disabled()
                page.locator('#gateway-cancel').click()
                page.locator('#add-gateway').click()
                expect(page.locator('#gateway-save')).to_be_enabled()
                release_old()
                expect(page.locator('#gateway-location')).to_have_value('')
                page.locator('#gateway-cancel').click()
                page.unroute('**/api/locations?**', hold_us)

                # A background country refresh while editing must preserve the
                # selected country's identity, including its saved fallback and
                # a replacement official country ID when the directory recovers.
                card.get_by_role('button', name='编辑 Region cascade fixture', exact=True).click()
                expect(page.locator('#gateway-location')).to_have_value('us-ca')
                expect(page.locator('#gateway-regions-refresh')).to_be_enabled()
                directory_empty = True
                page.locator('#refresh-countries').evaluate('(button) => button.click()')
                expect(page.locator('#stat-countries')).to_have_text('0')
                expect(page.locator('#gateway-country')).to_have_value('saved-country:US')
                expect(page.locator('#gateway-location')).to_have_value('us-ca')
                directory_empty = False
                us_country_id = 'us-country-restored'
                page.locator('#refresh-countries').evaluate('(button) => button.click()')
                expect(page.locator('#stat-countries')).to_have_text('3')
                expect(page.locator('#gateway-country')).to_have_value(us_country_id)
                expect(page.locator('#gateway-location')).to_have_value('us-ca')
                directory_empty = True
                page.locator('#refresh-countries').evaluate('(button) => button.click()')
                expect(page.locator('#stat-countries')).to_have_text('0')
                expect(page.locator('#gateway-country')).to_have_value('saved-country:US')
                page.locator('#gateway-pool-size').fill('3')
                page.locator('#gateway-save').click()
                expect(page.locator('#gateway-dialog')).not_to_be_visible()
                row = app.call(manager.list_gateways())[0]
                assert (row['country_code'], row['location_id'], row['pool_size']) == ('US', 'us-ca', 3)

                # Missing country directory does not erase a saved region;
                # the automatic query falls back to its official English name.
                directory_empty = True
                availability['US'] = 'failure'
                page.locator('.nav-item[data-view="countries"]').click()
                page.locator('#refresh-countries').click()
                expect(page.locator('#stat-countries')).to_have_text('0')
                page.locator('.nav-item[data-view="gateways"]').click()
                card.get_by_role('button', name='编辑 Region cascade fixture', exact=True).click()
                expect(page.locator('#gateway-country')).not_to_have_value('')
                expect(page.locator('#location-picker-note')).to_contain_text('过期')
                expect(page.locator('#gateway-location')).to_have_value('us-ca')
                page.locator('#gateway-pool-size').fill('1')
                page.locator('#gateway-save').click()
                expect(page.locator('#gateway-dialog')).not_to_be_visible()
                row = app.call(manager.list_gateways())[0]
                assert (row['country_code'], row['location_id'], row['pool_size']) == ('US', 'us-ca', 1)

                # If the user picked another country, a catalog outage must not
                # silently restore the saved gateway's original country instead.
                directory_empty = False
                card.get_by_role('button', name='编辑 Region cascade fixture', exact=True).click()
                expect(page.locator('#gateway-regions-refresh')).to_be_enabled()
                page.locator('#refresh-countries').evaluate('(button) => button.click()')
                expect(page.locator('#gateway-country')).to_have_value(us_country_id)
                page.locator('#gateway-country').select_option('jp-country')
                expect(page.locator('#gateway-location')).to_contain_text('Osaka')
                page.locator('#gateway-location').select_option('jp-osaka')
                directory_empty = True
                page.locator('#refresh-countries').evaluate('(button) => button.click()')
                expect(page.locator('#stat-countries')).to_have_text('0')
                expect(page.locator('#gateway-country')).to_have_value('')
                expect(page.locator('#gateway-location')).to_have_value('')
                expect(page.locator('#gateway-location')).not_to_contain_text('Osaka')
                page.locator('#gateway-save').click()
                expect(page.locator('#country-picker-error')).to_contain_text('请选择国家')
                page.locator('#gateway-cancel').click()
                row = app.call(manager.list_gateways())[0]
                assert (row['country_code'], row['location_id'], row['pool_size']) == ('US', 'us-ca', 1)
                assert not errors, errors
                browser.close()
            print('PASS: automatic region cascade, canonical IDs, supply filter, refresh/empty/failure, saved binding, country/mode/dialog races, city regression')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)
            app.close()


if __name__ == '__main__':
    main()
