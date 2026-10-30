#!/usr/bin/env python3
import json
import os
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "config.json"
EXAMPLE_CONFIG_FILE = BASE_DIR / "config.example.json"

DEFAULT_CONFIG = {
    "api_key": "",          # 官方长期 API Key (urn_...)，推荐首选
    "jwt_token": "",        # 浏览器端提取的 byJwt 或临时 JWT
    "seedphrase": "",       # 24 位助记词
    "email": "",            # 独立注册的邮箱账号 (若支持密码登录)
    "password": "",         # 账户独立密码
    "api_base_url": "https://api.bringyour.com",
    "ur_base_url": "https://ur.io",
    "default_mode": "best",
    "default_country": "US",
    "timeout_seconds": 15
}

def _strip_json_comments(text: str) -> str:
    """去除 JSON 中的 // 与 /* */ 注释及多余尾随逗号 (支持 JSONC 格式)"""
    pattern = r'("(?:\\.|[^"\\])*")|//.*?$|/\*.*?\*/'
    def replace(match):
        return match.group(1) if match.group(1) else ""
    cleaned = re.sub(pattern, replace, text, flags=re.MULTILINE | re.DOTALL)
    cleaned = re.sub(r',\s*([}\]])', r'\1', cleaned)
    return cleaned

def load_config():
    """加载配置文件，若 config.json 不存在则尝试从 config.example.json 回退，支持 JSONC 注释"""
    target = CONFIG_FILE
    if not target.exists():
        if EXAMPLE_CONFIG_FILE.exists():
            target = EXAMPLE_CONFIG_FILE
        else:
            save_config(DEFAULT_CONFIG)
            return DEFAULT_CONFIG.copy()
    try:
        with open(target, "r", encoding="utf-8") as f:
            raw_text = f.read()
            cleaned_text = _strip_json_comments(raw_text)
            cfg = json.loads(cleaned_text)
            merged = DEFAULT_CONFIG.copy()
            merged.update(cfg)
            return merged
    except Exception as e:
        print(f"[!] 读取配置文件失败: {e}，使用默认配置。")
        return DEFAULT_CONFIG.copy()

def save_config(cfg):
    """保存配置到 config.json"""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
