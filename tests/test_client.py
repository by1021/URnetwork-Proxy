import io
import json
import unittest
import urllib.error
from unittest.mock import Mock, patch
from core.client import URClient, ApiError


def response(data):
    obj = Mock()
    obj.__enter__ = Mock(return_value=obj)
    obj.__exit__ = Mock(return_value=False)
    obj.read.return_value = json.dumps(data).encode()
    return obj


class ClientTests(unittest.TestCase):
    def test_no_creation_replay_on_timeout(self):
        client = URClient(api_key="fixture")
        client.jwt = "fixture"
        opener = Mock()
        opener.open.side_effect = TimeoutError()
        with patch("urllib.request.build_opener", return_value=opener):
            with self.assertRaises(ApiError):
                client._request("/network/auth-client", method="POST", body={})
        self.assertEqual(opener.open.call_count, 1)

    def test_get_auth_refresh_once(self):
        client = URClient(seedphrase="fixture")
        client.jwt = "expired"
        client.login = Mock(side_effect=lambda: setattr(client, "jwt", "new-session"))
        opener = Mock()
        opener.open.side_effect = [urllib.error.HTTPError("http://test/", 401, "expired", {}, io.BytesIO()), response({"ok": True})]
        with patch("urllib.request.build_opener", return_value=opener):
            self.assertTrue(client._request("/network/user")["ok"])
        client.login.assert_called_once()
        self.assertEqual(opener.open.call_count, 2)

    def test_proxy_list_does_not_hide_auth_failure(self):
        client = URClient()
        client._request = Mock(side_effect=ApiError(401, "authentication"))
        with self.assertRaises(ApiError):
            client.list_proxies()
        self.assertEqual(client._request.call_count, 1)

    def test_country_binding_uses_location_id(self):
        client = URClient()
        client._request = Mock(return_value=dict(client_id="c", proxy_config_result=dict(
            proxy_host="fixture", auth_token="token", http_proxy_port=80,
            socks_proxy_port=1080, https_proxy_port=443,
            http_proxy_url="", https_proxy_url="", socks_proxy_url="")))
        client.create_proxy(mode="country", target="JP", location_id="japan-id")
        payload = client._request.call_args.kwargs["body"]
        state = payload["proxy_config"]["initial_device_state"]
        self.assertEqual(state["location"]["connect_location_id"]["location_id"], "japan-id")
        self.assertNotIn("best_available", str(state))

    def test_empty_city_never_falls_back_to_best(self):
        client = URClient()
        with patch("core.client.search_cities", return_value=[]):
            with self.assertRaises(ValueError):
                client.create_proxy(mode="city", target="Nowhere")
