#!/usr/bin/env python3
import json
import os
from pathlib import Path

CONFIG_FILE = Path(__file__).resolve().parent.parent / "config.json"

DEFAULT_CONFIG = {
    "seedphrase": "",
    "api_base_url": "https://api.bringyour.com",
    "ur_base_url": "https://ur.io",
    "default_mode": "best",
    "default_country": "US",
    "timeout_seconds": 15
}

def load_config():
    if not CONFIG_FILE.exists():
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            # 合并默认值
            merged = DEFAULT_CONFIG.copy()
            merged.update(cfg)
            return merged
    except Exception as e:
        print(f"[!] 读取 config.json 失败: {e}，使用默认配置。")
        return DEFAULT_CONFIG.copy()

def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
