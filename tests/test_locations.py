import unittest
from unittest.mock import Mock
from core.locator import fetch_live_countries, CountryCatalog, search_cities


class LocationTests(unittest.TestCase):
    def test_all_countries_and_exact_counts(self):
        locations = [dict(location_type="country", country_code="a" + chr(97 + n),
                          location_id=str(n), name=str(n), provider_count=n) for n in range(26)]
        locations += [dict(location_type="city", country_code="us", location_id="city", provider_count=999)]
        client = Mock()
        client._request.return_value = {"locations": locations}
        rows = fetch_live_countries(client=client)
        self.assertEqual(len(rows), 26)
        self.assertEqual(rows[0]["providers"], 25)
        self.assertNotIn("city", [c["location_id"] for c in rows])
        client._request.assert_called_once_with("/network/provider-locations", method="GET", body=None)

    def test_stale_cache_preserved_with_visible_error(self):
        client = Mock()
        client._request.return_value = {"locations": [dict(location_type="country", country_code="jp", location_id="j", provider_count=7)]}
        catalog = CountryCatalog(client)
        before = catalog.get(refresh=True)
        client._request.side_effect = OSError("offline")
        after = catalog.get(refresh=True)
        self.assertEqual(before["countries"], after["countries"])
        self.assertEqual(before["updated_at"], after["updated_at"])
        self.assertTrue(after["stale"])
        self.assertIsNotNone(after["error"])

    def test_city_search_filters_countries(self):
        client = Mock()
        client._request.return_value = {"locations": [
            {"location_type": "country"}, {"location_type": "city", "location_id": "c"}]}
        self.assertEqual(search_cities("Tokyo", client=client), [{"location_type": "city", "location_id": "c"}])
