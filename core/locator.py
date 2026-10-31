"""Official provider locations, with explicit provenance and stale-cache semantics."""
import json
import threading
import time
import urllib.request
from datetime import datetime, timezone

SOURCE = '/network/provider-locations'


def _request(path, *, api_base, timeout, client=None, body=None):
    if client is not None:
        return client._request(path, method='GET' if body is None else 'POST', body=body)
    req = urllib.request.Request(api_base.rstrip('/') + path,
        data=None if body is None else json.dumps(body).encode(),
        headers={'Content-Type': 'application/json', 'User-Agent': 'URnetwork-Proxy/2.0'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as response:
        result = json.load(response)
    if not isinstance(result, dict) or result.get('error'):
        raise ValueError('Invalid provider locations response')
    return result


def fetch_live_countries(ur_base='https://ur.io', api_base='https://api.bringyour.com', timeout=15, client=None):
    """Return all country locations, never blend public-map counts into this snapshot."""
    result = _request(SOURCE, api_base=api_base, timeout=timeout, client=client)
    locations = result.get('locations')
    if not isinstance(locations, list):
        raise ValueError('Missing locations in provider response')
    countries = {}
    for loc in locations:
        if loc.get('location_type') != 'country':
            continue
        code = str(loc.get('country_code', '')).upper()
        if len(code) != 2 or not code.isascii() or not code.isalpha() or not loc.get('location_id'):
            continue
        count = loc.get('provider_count', 0)
        if not isinstance(count, int) or count < 0:
            raise ValueError('Invalid provider count')
        countries[code] = dict(code=code, name=loc.get('name') or loc.get('country') or code,
            providers=count, location_id=loc['location_id'],
            stable=bool(loc.get('stable')), strong_privacy=bool(loc.get('strong_privacy')))
    return sorted(countries.values(), key=lambda c: (-c['providers'], c['code']))


def search_cities(query, api_base='https://api.bringyour.com', timeout=15, client=None):
    result = _request('/network/find-provider-locations', api_base=api_base, timeout=timeout,
                      client=client, body={'query': query, 'max_distance_fraction': 1,
                                          'enable_max_distance_fraction': True})
    return [loc for loc in result.get('locations', []) if loc.get('location_type') == 'city']


class CountryCatalog:
    def __init__(self, client, ttl=60):
        self.client, self.ttl = client, ttl
        self._lock = threading.Lock()
        self._countries, self._updated_at, self._time = [], None, 0
        self._error, self._last_attempt = None, 0

    def get(self, refresh=False):
        with self._lock:
            now = time.monotonic()
            expired = not self._time or now - self._time >= self.ttl
            if refresh or (expired and now - self._last_attempt >= 5):
                self._last_attempt = now
                try:
                    self._countries = fetch_live_countries(client=self.client)
                    self._updated_at = datetime.now(timezone.utc).isoformat()
                    self._time = time.monotonic()
                    self._error = None
                except Exception as exc:
                    self._error = f'位置数据刷新失败 ({type(exc).__name__})'
            return dict(countries=[dict(c) for c in self._countries], source=SOURCE,
                        updated_at=self._updated_at, stale=bool(self._error) or not self._time or time.monotonic()-self._time >= self.ttl,
                        error=self._error)

    def invalidate(self):
        with self._lock:
            self._countries, self._updated_at, self._time = [], None, 0
            self._error, self._last_attempt = None, 0
