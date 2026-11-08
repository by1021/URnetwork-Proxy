import unittest
from unittest.mock import patch

from core.client import URClient


class ClientLocationTests(unittest.TestCase):
    def setUp(self):
        self.client = URClient()
        self.requests = []

        def request(path, method="GET", body=None, **kwargs):
            self.requests.append((path, body))
            return {"client_id": "fixture-client", "proxy_config_result": {
                "proxy_host": "proxy.example.test", "auth_token": "fixture-token",
                "http_proxy_port": 80, "socks_proxy_port": 1080, "https_proxy_port": 443,
                "http_proxy_url": "http://proxy.example.test",
                "https_proxy_url": "https://proxy.example.test",
                "socks_proxy_url": "socks5://proxy.example.test",
            }}

        self.client._request = request

    def initial_state(self):
        return self.requests[-1][1]["proxy_config"]["initial_device_state"]

    def test_explicit_country_location_is_not_overridden_by_residential_selection(self):
        city = {"location_id": "us-city", "country_code": "us", "name": "Fixture city"}
        with patch("core.locator.get_residential_locations", return_value=[city]) as search:
            result = self.client.create_proxy(mode="country", target="US", location_id="us-country")
        self.assertEqual(self.initial_state()["location"]["connect_location_id"], {"location_id": "us-country"})
        search.assert_not_called()
        self.assertEqual(result["location_id"], "us-country")

    def test_country_code_is_normalized_at_both_wire_levels_and_in_result(self):
        self.client.create_proxy(mode="country", target=" us ", location_id="us-country", residential_only=False)
        state = self.initial_state()
        self.assertEqual(state.get("country_code"), "us")
        self.assertEqual(state["location"]["country_code"], "us")
        result = self.client.create_proxy(mode="country", target=" us ", location_id="us-country", residential_only=False)
        self.assertEqual(result["country_code"], "US")

    def test_country_lookup_returns_resolved_location(self):
        with patch("core.locator.fetch_live_countries", return_value=[{"code": "US", "location_id": "us-country"}]):
            result = self.client.create_proxy(mode="country", target="US", residential_only=False)
        self.assertEqual(result["location_id"], "us-country")
        self.assertEqual(self.initial_state().get("country_code"), "us")

    def test_cli_residential_city_returns_actual_resolved_location(self):
        city = {"location_id": "us-city", "country_code": "us", "name": "Fixture city"}
        with patch("core.locator.get_residential_locations", return_value=[city]):
            result = self.client.create_proxy(mode="country", target="US")
        self.assertEqual(result["location_id"], "us-city")
        self.assertEqual(self.initial_state().get("country_code"), "us")

    def test_best_mode_remains_unbound(self):
        self.client.create_proxy(mode="best")
        self.assertEqual(self.initial_state(), {"location": {"connect_location_id": {"best_available": True}}})

class PreciseLocationTests(unittest.TestCase):
    setUp = ClientLocationTests.setUp
    initial_state = ClientLocationTests.initial_state
    def test_city_exact_id_wins_without_search(self):
        with patch('core.locator.search_locations') as search:
            result = self.client.create_proxy(mode='city', target='US', location_id='us-city-2',
                location_name='Springfield', city='Springfield', region='Illinois', region_location_id='us-il')
        search.assert_not_called()
        self.assertEqual(result['location_id'], 'us-city-2')
        self.assertEqual(result['country_code'], 'US')
        self.assertEqual(self.initial_state()['location']['location_type'], 'city')

    def test_region_exact_id_is_supported(self):
        result = self.client.create_proxy(mode='region', target='US', location_id='us-ca', location_name='California')
        self.assertEqual(result['location_type'], 'region')
        self.assertEqual(self.initial_state()['location']['connect_location_id']['location_id'], 'us-ca')

    def test_ambiguous_city_name_requires_selection(self):
        matches = [{'location_id': 'us-a', 'name': 'Springfield', 'country_code': 'us'},
                   {'location_id': 'ca-a', 'name': 'Springfield', 'country_code': 'ca'}]
        with patch('core.locator.search_locations', return_value=matches):
            with self.assertRaises(ValueError): self.client.create_proxy(mode='city', target='Springfield')
        self.assertFalse(any(path == '/network/auth-client' for path, _ in self.requests))

    def test_named_locations_filter_same_names_by_normalized_country(self):
        original_request = self.client._request
        for mode in ('city', 'region'):
            with self.subTest(mode=mode):
                def request(path, **kwargs):
                    if path == '/network/find-provider-locations':
                        return {'locations': [
                            {'location_id':f'{country}-{mode}', 'location_type':mode,
                             'country_code':country, 'name':'Tokyo', 'provider_count':5}
                            for country in ('us', 'jp')]}
                    return original_request(path, **kwargs)
                self.client._request = request
                result = self.client.create_proxy(mode=mode, target='Tokyo', country_code=' jp ')
                self.assertEqual(result['location_id'], f'jp-{mode}')
                self.assertEqual(result['country_code'], 'JP')
                self.assertEqual(self.initial_state()['country_code'], 'jp')

    def test_invalid_named_location_country_is_rejected_before_any_request(self):
        for mode in ('city', 'region'):
            with self.subTest(mode=mode):
                with self.assertRaises(ValueError):
                    self.client.create_proxy(mode=mode, target='Tokyo', country_code='JAPAN')
        self.assertEqual(self.requests, [])

    def test_pro_create_defaults_to_auto_and_returns_exit(self):
        self.client.get_proxy_capabilities = lambda: {'is_pro': True, 'unique_exit': True}
        result = self.client.create_proxy(mode='country', target='US', location_id='us-country')
        proxy = self.requests[-1][1]['proxy_config']
        self.assertTrue(proxy['unique_exit'])
        self.assertEqual(proxy['unique_exit_replace'], 'auto')
        self.assertTrue(result['unique_exit'])

    def test_unique_failure_is_not_replayed(self):
        from core.client import ApiError
        self.client.get_proxy_capabilities = lambda: {'is_pro': True, 'unique_exit': True}
        requests = []
        def fail(path, **kw):
            requests.append(path)
            raise ApiError(200, 'rejected', code='no_unique_supply')
        self.client._request = fail
        with self.assertRaises(ApiError): self.client.create_proxy(mode='country', target='US', location_id='us-country')
        self.assertEqual(requests, ['/network/auth-client'])


class CLILocationTests(unittest.TestCase):
    def test_cli_forwards_country_filter_for_named_city_and_region(self):
        from unittest.mock import MagicMock
        import main
        for mode in ('city', 'region'):
            with self.subTest(mode=mode):
                client = MagicMock()
                argv = ['main.py', '--mode', mode, '--target', 'Tokyo',
                        '--country-code', 'JP', '--api-key', 'test-fake-key', '--no-check']
                with patch('sys.argv', argv), patch.object(main, 'load_config', return_value={}), \
                        patch.object(main, 'URClient', return_value=client), \
                        patch.object(main, 'display_proxy_result'):
                    main.main()
                client.create_proxy.assert_called_once_with(mode=mode, target='Tokyo', country_code='JP')
