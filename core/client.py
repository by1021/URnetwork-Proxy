#!/usr/bin/env python3
"""
URnetwork 核心 API 客户端
封装多重认证登录 (API Key / JWT Token / 网页授权码 / 助记词 / 密码)、
账户配额、代理节点申请与生命周期管理
"""

import urllib.request
import json
import base64
import threading
import urllib.error
import time
import re
from datetime import datetime, timezone

API_ERROR_CODES = frozenset(('no_unique_supply', 'client_limit_exceeded', 'rate_limited',
    'upgrade_required', 'authentication', 'invalid_location', 'rejected'))


def _api_error_code(error: object) -> str:
    if isinstance(error, dict):
        code = error.get('code')
        if isinstance(code, str) and code in API_ERROR_CODES:
            return code
        return next((key for key in ('no_unique_supply', 'rate_limited', 'client_limit_exceeded', 'upgrade_required')
                     if error.get(key) is True), 'rejected')
    return error if isinstance(error, str) and error in API_ERROR_CODES else 'rejected'


def _retry_after(value: object) -> int:
    try:
        return min(3600, max(0, int(value)))
    except (TypeError, ValueError):
        return 0


def _proxy_exit(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    return {key: str(value[key])[:256] for key in ('exit_id', 'country_code', 'region', 'city',
        'exit_since', 'state', 'state_reason', 'replace_mode') if isinstance(value.get(key), str)}


class ApiError(RuntimeError):
    """Sanitized control-plane failure; never reflect credentials/remote bodies."""
    def __init__(self, status=0, kind="request", *, code="", retry_after=0):
        self.status = status
        self.kind = kind
        self.code = code if code in API_ERROR_CODES else ""
        self.retry_after = _retry_after(retry_after)
        super().__init__(f"URnetwork API {kind} (HTTP {status})")


class URClient:
    def __init__(
        self,
        api_key="",
        jwt_token="",
        seedphrase="",
        auth_code="",
        email="",
        password="",
        api_base="https://api.bringyour.com",
        timeout=15
    ):
        self._lock = threading.RLock()
        self._resolving = False
        self.api_key = (api_key or "").strip()
        self.jwt_token = (jwt_token or "").strip()
        self.seedphrase = (seedphrase or "").strip()
        self.auth_code = (auth_code or "").strip()
        self.email = (email or "").strip()
        self.password = (password or "").strip()
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout
        self.jwt = None
        self.jwt_payload = {}
        self.network_info = {}
        self.auth_method = "none"
        self._capability_cache = None

    def _request(self, path, method="GET", body=None, auth=True):
        with self._lock:
            if auth and not self.jwt:
                self.login()
            for attempt in range(2):
                headers = {"Content-Type": "application/json", "User-Agent": "URnetwork-Proxy/2.0"}
                if auth:
                    headers["Authorization"] = f"Bearer {self.jwt}"
                data = json.dumps(body).encode() if body is not None else None
                req = urllib.request.Request(self.api_base + path, data=data, headers=headers, method=method)
                try:
                    # Ignore OS proxy variables: a managed proxy may itself be offline.
                    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                    with opener.open(req, timeout=self.timeout) as resp:
                        content = resp.read(8 * 1024 * 1024 + 1)
                    if len(content) > 8 * 1024 * 1024:
                        raise ApiError(0, "response-too-large")
                    result = json.loads(content) if content else {}
                    if not isinstance(result, dict):
                        raise ApiError(0, "invalid-response")
                    if result.get("error"):
                        raise ApiError(200, "rejected", code=_api_error_code(result["error"]))
                    return result
                except urllib.error.HTTPError as exc:
                    status = exc.code
                    retry_after = _retry_after(exc.headers.get('Retry-After'))
                    code = ''
                    try:
                        error_body = json.loads(exc.read(8192))
                        code = _api_error_code(error_body.get('error')) if isinstance(error_body, dict) else ''
                    except (ValueError, OSError):
                        code = ''
                    finally:
                        exc.close()
                    renewable = not self.api_key and not self.jwt_token and bool(self.seedphrase or (self.email and self.password))
                    if status == 401 and auth and not self._resolving:
                        self.jwt = None
                        # Replay only GET after explicit authentication rejection, never creation POSTs.
                        if method == "GET" and attempt == 0 and renewable:
                            self.login()
                            continue
                    raise ApiError(status, "authentication" if status in (401, 403) else "request",
                                   code=code, retry_after=retry_after) from None
                except (OSError, ValueError) as exc:
                    raise ApiError(0, type(exc).__name__) from None

    def _resolve_session_info(self):
        """解析 Token Payload 并向官方接口查询 network_user 详情"""
        # 尝试解码 JWT Payload (如果是 urn_... 格式的 API Key 则无法 base64 解码，跳过即可)
        self.jwt_payload = {}
        try:
            if self.jwt and "." in self.jwt:
                parts = self.jwt.split(".")
                if len(parts) >= 2:
                    padded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
                    self.jwt_payload = json.loads(base64.urlsafe_b64decode(padded))
        except Exception:
            pass

        # 通过 /network/user 获取网络详情
        self._resolving = True
        try:
            u_info = self._request("/network/user", method="GET")
            nu = u_info.get("network_user", {})
            self.network_info = {
                "network_id": nu.get("network_id") or self.jwt_payload.get("network_id"),
                "network_name": nu.get("network_name") or self.jwt_payload.get("network_name") or "unknown",
                "user_id": nu.get("user_id") or self.jwt_payload.get("user_id"),
                "auth_types": nu.get("auth_types", [])
            }
        except Exception:
            self.network_info = {
                "network_id": self.jwt_payload.get("network_id"),
                "network_name": self.jwt_payload.get("network_name") or "unknown",
                "user_id": self.jwt_payload.get("user_id"),
                "auth_types": []
            }
        finally:
            self._resolving = False

    def login(self):
        with self._lock:
            return self._login()

    def _login(self):
        """
        统一认证登录接口，按优先级适配不同账号类型：
        1. 官方 API Key (urn_...) - 推荐，长期有效，不随浏览器会话过期
        2. 浏览器 Token (jwt_token / byJwt) - 直接从网页 F12 复制
        3. 网页授权码 (auth_code) - 官网生成 5 分钟授权码换取 JWT
        4. 邮箱独立密码 (email + password) - 仅限在 URnetwork 设置过密码的用户
        5. 恢复助记词 (seedphrase) - 经典 24 词助记词
        """
        # 1. 优先使用 API Key
        if self.api_key:
            self.jwt = self.api_key
            self.auth_method = "api_key"
            self._resolve_session_info()
            return self.network_info

        # 2. 次选浏览器导出的 JWT
        if self.jwt_token:
            self.jwt = self.jwt_token
            self.auth_method = "jwt_token"
            self._resolve_session_info()
            return self.network_info

        # 3. 网页授权码 (Auth Code)
        if self.auth_code:
            payload = {"auth_code": self.auth_code}
            res = self._request("/auth/code-login", method="POST", body=payload, auth=False)
            by_jwt = res.get("by_jwt")
            if not by_jwt:
                raise RuntimeError("授权码登录未返回有效凭证。")
            self.jwt = by_jwt
            self.auth_method = "auth_code"
            self._resolve_session_info()
            return self.network_info

        # 4. 邮箱 + 密码
        if self.email and self.password:
            payload = {
                "user_auth": self.email,
                "password": self.password,
                "verify_otp_numeric": True
            }
            res = self._request("/auth/login-with-password", method="POST", body=payload, auth=False)
            if res.get("verification_required"):
                raise RuntimeError("此账号开启了两步验证，请登录网页端生成 Auth Code 或 API Key。")
            net = res.get("network", {})
            by_jwt = net.get("by_jwt")
            if not by_jwt:
                raise RuntimeError("邮箱密码登录未返回网络凭证。")
            self.jwt = by_jwt
            self.auth_method = "password"
            self._resolve_session_info()
            return self.network_info

        # 5. 助记词登录
        if self.seedphrase:
            payload = {
                "seedphrase": self.seedphrase,
                "result_errors": True
            }
            res = self._request("/auth/login", method="POST", body=payload, auth=False)
            net = res.get("network", {})
            by_jwt = net.get("by_jwt")
            if not by_jwt:
                raise RuntimeError("助记词未返回有效 JWT。")
            self.jwt = by_jwt
            self.auth_method = "seedphrase"
            self._resolve_session_info()
            return self.network_info

        raise ValueError("未检测到有效认证配置！请在 config.json 中配置 api_key, jwt_token 或 seedphrase，或使用 --auth-code 登录。")

    def create_api_key(self, name="URnetwork-Proxy-Client"):
        """为当前账号生成一个长期有效的 API Key (urn_...)"""
        res = self._request("/account/api-key", method="POST", body={"name": name})
        return res

    def list_api_keys(self):
        """列出当前账号名下的所有 API Key"""
        res = self._request("/account/api-keys", method="GET")
        return res.get("api_keys", [])

    def remove_api_key(self, key_id):
        """撤销指定的 API Key"""
        return self._request("/account/api-key/remove", method="POST", body={"id": key_id})

    def generate_seedphrase(self):
        """
        为未创建助记词的账号 (例如 Google / 邮箱注册的账号) 生成专属 24 位助记词
        若账号已有助记词，服务端将返回错误，此时可使用 regenerate_seedphrase
        """
        res = self._request("/auth/generate-seedphrase", method="POST", body={})
        return res.get("seedphrase")

    def regenerate_seedphrase(self):
        """重置并重新生成 24 位助记词"""
        res = self._request("/auth/regenerate-seedphrase", method="POST", body={})
        return res.get("seedphrase")

    def _capabilities_from_balance(self, balance: dict) -> dict:
        """A Pro entitlement is an in-window live balance, not an opaque key claim."""
        now = datetime.now(timezone.utc)
        is_pro = False
        balances = balance.get('active_transfer_balances')
        if isinstance(balances, list):
            for entry in balances:
                if not isinstance(entry, dict) or entry.get('pro') is not True:
                    continue
                try:
                    start = datetime.fromisoformat(entry['start_time'].replace('Z', '+00:00'))
                    end = datetime.fromisoformat(entry['end_time'].replace('Z', '+00:00'))
                    if start.tzinfo is None or end.tzinfo is None:
                        continue
                    is_pro = is_pro or start <= now < end
                except (KeyError, TypeError, ValueError, AttributeError):
                    continue
        return dict(is_pro=is_pro, unique_exit=is_pro, active_limit=1000 if is_pro else 5,
                    creates_per_minute=20 if is_pro else 5, known=isinstance(balances, list))

    def get_proxy_capabilities(self, refresh: bool = False) -> dict:
        """Read live plan capabilities with bounded caching and conservative unknowns."""
        with self._lock:
            now = time.monotonic()
            if not refresh and self._capability_cache and now - self._capability_cache[0] < 60:
                return dict(self._capability_cache[1])
            try:
                result = self._capabilities_from_balance(self._request('/subscription/balance'))
            except ApiError:
                result = dict(is_pro=False, unique_exit=False, active_limit=5, creates_per_minute=5, known=False)
            self._capability_cache = (time.monotonic(), result)
            return dict(result)

    def get_account_status(self):
        """查询账户每日配额与使用量信息"""
        balance_data = self._request("/subscription/balance", method="GET")
        self._capability_cache = (time.monotonic(), self._capabilities_from_balance(balance_data))
        start_bytes = balance_data.get("start_balance_byte_count", 0)
        curr_bytes = balance_data.get("balance_byte_count", 0)
        
        return {
            "network_name": self.network_info.get("network_name", "未知"),
            "network_id": self.network_info.get("network_id"),
            "auth_method": self.auth_method,
            "auth_types": self.network_info.get("auth_types", []),
            "daily_quota_gib": round(start_bytes / (1024**3), 2),
            "remaining_gib": round(curr_bytes / (1024**3), 2),
            "used_gib": round((start_bytes - curr_bytes) / (1024**3), 2),
            "is_guest": balance_data.get("guest", False),
            "plan_tier": balance_data.get("price_tier", {}).get("name", "standard")
        }

    def list_proxies(self):
        """
        获取当前网页端 PROXIES 列表中所有已分配的代理实例
        对应官方接口: GET /network/proxies (404 降级为 GET /network/clients 过滤 proxy_client)
        """
        try:
            res = self._request("/network/proxies", method="GET")
            clients = res.get("clients", [])
        except ApiError as exc:
            if exc.status != 404:
                raise
            res = self._request("/network/clients", method="GET")
            clients = [c for c in res.get("clients", []) if c.get("proxy_client")]

        formatted = []
        for c in clients:
            pc = c.get("proxy_client") or {}
            spec = (c.get("device_spec") or "").lower()
            proto = "wireguard" if (pc.get("wg_config") or "wireguard" in spec) else ("socks" if "socks" in spec else "https")
            formatted.append({
                "client_id": c.get("client_id"),
                "description": c.get("description") or c.get("device_name") or "resident proxy",
                "protocol": proto.upper(),
                "host": pc.get("proxy_host"),
                "http_port": pc.get("http_proxy_port"),
                "socks_port": pc.get("socks_proxy_port"),
                "https_port": pc.get("https_proxy_port"),
                "auth_token": pc.get("auth_token"),
                "create_time": c.get("create_time"),
                "exit": _proxy_exit(c.get("exit")),
                "unique_exit": bool(c.get("exit")),
                "raw": c
            })
        return formatted

    def remove_proxy(self, client_id):
        """
        删除指定的代理实例
        对应官方接口: POST /network/remove-client, body: {"client_id": "..."}
        """
        return self._request("/network/remove-client", method="POST", body={"client_id": client_id})

    def clean_all_proxies(self):
        """
        清理所有已创建的代理实例 (完全清空 https://ur.io 网页上的 Proxies 列表)
        """
        proxies = self.list_proxies()
        removed_count = 0
        for p in proxies:
            cid = p.get("client_id")
            if cid:
                try:
                    self.remove_proxy(cid)
                    removed_count += 1
                except Exception as e:
                    print(f"[!] 删除代理 {cid} 失败: {e}")
        return removed_count

    def clean_clients(self):
        """
        全量清理：
        1. 逐个清理网页端 PROXIES 列表中的代理实例 (POST /network/remove-client)
        2. 重置网络并发客户端连接计数 (POST /network/remove-clients)
        """
        proxies_removed = self.clean_all_proxies()
        clients_reset = False
        try:
            self._request("/network/remove-clients", method="POST", body={})
            clients_reset = True
        except Exception as e:
            print(f"[!] 清理历史客户端连接异常: {e}")

        return {
            "proxies_removed": proxies_removed,
            "clients_reset": clients_reset
        }

    def rotate_proxy_exit(self, client_id):
        """触发官方 unique_exit 轮换接口 POST /network/proxy/rotate-exit"""
        return self._request("/network/proxy/rotate-exit", method="POST", body={"client_id": client_id})

    def create_proxy(self, mode="best", target="", protocol="socks", location_id=None, gateway_name="", gateway_id="", residential_only=True, **kwargs):
        """
        分配代理节点
        mode: 'best' | 'country' | 'region' | 'city'
        target: 精确 location_id 绑定时为国家代码；未绑定的 CLI 城市/地区搜索词
        country_code kwarg: 未绑定的地区/城市搜索可按两位国家代码筛选
        protocol: 'socks' | 'https' | 'wireguard' (影响官方网页端 UI 识别与标注)
        """
        init_state = {}
        target_desc = ""
        loc_label = ""
        country_code = ""

        if mode == "best":
            init_state = {
                "location": {
                    "connect_location_id": {
                        "best_available": True
                    }
                }
            }
            target_desc = "全球最佳链路 (自动优选)"
            loc_label = "Best Available"
        elif mode == "country":
            country_code = (target or "US").strip().upper()
            chosen_loc = None
            # A gateway's explicit country binding wins over CLI city heuristics.
            if residential_only and not location_id:
                try:
                    from .locator import get_residential_locations
                    import random
                    res_locs = get_residential_locations(country_code, client=self)
                    if res_locs:
                        chosen_loc = random.choice(res_locs)
                except Exception:
                    pass

            if chosen_loc:
                location_id = chosen_loc["location_id"]
                init_state = {
                    "location": {
                        "connect_location_id": {"location_id": location_id},
                        "name": chosen_loc.get("name"),
                        "country_code": country_code.lower(),
                        "location_type": chosen_loc.get("location_type", "city")
                    }
                }
                target_desc = f"指定国家/地区 [{country_code}] · 住宅宽带 [{chosen_loc.get('name')}, {chosen_loc.get('region')}]"
                loc_label = f"{country_code}-{chosen_loc.get('name')}"
            else:
                if not location_id:
                    from .locator import fetch_live_countries
                    match = next((c for c in fetch_live_countries(client=self) if c["code"] == country_code), None)
                    if not match:
                        raise ValueError(f"国家 {country_code} 当前未返回可用位置")
                    location_id = match["location_id"]
                init_state = {"location": {"connect_location_id": {"location_id": location_id},
                                          "country_code": country_code.lower(), "location_type": "country"}}
                target_desc = f"指定国家/地区 [{country_code}]"
                loc_label = country_code
            init_state["country_code"] = country_code.lower()
        elif mode in ('city', 'region'):
            country_code = (target or '').strip().upper()
            if location_id:
                if not re.fullmatch(r'[A-Z]{2}', country_code):
                    raise ValueError('精确城市/地区绑定须提供两位国家代码')
                loc = dict(location_id=location_id, name=kwargs.get('location_name') or
                           kwargs.get(mode) or location_id, country_code=country_code,
                           location_type=mode, region=kwargs.get('region', ''), city=kwargs.get('city', ''),
                           region_location_id=kwargs.get('region_location_id', ''))
            else:
                from .locator import search_locations
                locations = search_locations(target, client=self, location_type=mode,
                                             country_code=kwargs.get('country_code', ''))
                exact = [loc for loc in locations if loc.get('name', '').casefold() == target.strip().casefold()]
                matches = exact or locations
                if len(matches) != 1:
                    raise ValueError('位置为空或匹配多个城市/地区，请选择精确 location_id 和国家代码')
                loc = matches[0]
                location_id = loc['location_id']
                country_code = str(loc.get('country_code', '')).upper()
            location_id = loc['location_id']
            loc_label = f"{country_code}-{loc.get('name')}"
            target_desc = f"指定{'城市' if mode == 'city' else '地区'} [{loc.get('name')}, {country_code}]"
            init_state = {'country_code': country_code.lower(), 'location': {
                'connect_location_id': {'location_id': location_id}, 'name': loc.get('name'),
                'country_code': country_code.lower(), 'location_type': mode,
                'region': loc.get('region', ''), 'city': loc.get('city', ''),
                'region_location_id': loc.get('region_location_id', '')}}
        else:
            raise ValueError(f"未知策略模式: {mode}")

        proto_clean = protocol.lower() if protocol in ["socks", "https", "wireguard"] else "socks"
        tag = (gateway_name or (gateway_id[:8] if gateway_id else "")).strip()
        desc_suffix = f" [{tag}]" if tag else ""
        description = f"resident proxy · {loc_label}{desc_suffix}"
        spec_suffix = f" {tag}" if tag else ""
        device_spec = f"ur.io proxy {proto_clean}{spec_suffix}"

        is_pro = self.get_proxy_capabilities()["is_pro"]
        proxy_config = {
            "enable_http": True,
            "enable_socks": True,
            "enable_wg": proto_clean == "wireguard",
            "lock_caller_ip": False,
            "initial_device_state": init_state
        }
        if is_pro:
            proxy_config["unique_exit"] = True
            proxy_config["unique_exit_replace"] = "auto"

        payload = {
            "description": description,
            "device_spec": device_spec,
            "proxy_config": proxy_config,
            "time_zone": "Asia/Shanghai",
            "locale": "zh-CN"
        }

        res = self._request('/network/auth-client', method='POST', body=payload)
        p = res.get("proxy_config_result")
        if not p:
            raise ApiError(200, "invalid-response")

        host = p["proxy_host"]
        token = p["auth_token"]
        password = "none"

        http_port = p["http_proxy_port"]
        socks_port = p["socks_proxy_port"]
        https_port = p["https_proxy_port"]

        return {
            "mode": mode,
            "location_type": init_state.get("location", {}).get("location_type", "best"),
            "exit": _proxy_exit(p.get("exit")),
            "unique_exit": is_pro,
            "location_id": location_id,
            "country_code": country_code,
            "target_desc": target_desc,
            "client_id": res.get("client_id"),
            "description": description,
            "protocol_spec": proto_clean,
            "host": host,
            "auth_token": token,
            "password": password,
            "http_port": http_port,
            "https_port": https_port,
            "socks_port": socks_port,
            "standard_http": f"http://{token}:{password}@{host}:{http_port}",
            "standard_socks5": f"socks5://{token}:{password}@{host}:{socks_port}",
            "standard_https": f"https://{token}:{password}@{host}:{https_port}",
            "native_http_url": p["http_proxy_url"],
            "native_https_url": p["https_proxy_url"],
            "native_socks_url": p["socks_proxy_url"],
            "wg_config": p.get("wg_config"),
            "test_commands": {
                "curl_http": f'curl -s -x "http://{token}:{password}@{host}:{http_port}" https://api.ipify.org',
                "curl_socks5": f'curl -s --socks5-hostname "{token}:{password}@{host}:{socks_port}" https://api.ipify.org',
                "curl_https": f'curl -s -x "https://{token}:{password}@{host}:{https_port}" https://api.ipify.org',
                "python_http": f"python -c \"import requests; print('HTTP 代理 IP:', requests.get('https://api.ipify.org', proxies={{'http': 'http://{token}:{password}@{host}:{http_port}', 'https': 'http://{token}:{password}@{host}:{http_port}'}}, timeout=10).text.strip())\"",
                "python_socks5": f"python -c \"import requests; print('SOCKS5 代理 IP:', requests.get('https://api.ipify.org', proxies={{'http': 'socks5://{token}:{password}@{host}:{socks_port}', 'https': 'socks5://{token}:{password}@{host}:{socks_port}'}}, timeout=10).text.strip())\""
            }
        }
