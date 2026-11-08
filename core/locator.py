"""Official provider locations, with explicit provenance and stale-cache semantics."""
import json
import re
from collections import OrderedDict
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


SEARCH_SOURCE = '/network/find-provider-locations'
LOCATION_FIELDS = ('location_id', 'location_type', 'name', 'city', 'region', 'country',
                   'country_code', 'city_location_id', 'region_location_id', 'country_location_id')


def search_locations(query: str, api_base='https://api.bringyour.com', timeout=15,
                     client=None, *, country_code='', location_type='', region_location_id='') -> list[dict]:
    """Search canonical city/region records, filtering type, country and parent IDs."""
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 128:
        raise ValueError('位置搜索须为 1–128 个字符')
    code = str(country_code or '').strip().upper()
    if code and not re.fullmatch(r'[A-Z]{2}', code):
        raise ValueError('国家代码须为两位字母')
    if location_type not in ('', 'country', 'region', 'city'):
        raise ValueError('位置类型须为 country、region 或 city')
    if not isinstance(region_location_id, str) or len(region_location_id) > 256:
        raise ValueError('地区标识无效')
    result = _request(SEARCH_SOURCE, api_base=api_base, timeout=timeout, client=client,
                      body={'query': query.strip(), 'max_distance_fraction': 1,
                            'enable_max_distance_fraction': True})
    locations = result.get('locations')
    if not isinstance(locations, list):
        raise ValueError('Missing locations in provider response')
    output = []
    seen = set()
    for raw in locations:
        if not isinstance(raw, dict):
            raise ValueError('Invalid location record')
        typ, cid = raw.get('location_type'), raw.get('location_id')
        country = str(raw.get('country_code', '')).upper()
        if typ not in ('country', 'region', 'city') or not isinstance(cid, str) or not 1 <= len(cid) <= 256:
            continue
        if not re.fullmatch(r'[A-Z]{2}', country):
            continue
        if location_type and typ != location_type or code and country != code:
            continue
        if region_location_id and raw.get('region_location_id') != region_location_id:
            continue
        count = raw.get('provider_count', 0)
        if type(count) is not int or count < 0:
            raise ValueError('Invalid provider count')
        loc = {key: str(raw.get(key) or '')[:256] for key in LOCATION_FIELDS}
        loc.update(country_code=country, provider_count=count, stable=bool(raw.get('stable')),
                   strong_privacy=bool(raw.get('strong_privacy')))
        if cid not in seen:
            output.append(loc)
            seen.add(cid)
    return sorted(output, key=lambda loc: (-loc['provider_count'], loc['name'], loc['location_id']))


def search_cities(query, api_base='https://api.bringyour.com', timeout=15, client=None):
    return search_locations(query, api_base, timeout, client, location_type='city')


