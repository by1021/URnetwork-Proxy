#!/usr/bin/env python3
import json
import re
from pathlib import Path
from urllib.parse import urlsplit
from .storage import atomic_json

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "config.json"
EXAMPLE_CONFIG_FILE = BASE_DIR / "config.example.json"

DEFAULT_CONFIG = {
    "api_key": "",          # 官方长期 API Key (urn_...)，推荐首选
    "jwt_token": "",        # 浏览器端提取的 byJwt 或临时 JWT
    "seedphrase": "",       # 24 位助记词
    "auth_code": "",
    "email": "",            # 独立注册的邮箱账号 (若支持密码登录)
    "password": "",         # 账户独立密码
    "api_base_url": "https://api.bringyour.com",
    "ur_base_url": "https://ur.io",
    "default_mode": "best",
    "default_country": "US",
    "timeout_seconds": 15,
    "web_port": 8777
}

CREDENTIAL_FIELDS = ("api_key", "jwt_token", "seedphrase", "auth_code", "email", "password")
KEEP_SECRET = "__KEEP_EXISTING__"
MAX_CONFIG_BYTES = 65536


class ConfigError(ValueError):
    """Only controlled messages; never echo config contents or parser excerpts."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError("配置中存在重复字段，请删除重复键")
        result[key] = value
    return result


def parse_config(text, *, max_bytes=MAX_CONFIG_BYTES, allow_comments=True):
    if not isinstance(text, str):
        raise ConfigError("配置源码须为文本，且不超过 64 KiB")
    try:
        if len(text.encode("utf-8")) > max_bytes:
            raise ConfigError("配置源码超过大小限制")
        cfg = json.loads(_strip_json_comments(text) if allow_comments else text, object_pairs_hook=_unique_object,
                         parse_constant=lambda value: (_ for _ in ()).throw(ConfigError("配置包含非有限数值")))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"JSONC 格式错误：第 {exc.lineno} 行，第 {exc.colno} 列") from None
    except (RecursionError, UnicodeError):
        raise ConfigError("配置嵌套过深或文本编码无效") from None
    if not isinstance(cfg, dict):
        raise ConfigError("配置最外层须为 JSON 对象")
    return cfg


def validate_config(cfg):
    if not isinstance(cfg, dict):
        raise ConfigError("配置最外层须为 JSON 对象")
    result = {**DEFAULT_CONFIG, **cfg}
    for key in CREDENTIAL_FIELDS:
        if not isinstance(result[key], str) or len(result[key]) > 8192:
            raise ConfigError(f"{key} 须为不超过 8192 字符的文本")
    for key in ("api_base_url", "ur_base_url"):
        value = result[key]
        try:
            if not isinstance(value, str) or len(value) > 2048 or re.search(r"[\s\x00-\x1f\x7f]", value):
                raise ValueError
            url = urlsplit(value)
            if (url.scheme not in ("http", "https") or not url.hostname or
                    url.username is not None or url.password is not None or url.query or url.fragment or
                    (url.port is not None and not 1 <= url.port <= 65535)):
                raise ValueError
        except ValueError:
            raise ConfigError(f"{key} 须为完整 HTTP(S) 地址，不含凭据、查询参数或片段") from None
    for key, minimum, maximum in (("timeout_seconds", 1, 300), ("web_port", 1, 65535)):
        if type(result[key]) is not int or not minimum <= result[key] <= maximum:
            raise ConfigError(f"{key} 须为 {minimum}–{maximum} 之间的整数")
    if result["default_mode"] not in ("best", "country", "city"):
        raise ConfigError("default_mode 须为 best、country 或 city")
    country = result["default_country"]
    if not isinstance(country, str) or not re.fullmatch(r"[A-Za-z]{2}", country):
        raise ConfigError("default_country 须为两个英文字母的国家代码")
    result["default_country"] = country.upper()
    try:
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise ConfigError("配置须为有效、有限嵌套的 JSON 数据") from None
    if len(encoded) > MAX_CONFIG_BYTES:
        raise ConfigError("配置不超过 64 KiB")
    return result

def _strip_json_comments(text: str) -> str:
    """String-aware JSONC cleanup; keep line/column offsets and literal ',}' intact."""
    chars = list(text.lstrip("\ufeff"))
    quoted, escaped, index = False, False, 0
    while index < len(chars):
        char = chars[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == "/" and index + 1 < len(chars) and chars[index + 1] in ("/", "*"):
            block = chars[index + 1] == "*"
            end = index + 2
            if block:
                while end + 1 < len(chars) and chars[end:end + 2] != ["*", "/"]:
                    end += 1
                if end + 1 >= len(chars):
                    raise ConfigError("JSONC 块注释未闭合")
                end += 2
            else:
                while end < len(chars) and chars[end] not in "\r\n":
                    end += 1
            for pos in range(index, end):
                if chars[pos] not in "\r\n":
                    chars[pos] = " "
            index = end - 1
        index += 1
    quoted, escaped = False, False
    for index, char in enumerate(chars):
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == ",":
            end = index + 1
            while end < len(chars) and chars[end].isspace():
                end += 1
            if end < len(chars) and chars[end] in "}]":
                chars[index] = " "
    return "".join(chars)

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
            cfg = parse_config(raw_text)
            merged = DEFAULT_CONFIG.copy()
            merged.update(cfg)
            return merged
    except Exception as e:
        print(f"[!] 读取配置文件失败: {e}，使用默认配置。")
        return DEFAULT_CONFIG.copy()

def save_config(cfg):
    """保存配置到 config.json"""
    atomic_json(CONFIG_FILE, cfg)
