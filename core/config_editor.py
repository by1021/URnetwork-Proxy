"""Revisioned, redacted config.json editing; no cloud calls or live reconfiguration."""
import hashlib
import json
from pathlib import Path
import threading
import uuid

from .config import (CREDENTIAL_FIELDS, SECRET_FIELDS, DEFAULT_CONFIG, KEEP_SECRET, MAX_CONFIG_BYTES,
                     ConfigError, parse_config, validate_config)
from .storage import atomic_json


class ConfigConflict(ConfigError):
    pass


class ConfigEditor:
    def __init__(self, path, legacy_path, fallback=None):
        self.path, self.legacy_path = Path(path), Path(legacy_path)
        self.fallback = dict(fallback or {})
        self.lock = threading.RLock()

    @staticmethod
    def _read(path):
        try:
            with path.open("rb") as file:
                raw = file.read(MAX_CONFIG_BYTES + 1)
            if len(raw) > MAX_CONFIG_BYTES:
                raise ConfigError("配置文件不超过 64 KiB")
            return raw
        except FileNotFoundError:
            return None

    def _snapshot(self):
        raw, legacy = self._read(self.path), self._read(self.legacy_path)
        try:
            config = {**DEFAULT_CONFIG, **(parse_config(raw.decode("utf-8-sig")) if raw is not None else self.fallback)}
            if legacy is not None:
                old = parse_config(legacy.decode("utf-8-sig"))
                config.update({key: old.get(key, "") for key in CREDENTIAL_FIELDS})
        except UnicodeError:
            raise ConfigError("配置文件须为 UTF-8 文本") from None
        revision = hashlib.sha256(json.dumps([
            raw.hex() if raw is not None else None,
            legacy.hex() if legacy is not None else None,
        ]).encode()).hexdigest()
        return config, revision, legacy is not None, raw is not None

    def read(self):
        with self.lock:
            return self._snapshot()

    @staticmethod
    def redacted(config):
        return {key: KEEP_SECRET if key in SECRET_FIELDS and value else value
                for key, value in config.items()}

    def prepare(self, data, current):
        if ("source" in data) == ("config" in data):
            raise ConfigError("请提交 config 对象或 source 源码其中一种")
        config = parse_config(data["source"]) if "source" in data else data["config"]
        if not isinstance(config, dict):
            raise ConfigError("配置最外层须为 JSON 对象")
        config = dict(config)
        fields = data.get("fields", {})
        if not isinstance(fields, dict) or any(key not in DEFAULT_CONFIG for key in fields):
            raise ConfigError("可视化编辑只接受已知配置字段")
        config.update(fields)
        for key in SECRET_FIELDS:
            if key not in config and current.get(key):
                raise ConfigError(f"请保留 {key} 占位符；清除已有凭据须显式设置空字符串")
            if config.get(key) == KEEP_SECRET:
                if not current.get(key):
                    raise ConfigError(f"{key} 没有已保存值，请填写或设置空字符串")
                config[key] = current[key]
        return validate_config(config)

    def candidate(self, data):
        with self.lock:
            current, revision, _, _ = self._snapshot()
            if not isinstance(data.get("revision"), str) or data["revision"] != revision:
                raise ConfigConflict("配置已被其他窗口或磁盘修改，请重新读取后再保存（当前草稿保留）")
            return self.prepare(data, current)

    def save(self, config, revision):
        with self.lock:
            _, current_revision, legacy, _ = self._snapshot()
            if current_revision != revision:
                raise ConfigConflict("配置已变更，请重新读取后再保存（当前草稿保留）")
            config = validate_config(config)
            # Preserve the legacy file rather than deleting it. If atomic write fails,
            # restore its original name so the old startup behaviour remains intact.
            backup = self.legacy_path.with_name(f"credentials.migrated-{uuid.uuid4().hex}.json") if legacy else None
            if backup:
                self.legacy_path.rename(backup)
            try:
                atomic_json(self.path, config)
            except BaseException:
                if backup:
                    backup.rename(self.legacy_path)
                raise
            return self._snapshot()
