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
