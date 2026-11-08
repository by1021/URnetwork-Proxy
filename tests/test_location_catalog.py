import asyncio
import unittest

from core.client import ApiError, URClient
from core.locator import CountryCatalog
from core.web_server import ManagementApp, GatewayValidationError


def location(cid, kind='city', country='us', region='us-ca', name='Springfield'):
    return {'location_id': cid, 'location_type': kind, 'country_code': country,
            'country_location_id': country + '-country', 'region_location_id': region,
            'name': name, 'city': name if kind == 'city' else '', 'region': 'California',
            'provider_count': 10}


class LocationCatalogTests(unittest.TestCase):
    def setUp(self):
        self.client = URClient()
        self.client._request = lambda *a, **kw: {'locations': [
            location('mx-city', country='mx'), location('us-other', region='us-ma'),
            location('us-city'), location('us-region', kind='region')]}
        self.catalog = CountryCatalog(self.client)

    def test_search_filters_country_type_and_parent(self):
        data = self.catalog.search('Springfield', country_code='US', location_type='city', region_location_id='us-ca')
        self.assertEqual([x['location_id'] for x in data['locations']], ['us-city'])
        self.assertFalse(data['stale'])
        self.assertEqual(data['source'], '/network/find-provider-locations')

    def test_region_results_are_not_filtered_out(self):
        data = self.catalog.search('California', country_code='US', location_type='region')
        self.assertEqual(data['locations'][0]['location_id'], 'us-region')

    def test_stale_search_is_explicit_and_cached(self):
        self.catalog.search('California', location_type='region')
        self.client._request = lambda *a, **kw: (_ for _ in ()).throw(ApiError(503))
        data = self.catalog.search('California', location_type='region', refresh=True)
        self.assertTrue(data['stale'])
        self.assertTrue(data['error'])
        self.assertEqual(len(data['locations']), 1)

    def test_resolve_rejects_id_from_wrong_country_or_parent(self):
        self.assertIsNone(self.catalog.resolve('mx-city', 'US', 'city', query='Springfield'))
        self.assertIsNone(self.catalog.resolve('us-other', 'US', 'city', query='Springfield', region_location_id='us-ca'))
        self.assertEqual(self.catalog.resolve('us-city', 'US', 'city', query='Springfield')['location_id'], 'us-city')

    def test_bad_input_does_not_call_upstream(self):
        with self.assertRaises(ValueError): self.catalog.search('x' * 129)
        with self.assertRaises(ValueError): self.catalog.search('Tokyo', country_code='invalid')
        with self.assertRaises(ValueError): self.catalog.search('Tokyo', location_type='province')

    def test_api_binding_uses_canonical_metadata(self):
        app = object.__new__(ManagementApp)
        app.catalog = self.catalog
        data = {'mode': 'city', 'country_code': 'US', 'location_id': 'us-city',
                'location_name': 'Springfield', 'region': 'forged', 'city': 'forged', 'region_location_id': 'us-ca'}
        asyncio.run(app.validate_binding(data))
        self.assertEqual(data['region'], 'California')
        self.assertEqual(data['city'], 'Springfield')
        data['location_id'] = 'mx-city'
        with self.assertRaises(GatewayValidationError): asyncio.run(app.validate_binding(data))


class CapabilityTests(unittest.TestCase):
    def test_api_key_uses_live_balances_and_caches(self):
        client = URClient(api_key='test-fake-key-opaque')
        calls = []
        def request(path, **kw):
            calls.append(path)
            return {'active_transfer_balances': [{'pro': True, 'start_time': '2026-01-01T00:00:00Z', 'end_time': '2099-01-01T00:00:00Z'}]}
        client._request = request
        self.assertTrue(client.get_proxy_capabilities()['is_pro'])
        self.assertEqual(client.get_proxy_capabilities()['active_limit'], 1000)
        self.assertEqual(calls, ['/subscription/balance'])

    def test_expired_pro_and_unverified_jwt_do_not_enable_pro(self):
        client = URClient()
        client.jwt_payload = {'pro': True}
        client._request = lambda *a, **kw: {'active_transfer_balances': [{'pro': True, 'start_time': '2000-01-01T00:00:00Z', 'end_time': '2001-01-01T00:00:00Z'}]}
        self.assertFalse(client.get_proxy_capabilities()['is_pro'])

    def test_unknown_capabilities_are_conservative(self):
        client = URClient()
        client._request = lambda *a, **kw: (_ for _ in ()).throw(ApiError(503))
        result = client.get_proxy_capabilities()
        self.assertFalse(result['known'])
        self.assertEqual(result['creates_per_minute'], 5)


if __name__ == '__main__': unittest.main()
