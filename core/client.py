#!/usr/bin/env python3
"""
URnetwork 核心 API 客户端
封装登录认证、账户配额、代理节点申请与生命周期管理
"""

import urllib.request
import json
import base64
import time
from .locator import search_cities

class URClient:
    def __init__(self, seedphrase, api_base="https://api.bringyour.com", timeout=15):
        self.seedphrase = seedphrase.strip()
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout
        self.jwt = None
        self.jwt_payload = None
        self.network_info = {}

    def _request(self, path, method="GET", body=None, auth=True):
        url = f"{self.api_base}{path}"
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) URnetwork-Proxy-Client/1.0"
        }
        if auth:
            if not self.jwt:
                self.login()
            headers["Authorization"] = f"Bearer {self.jwt}"

        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            content = resp.read().decode("utf-8")
            return json.loads(content) if content else {}

    def login(self):
        """使用助记词登录，获取并解析 Network JWT"""
        if not self.seedphrase:
            raise ValueError("助记词为空，请在 config.json 中配置正确助记词。")
        
        payload = {
            "seedphrase": self.seedphrase,
            "result_errors": True
        }
        res = self._request("/auth/login", method="POST", body=payload, auth=False)
        if "error" in res and res["error"]:
            raise RuntimeError(f"登录失败: {res['error'].get('message', res['error'])}")
        
        network = res.get("network", {})
        self.jwt = network.get("by_jwt")
        if not self.jwt:
            raise RuntimeError("登录未返回有效 JWT，请核对助记词。")

        # 解析 JWT Payload
        try:
            parts = self.jwt.split(".")
            padded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
            self.jwt_payload = json.loads(base64.urlsafe_b64decode(padded))
            self.network_info = {
                "network_id": self.jwt_payload.get("network_id"),
                "network_name": self.jwt_payload.get("network_name"),
                "user_id": self.jwt_payload.get("user_id")
            }
        except Exception:
            self.network_info = {"network_name": "unknown"}

        return self.network_info

    def get_account_status(self):
        """查询账户每日配额与使用量信息"""
        balance_data = self._request("/subscription/balance", method="GET")
        start_bytes = balance_data.get("start_balance_byte_count", 0)
        curr_bytes = balance_data.get("balance_byte_count", 0)
        
        return {
            "network_name": self.network_info.get("network_name", "未知"),
            "network_id": self.network_info.get("network_id"),
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
        except Exception:
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

    def create_proxy(self, mode="best", target="", protocol="socks"):
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
            country_code = target.strip().upper()
            init_state = {"country_code": country_code}
            target_desc = f"指定国家/地区 [{country_code}]"
            loc_label = country_code
        elif mode == "city":
            # 搜索城市节点
            cities = search_cities(target, api_base=self.api_base, timeout=self.timeout)
            if not cities:
                # 若未匹配到，降级为最佳链路
                print(f"[!] 城市 [{target}] 未找到可用节点，自动降级为最佳连接。")
                init_state = {"location": {"connect_location_id": {"best_available": True}}}
                target_desc = "城市未命中降级 -> 全球最佳"
                loc_label = "Best Available"
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

        # 对齐官方网页端规范参数
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
        # 密码设为 'none'：多数客户端（PySocks / sing-box / Clash 等）在密码为空时会把 RFC 1929 认证
        # 降级为 0x00 (无认证) 导致被服务端拦截。填入 'none' 可确保 RFC 1929 认证严格生效。
        password = "none"

        http_port = p["http_proxy_port"]
        socks_port = p["socks_proxy_port"]
        https_port = p["https_proxy_port"]

        return {
            "mode": mode,
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
            # 标准格式: protocol://user:pass@host:port
            "standard_http": f"http://{token}:{password}@{host}:{http_port}",
            "standard_socks5": f"socks5://{token}:{password}@{host}:{socks_port}",
            "standard_https": f"https://{token}:{password}@{host}:{https_port}",
            # 官方原生返回 (注意: 原生 URL 不含 Basic 凭证，直接使用会超时被拒)
            "native_http_url": p["http_proxy_url"],
            "native_https_url": p["https_proxy_url"],
            "native_socks_url": p["socks_proxy_url"],
            "wg_config": p.get("wg_config"),
            # 即用型测试命令
            "test_commands": {
                "curl_http": f'curl -s -x "http://{token}:{password}@{host}:{http_port}" https://api.ipify.org',
                "curl_socks5": f'curl -s --socks5-hostname "{token}:{password}@{host}:{socks_port}" https://api.ipify.org',
                "python_http": f'python -c "import requests; print(\'HTTP 代理 IP:\', requests.get(\'https://api.ipify.org\', proxies={{\'http\': \'http://{token}:{password}@{host}:{http_port}\', \'https\': \'http://{token}:{password}@{host}:{http_port}\'}}, timeout=10).text.strip())"',
                "python_socks5": f'python -c "import requests; print(\'SOCKS5 代理 IP:\', requests.get(\'https://api.ipify.org\', proxies={{\'http\': \'socks5://{token}:{password}@{host}:{socks_port}\', \'https\': \'socks5://{token}:{password}@{host}:{socks_port}\'}}, timeout=10).text.strip())"'
            }
        }
