#!/usr/bin/env python3
"""
URnetwork 核心 API 客户端
封装登录认证、账户配额、代理节点申请与生命周期管理
"""

import urllib.request
import json
import base64
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
            "User-Agent": "URnetwork-Proxy-Client/1.0"
        }
        if auth:
            if not self.jwt:
                self.login()
            headers["Authorization"] = f"Bearer {self.jwt}"

        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

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

    def create_proxy(self, mode="best", target=""):
        """
        分配代理节点
        mode: 'best' | 'country' | 'city'
        target: 国家代码（如 'US', 'JP'）或城市名（如 'Tokyo'）
        """
        init_state = {}
        target_desc = ""

        if mode == "best":
            init_state = {
                "location": {
                    "connect_location_id": {
                        "best_available": True
                    }
                }
            }
            target_desc = "全球最佳链路 (自动优选)"
        elif mode == "country":
            country_code = target.strip().upper()
            init_state = {"country_code": country_code}
            target_desc = f"指定国家/地区 [{country_code}]"
        elif mode == "city":
            # 搜索城市节点
            cities = search_cities(target, api_base=self.api_base, timeout=self.timeout)
            if not cities:
                # 若未匹配到，降级为最佳链路
                print(f"[!] 城市 [{target}] 未找到可用节点，自动降级为最佳连接。")
                init_state = {"location": {"connect_location_id": {"best_available": True}}}
                target_desc = f"城市未命中降级 -> 全球最佳"
            else:
                loc = cities[0]
                target_desc = f"指定城市 [{loc.get('name')}, {loc.get('country')}] (节点数: {loc.get('provider_count')})"
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

        payload = {
            "proxy_config": {
                "enable_http": True,
                "enable_socks": True,
                "lock_caller_ip": False,
                "initial_device_state": init_state
            }
        }

        res = self._request("/network/auth-client", method="POST", body=payload)
        p = res.get("proxy_config_result")
        if not p:
            err = res.get("error", {})
            raise RuntimeError(f"分配代理节点失败: {err.get('message', res)}")

        host = p["proxy_host"]
        token = p["auth_token"]
        password = ""

        return {
            "mode": mode,
            "target_desc": target_desc,
            "client_id": res.get("client_id"),
            "host": host,
            "auth_token": token,
            "http_port": p["http_proxy_port"],
            "https_port": p["https_proxy_port"],
            "socks_port": p["socks_proxy_port"],
            # 标准格式: protocol://user:pass@host:port
            "standard_http": f"http://{token}:{password}@{host}:{p['http_proxy_port']}",
            "standard_socks5": f"socks5://{token}:{password}@{host}:{p['socks_proxy_port']}",
            "standard_https": f"https://{token}:{password}@{host}:{p['https_proxy_port']}",
            # 官方原生返回
            "native_http_url": p["http_proxy_url"],
            "native_https_url": p["https_proxy_url"],
            "native_socks_url": p["socks_proxy_url"],
            "wg_config": p.get("wg_config")
        }

    def clean_clients(self):
        """清理历史闲置客户端（释放并发槽位）"""
        try:
            res = self._request("/network/remove-clients", method="POST", body={})
            return True
        except Exception as e:
            print(f"[!] 清理历史客户端警告: {e}")
            return False
