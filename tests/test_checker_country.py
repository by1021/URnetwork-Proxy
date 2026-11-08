import asyncio
import io
import json
import threading
import unittest
from unittest.mock import Mock, patch

from core import checker


def response(data, url="https://ipinfo.io/fixture/json"):
    stream = io.BytesIO(json.dumps(data).encode())
    stream.geturl = lambda: url
    return stream


class IpCountryTests(unittest.TestCase):
    def setUp(self):
        checker._IP_INFO_CACHE.clear()

    def tearDown(self):
        checker._IP_INFO_CACHE.clear()

    def test_ip_api_quality_and_https_country_are_combined(self):
        opener = Mock()
        opener.open.side_effect = [response({"status": "success", "countryCode": "VN", "isp": "Fixture ISP"}),
                                  response({"country": "US", "org": "Fixture cloud hosting"})]
        with patch("urllib.request.build_opener", return_value=opener):
            info = checker.inspect_ip_quality("203.0.113.1")
        self.assertNotIn("countryCode", opener.open.call_args_list[0].args[0].full_url)
        self.assertTrue(opener.open.call_args_list[1].args[0].full_url.startswith("https://ipinfo.io/"))
        self.assertEqual(info.get("isp"), "Fixture ISP")
        self.assertTrue(info.get("is_residential"))
        self.assertEqual(info.get("country_code"), "US")
        self.assertEqual(info.get("country_source"), "ipinfo.io")

    def test_plain_http_country_cannot_override_https_country(self):
        opener = Mock()
        opener.open.side_effect = [response({"status": "success", "countryCode": "US"}),
                                  response({"country": "VN"})]
        with patch("urllib.request.build_opener", return_value=opener):
            info = checker.inspect_ip_quality("203.0.113.5")
            cached = checker.inspect_ip_quality("203.0.113.5")
        self.assertEqual(info["country_code"], "VN")
        self.assertEqual(cached["country_code"], "VN")
        self.assertEqual(info["country_source"], "ipinfo.io")
        self.assertEqual(opener.open.call_count, 2)

    def test_plain_http_country_is_not_cached_when_https_fails(self):
        opener = Mock()
        opener.open.side_effect = [response({"status": "success", "countryCode": "US"}),
                                  OSError("fixture TLS outage")]
        with patch("urllib.request.build_opener", return_value=opener):
            info = checker.inspect_ip_quality("203.0.113.6")
        self.assertFalse(info["country_code"])
        self.assertFalse(info["country_source"])
        self.assertNotIn("203.0.113.6", checker._IP_INFO_CACHE)

    def test_https_redirect_to_plain_http_is_not_trusted(self):
        opener = Mock()
        opener.open.side_effect = [OSError("fixture quality outage"),
                                  response({"country": "US"}, url="http://ipinfo.io/fixture/json")]
        with patch("urllib.request.build_opener", return_value=opener):
            info = checker.inspect_ip_quality("203.0.113.7")
        self.assertFalse(info["country_code"])
        self.assertNotIn("203.0.113.7", checker._IP_INFO_CACHE)

    def test_ipinfo_fallback_normalizes_country(self):
        opener = Mock()
        opener.open.side_effect = [OSError("fixture outage"), response({"country": "vn", "org": "Fixture ISP"})]
        with patch("urllib.request.build_opener", return_value=opener):
            info = checker.inspect_ip_quality("203.0.113.2")
        self.assertEqual(info.get("country_code"), "VN")
        self.assertEqual(info.get("country_source"), "ipinfo.io")

    def test_failed_lookup_does_not_poison_future_country_verification(self):
        opener = Mock()
        opener.open.side_effect = [OSError("fixture outage"), OSError("fixture outage"),
                                  response({"status": "success", "countryCode": "US"}),
                                  response({"country": "US"})]
        with patch("urllib.request.build_opener", return_value=opener):
            first = checker.inspect_ip_quality("203.0.113.3")
            second = checker.inspect_ip_quality("203.0.113.3")
        self.assertFalse(first.get("country_code"))
        self.assertEqual(second.get("country_code"), "US")

    def test_successful_country_cache_expires(self):
        opener = Mock()
        opener.open.side_effect = [response({"status": "success", "countryCode": "US"}),
                                  response({"country": "US"}),
                                  response({"status": "success", "countryCode": "VN"}),
                                  response({"country": "VN"})]
        with patch("urllib.request.build_opener", return_value=opener), \
             patch.object(checker.time, "monotonic", return_value=10) as clock:
            self.assertEqual(checker.inspect_ip_quality("203.0.113.4")["country_code"], "US")
            clock.return_value = 311
            self.assertEqual(checker.inspect_ip_quality("203.0.113.4")["country_code"], "VN")


