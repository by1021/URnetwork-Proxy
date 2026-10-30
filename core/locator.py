#!/usr/bin/env python3
"""
URnetwork 地理信息与节点检索模块
从 https://ur.io 及官方 API 实时获取全球国家、地区与住宅节点分布
"""

import urllib.request
import json
import re

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

def fetch_live_countries(ur_base="https://ur.io", api_base="https://api.bringyour.com", timeout=15):
    """
    实时从 https://ur.io/location 与 API 提取支持的国家列表及在线节点数
    """
    # 1. 抓取 ur.io/location 提取国家编码与全称
    countries_meta = {}
    try:
        req_html = urllib.request.Request(f"{ur_base}/location", headers=HEADERS)
        with urllib.request.urlopen(req_html, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
            matches = re.findall(
                r'href=[\'"]/location/([a-z]{2})[\'"][\s\S]*?class=[\'"]location-hub-name[\'"][^>]*>([^<]+)</span>',
                html
            )
            for code, name in matches:
                countries_meta[code.upper()] = name.strip()
    except Exception as e:
        # 网络异常时容错降级
        pass

    # 2. 抓取 stats/providers-map 提取精确在线 Provider 数量与区域
    providers_map = {}
    try:
        req_map = urllib.request.Request(f"{api_base}/stats/providers-map", headers=HEADERS)
        with urllib.request.urlopen(req_map, timeout=timeout) as resp:
            providers_map = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        pass

    merged_countries = []
    # 优先使用实时统计的节点表
    for code_lower, regions in providers_map.items():
        code_upper = code_lower.upper()
        name = countries_meta.get(code_upper, code_upper)
        total_providers = sum(r.get("provider_count", 0) for r in regions.values())
        merged_countries.append({
            "code": code_upper,
            "name": name,
            "providers": total_providers,
            "regions_count": len(regions)
        })

    # 若网页有而统计未包含（极少数边缘国家），做兜底补齐
    known_codes = {c["code"] for c in merged_countries}
    for code_upper, name in countries_meta.items():
        if code_upper not in known_codes:
            merged_countries.append({
                "code": code_upper,
                "name": name,
                "providers": 0,
                "regions_count": 0
            })

    # 按照节点数降序排列
    merged_countries.sort(key=lambda x: x["providers"], reverse=True)
    return merged_countries

def search_cities(query, api_base="https://api.bringyour.com", timeout=15):
    """
    调用 /network/find-provider-locations 搜索具体城市与节点 ID
    """
    url = f"{api_base}/network/find-provider-locations"
    data = json.dumps({"query": query}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", **HEADERS}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            return res.get("locations", [])
    except Exception as e:
        print(f"[!] 搜索城市失败: {e}")
        return []
