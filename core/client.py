#!/usr/bin/env python3
"""
URnetwork 核心 API 客户端
封装多重认证登录 (API Key / JWT Token / 网页授权码 / 助记词 / 密码)、
账户配额、代理节点申请与生命周期管理
"""

import urllib.request
import json
import base64
import time
import threading
import urllib.error
from .locator import search_cities

class ApiError(RuntimeError):
    """Sanitized control-plane failure; never reflect credentials/remote bodies."""
    def __init__(self, status=0, kind="request"):
        self.status = status
        self.kind = kind
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
                        raise ApiError(200, "rejected")
                    return result
                except urllib.error.HTTPError as exc:
                    status = exc.code
                    exc.close()
                    renewable = not self.api_key and not self.jwt_token and bool(self.seedphrase or (self.email and self.password))
                    if status == 401 and auth and not self._resolving:
                        self.jwt = None
                        # Replay only GET after explicit authentication rejection, never creation POSTs.
                        if method == "GET" and attempt == 0 and renewable:
                            self.login()
                            continue
                    raise ApiError(status, "authentication" if status in (401, 403) else "request") from None
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
            if "error" in res and res["error"]:
                raise RuntimeError(f"授权码兑换失败: {res['error'].get('message', res['error'])}")
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
            if "error" in res and res["error"]:
                err_msg = res["error"].get("message", str(res["error"]))
                raise RuntimeError(f"邮箱密码登录失败: {err_msg} (如果是 Google 快捷登录注册的用户，请使用网页 Auth Code 或 API Key 登录)")
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
            if "error" in res and res["error"]:
                raise RuntimeError(f"助记词登录失败: {res['error'].get('message', res['error'])}")
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
        if "error" in res and res["error"]:
            raise RuntimeError(f"创建 API Key 失败: {res['error'].get('message', res['error'])}")
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
        if "error" in res and res["error"]:
            raise RuntimeError(f"生成助记词失败: {res['error'].get('message', res['error'])}")
        return res.get("seedphrase")

    def regenerate_seedphrase(self):
        """重置并重新生成 24 位助记词"""
        res = self._request("/auth/regenerate-seedphrase", method="POST", body={})
        if "error" in res and res["error"]:
            raise RuntimeError(f"重新生成助记词失败: {res['error'].get('message', res['error'])}")
        return res.get("seedphrase")

    def get_account_status(self):
        """查询账户每日配额与使用量信息"""
        balance_data = self._request("/subscription/balance", method="GET")
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

    def create_proxy(self, mode="best", target="", protocol="socks", location_id=None):
        """
        分配代理节点
        mode: 'best' | 'country' | 'city'
        target: 国家代码（如 'US', 'JP'）或城市名（如 'Tokyo'）
        protocol: 'socks' | 'https' | 'wireguard' (影响官方网页端 UI 识别与标注)
        """
        init_state = {}
        target_desc = ""
        loc_label = ""

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
        elif mode == "city":
            cities = search_cities(target, client=self)
            if not cities:
                raise ValueError(f"城市 {target} 当前无可用位置；保留原绑定，不降级国家")
            else:
                loc = cities[0]
                target_desc = f"指定城市 [{loc.get('name')}, {loc.get('country')}] (节点数: {loc.get('provider_count')})"
                loc_label = loc.get("name")
                init_state = {
                    "location": {
                        "connect_location_id": {
                            "location_id": loc["location_id"]
                        },
                        "name": loc.get("name"),
                        "country_code": loc.get("country_code"),
                        "location_type": loc.get("location_type", "city")
                    }
                }
        else:
            raise ValueError(f"未知策略模式: {mode}")

        proto_clean = protocol.lower() if protocol in ["socks", "https", "wireguard"] else "socks"
        description = f"resident proxy · {loc_label}"
        device_spec = f"ur.io proxy {proto_clean}"

        payload = {
            "description": description,
            "device_spec": device_spec,
            "proxy_config": {
                "enable_http": True,
                "enable_socks": True,
                "enable_wg": proto_clean == "wireguard",
                "lock_caller_ip": False,
                "initial_device_state": init_state
            },
            "time_zone": "Asia/Shanghai",
            "locale": "zh-CN"
        }

        res = self._request("/network/auth-client", method="POST", body=payload)
        p = res.get("proxy_config_result")
        if not p:
            err = res.get("error", {})
            raise RuntimeError(f"分配代理节点失败: {err.get('message', res)}")

        host = p["proxy_host"]
        token = p["auth_token"]
        password = "none"

        http_port = p["http_proxy_port"]
        socks_port = p["socks_proxy_port"]
        https_port = p["https_proxy_port"]

        return {
            "mode": mode,
            "location_id": location_id,
            "country_code": target.upper() if mode == "country" else "",
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
                "python_http": f"python -c \"import requests; print('HTTP 代理 IP:', requests.get('https://api.ipify.org', proxies={{'http': 'http://{token}:{password}@{host}:{http_port}', 'https': 'http://{token}:{password}@{host}:{http_port}'}}, timeout=10).text.strip())\"",
                "python_socks5": f"python -c \"import requests; print('SOCKS5 代理 IP:', requests.get('https://api.ipify.org', proxies={{'http': 'socks5://{token}:{password}@{host}:{socks_port}', 'https': 'socks5://{token}:{password}@{host}:{socks_port}'}}, timeout=10).text.strip())\""
            }
        }