class ProtocolCountryTests(unittest.IsolatedAsyncioTestCase):
    async def test_python310_asyncio_timeout_is_not_builtin_timeout(self):
        class LegacyAsyncioTimeout(Exception):
            pass

        original_wait_for = asyncio.wait_for
        original_timeout = asyncio.TimeoutError
        release = threading.Event()

        async def legacy_wait_for(awaitable, timeout):
            try:
                return await original_wait_for(awaitable, timeout)
            except (original_timeout, TimeoutError) as error:
                raise LegacyAsyncioTimeout from error

        def slow_failure(*args, **kwargs):
            release.wait(2)
            return False, None, "OSError"

        # Before Python 3.11 asyncio used a distinct timeout exception type.
        with patch.object(checker, "test_http_proxy", return_value=(True, 1, "203.0.113.12")), \
             patch.object(checker, "test_socks5_proxy", side_effect=slow_failure), \
             patch.object(checker, "inspect_ip_quality", return_value={"country_code": "VN", "country_source": "fixture"}), \
             patch.object(asyncio, "TimeoutError", LegacyAsyncioTimeout), \
             patch.object(asyncio, "wait_for", side_effect=legacy_wait_for):
            try:
                result = await checker.probe_upstream(
                    {"host": "proxy.example.test", "auth_token": "fixture", "http_port": 80, "socks_port": 1080},
                    timeout=0.05)
            finally:
                release.set()
        self.assertFalse(result["ok"])
        self.assertEqual(result["country_codes"], {"http": "VN"})

    async def test_slow_failed_protocol_has_a_bounded_probe_budget(self):
        release = threading.Event()

        def slow_failure(*args, **kwargs):
            release.wait(2)
            return False, None, "OSError"

        with patch.object(checker, "test_http_proxy", return_value=(True, 1, "203.0.113.11")), \
             patch.object(checker, "test_socks5_proxy", side_effect=slow_failure), \
             patch.object(checker, "inspect_ip_quality", return_value={"country_code": "VN", "country_source": "fixture"}):
            try:
                result = await asyncio.wait_for(checker.probe_upstream(
                    {"host": "proxy.example.test", "auth_token": "fixture", "http_port": 80, "socks_port": 1080},
                    timeout=0.05), timeout=0.5)
            finally:
                release.set()
        self.assertFalse(result["ok"])
        self.assertEqual(result["country_codes"], {"http": "VN"})

    async def test_successful_protocol_country_is_checked_despite_other_failure(self):
        with patch.object(checker, "test_http_proxy", return_value=(True, 1, "203.0.113.8")), \
             patch.object(checker, "test_socks5_proxy", return_value=(False, None, "OSError")), \
             patch.object(checker, "inspect_ip_quality", return_value={"country_code": "VN", "country_source": "fixture"}) as inspect:
            result = await checker.probe_upstream({"host": "proxy.example.test", "auth_token": "fixture",
                                                  "http_port": 80, "socks_port": 1080})
        self.assertFalse(result["ok"])
        self.assertTrue(result["http_ok"])
        self.assertFalse(result["socks_ok"])
        self.assertEqual(result["country_code"], "VN")
        self.assertEqual(result["country_codes"], {"http": "VN"})
        self.assertEqual(inspect.call_count, 1)

    async def test_one_failed_geo_lookup_does_not_hide_other_country(self):
        def quality(ip):
            if ip == "203.0.113.9":
                return {"country_code": "VN", "country_source": "fixture"}
            raise OSError("fixture lookup failure")

        with patch.object(checker, "test_http_proxy", return_value=(True, 1, "203.0.113.9")), \
             patch.object(checker, "test_socks5_proxy", return_value=(True, 1, "203.0.113.10")), \
             patch.object(checker, "inspect_ip_quality", side_effect=quality):
            result = await checker.probe_upstream({"host": "proxy.example.test", "auth_token": "fixture",
                                                  "http_port": 80, "socks_port": 1080})
        self.assertEqual(result["country_codes"], {"http": "VN", "socks": ""})

    async def test_all_protocol_exit_countries_are_reported(self):
        def quality(ip):
            return {"country_code": "VN" if ip == "203.0.113.1" else "US",
                    "country_source": "fixture", "is_residential": True}

        with patch.object(checker, "test_http_proxy", return_value=(True, 1, "203.0.113.1")), \
             patch.object(checker, "test_socks5_proxy", return_value=(True, 1, "203.0.113.2")), \
             patch.object(checker, "test_https_proxy", return_value=(True, 1, "203.0.113.2")), \
             patch.object(checker, "inspect_ip_quality", side_effect=quality) as inspect:
            result = await checker.probe_upstream({"host": "proxy.example.test", "auth_token": "fixture",
                                                  "http_port": 80, "socks_port": 1080, "https_port": 443})
        self.assertEqual(result.get("country_codes"), {"http": "VN", "socks": "US", "https": "US"})
        self.assertEqual(result.get("country_code"), "US")
        self.assertEqual(inspect.call_count, 2)