class CountryCatalog:
    def __init__(self, client, ttl=60):
        self.client, self.ttl = client, ttl
        self._lock = threading.Lock()
        self._countries, self._updated_at, self._time = [], None, 0
        self._error, self._last_attempt = None, 0
        self._searches = OrderedDict()

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

    def search(self, query: str, country_code: str = '', location_type: str = '',
               region_location_id: str = '', refresh: bool = False) -> dict:
        """Bounded location snapshots with explicit freshness and no synthetic IDs."""
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 128:
            raise ValueError('位置搜索须为 1–128 个字符')
        code = str(country_code or '').strip().upper()
        if code and not re.fullmatch(r'[A-Z]{2}', code):
            raise ValueError('国家代码须为两位字母')
        if location_type not in ('', 'country', 'region', 'city'):
            raise ValueError('位置类型无效')
        if not isinstance(region_location_id, str) or len(region_location_id) > 256:
            raise ValueError('地区标识无效')
        key = (query.strip(), code, location_type, region_location_id)
        with self._lock:
            now = time.monotonic()
            entry = self._searches.get(key, dict(locations=[], time=0, attempted=0, updated_at=None, error=None))
            if refresh or not entry['time'] or now - entry['time'] >= self.ttl:
                if refresh or now - entry['attempted'] >= 5:
                    entry['attempted'] = now
                    try:
                        entry['locations'] = search_locations(key[0], client=self.client,
                            country_code=code, location_type=location_type, region_location_id=region_location_id)
                        entry.update(time=time.monotonic(), updated_at=datetime.now(timezone.utc).isoformat(), error=None)
                    except Exception as exc:
                        entry['error'] = f'位置数据刷新失败 ({type(exc).__name__})'
            self._searches[key] = entry
            self._searches.move_to_end(key)
            while len(self._searches) > 64:
                self._searches.popitem(last=False)
            return dict(locations=[dict(loc) for loc in entry['locations']], source=SEARCH_SOURCE,
                        updated_at=entry['updated_at'], stale=bool(entry['error']) or not entry['time']
                        or time.monotonic() - entry['time'] >= self.ttl, error=entry['error'])

    def resolve(self, location_id: str, country_code: str, location_type: str,
                query: str = '', region_location_id: str = '') -> dict | None:
        """Resolve only a fresh official record, never trust caller-supplied geography."""
        if query:
            snapshot = self.search(query, country_code, location_type, region_location_id)
            if snapshot['stale']:
                return None
            return next((loc for loc in snapshot['locations'] if loc['location_id'] == location_id), None)
        with self._lock:
            now = time.monotonic()
            for entry in reversed(self._searches.values()):
                if entry['error'] or not entry['time'] or now - entry['time'] >= self.ttl:
                    continue
                for loc in entry['locations']:
                    if (loc['location_id'] == location_id and loc['country_code'] == country_code.upper()
                            and loc['location_type'] == location_type
                            and (not region_location_id or loc['region_location_id'] == region_location_id)):
                        return dict(loc)
        return None

    def invalidate(self):
        with self._lock:
            self._countries, self._updated_at, self._time = [], None, 0
            self._error, self._last_attempt = None, 0
            self._searches.clear()

_COUNTRY_RESIDENTIAL_LOCS = {}
_COUNTRY_RESIDENTIAL_LOCK = threading.Lock()


def get_residential_locations(country_code, api_base='https://api.bringyour.com', timeout=15, client=None):
    """Return active city/region locations in a country, excluding known datacenter hubs (e.g. Ashburn VA)."""
    code = (country_code or 'US').strip().upper()
    with _COUNTRY_RESIDENTIAL_LOCK:
        if code in _COUNTRY_RESIDENTIAL_LOCS and _COUNTRY_RESIDENTIAL_LOCS[code]:
            return list(_COUNTRY_RESIDENTIAL_LOCS[code])

    country_names = {
        'US': 'United States', 'JP': 'Japan', 'GB': 'United Kingdom',
        'DE': 'Germany', 'CA': 'Canada', 'FR': 'France', 'SG': 'Singapore',
        'AU': 'Australia', 'KR': 'South Korea', 'TW': 'Taiwan', 'HK': 'Hong Kong'
    }
    query = country_names.get(code, code)
    try:
        result = _request('/network/find-provider-locations', api_base=api_base, timeout=timeout,
                          client=client, body={'query': query, 'max_distance_fraction': 1,
                                              'enable_max_distance_fraction': True})
        locs = result.get('locations', [])
        candidates = []
        for l in locs:
            if l.get('location_type') not in ('city', 'region'):
                continue
            if l.get('country_code', '').upper() != code:
                continue
            if l.get('provider_count', 0) <= 0:
                continue
            reg = (l.get('region') or '').lower()
            name = (l.get('name') or '').lower()
            # For US, filter out Virginia/Ashburn/Dulles datacenter hubs
            if code == 'US' and (reg == 'virginia' or 'ashburn' in name or 'dulles' in name):
                continue
            candidates.append(l)

        if candidates:
            with _COUNTRY_RESIDENTIAL_LOCK:
                _COUNTRY_RESIDENTIAL_LOCS[code] = candidates
            return list(candidates)
    except Exception:
        pass
    return []
