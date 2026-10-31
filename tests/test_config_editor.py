import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core.config import (ConfigError, DEFAULT_CONFIG, KEEP_SECRET, parse_config,
                         validate_config)
from core.config_editor import ConfigEditor, ConfigConflict


class ParserTests(unittest.TestCase):
    def test_jsonc_keeps_string_literals_and_offsets(self):
        value = parse_config('{\n// comment\n"literal": "https://host/,}/*literal*/", "items": [1,2,], /*x*/ }')
        self.assertEqual(value, {"literal": "https://host/,}/*literal*/", "items": [1, 2]})
        self.assertEqual(parse_config('{"s": "a\\\"//b",}')["s"], 'a"//b')
        with self.assertRaisesRegex(ConfigError, "第 3 行"):
            parse_config('{\n// comment\n"k": }')

    def test_invalid_sources_are_rejected_without_echo(self):
        for text in ('[]', 'null', '{"password":"private","password":"other"}',
                     '{"key":NaN}', '{"key":Infinity}', '{/* unfinished', '{"secret": private}'):
            with self.subTest(source_type=text[:2]), self.assertRaises(ConfigError) as error:
                parse_config(text)
            self.assertNotIn("private", str(error.exception))

    def test_fields_and_ranges(self):
        bad = [("web_port", 0), ("web_port", 65536), ("web_port", True),
               ("web_port", "8777"), ("timeout_seconds", 0), ("timeout_seconds", 1.5),
               ("timeout_seconds", 301), ("default_mode", "unknown"), ("default_country", "USA"),
               ("api_key", None), ("api_base_url", "file:///tmp"),
               ("api_base_url", "https://user:private@localhost"),
               ("ur_base_url", "https://localhost:70000"), ("ur_base_url", "https://localhost/?secret=1")]
        for key, value in bad:
            with self.subTest(key=key, value=value), self.assertRaises(ConfigError):
                validate_config({key: value})
        self.assertEqual(validate_config({"default_country": "jp"})["default_country"], "JP")
        self.assertEqual(validate_config({"web_port": 65535})["web_port"], 65535)


class ConfigEditorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "config.json"
        self.legacy = Path(self.temp.name) / ".local" / "credentials.json"
        self.config = {**DEFAULT_CONFIG, "api_key": "private-original", "extension": {"text": ",}"}}
        self.path.write_text(json.dumps(self.config), encoding="utf-8")
        self.editor = ConfigEditor(self.path, self.legacy)

    def tearDown(self):
        self.temp.cleanup()

    def payload(self):
        config, revision, _, _ = self.editor.read()
        return {"config": self.editor.redacted(config), "revision": revision}

    def test_roundtrip_masks_secrets_preserves_extensions(self):
        data = self.payload()
        self.assertNotIn("private-original", json.dumps(data))
        self.assertEqual(data["config"]["api_key"], KEEP_SECRET)
        data["config"]["web_port"] = 9000
        self.editor.save(self.editor.candidate(data), data["revision"])
        value = parse_config(self.path.read_text(encoding="utf-8"))
        self.assertEqual(value["api_key"], "private-original")
        self.assertEqual(value["web_port"], 9000)
        self.assertEqual(value["extension"], {"text": ",}"})

    def test_form_patches_keep_source_integer_precision(self):
        payload = self.payload()
        payload["config"]["extension"] = {"serial": 9007199254740993}
        payload["source"] = json.dumps(payload.pop("config"))
        payload["fields"] = {"timeout_seconds": 41}
        self.editor.save(self.editor.candidate(payload), payload["revision"])
        value = parse_config(self.path.read_text(encoding="utf-8"))
        self.assertEqual(value["extension"]["serial"], 9007199254740993)
        self.assertEqual(value["timeout_seconds"], 41)
        payload = self.payload()
        payload["fields"] = {"extension": "ignored?"}
        with self.assertRaises(ConfigError):
            self.editor.candidate(payload)

    def test_maximum_config_write_uses_exact_lf_size(self):
        payload = self.payload()
        size = len(json.dumps(payload["config"], ensure_ascii=False, indent=2).encode())
        payload["config"]["extension"] = "x" * (65536 - size - 10)
        candidate = self.editor.candidate(payload)
        self.editor.save(candidate, payload["revision"])
        self.assertNotIn(b"\r\n", self.path.read_bytes())
        self.assertLessEqual(len(self.path.read_bytes()), 65536)
        self.assertEqual(self.editor.read()[0], candidate)

    def test_clear_requires_explicit_empty_value(self):
        data = self.payload()
        del data["config"]["api_key"]
        with self.assertRaises(ConfigError):
            self.editor.candidate(data)
        data["config"]["api_key"] = ""
        self.editor.save(self.editor.candidate(data), data["revision"])
        self.assertEqual(self.editor.read()[0]["api_key"], "")

    def test_unknown_placeholder_rejected(self):
        data = self.payload()
        data["config"]["password"] = KEEP_SECRET
        with self.assertRaises(ConfigError):
            self.editor.candidate(data)

    def test_conflict_before_validation_and_before_write(self):
        data = self.payload()
        candidate = self.editor.candidate(data)
        self.path.write_text('{"web_port":9123}', encoding="utf-8")
        with self.assertRaises(ConfigConflict):
            self.editor.candidate(data)
        with self.assertRaises(ConfigConflict):
            self.editor.save(candidate, data["revision"])
        self.assertEqual(self.editor.read()[0]["web_port"], 9123)

    def test_migrate_legacy_on_save_without_losing_backup(self):
        self.legacy.parent.mkdir()
        self.legacy.write_text('{"jwt_token":"legacy-private"}', encoding="utf-8")
        data = self.payload()
        self.assertEqual(data["config"]["api_key"], "")
        self.editor.save(self.editor.candidate(data), data["revision"])
        self.assertFalse(self.legacy.exists())
        backups = list(self.legacy.parent.glob("credentials.migrated-*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text())["jwt_token"], "legacy-private")
        self.assertEqual(self.editor.read()[0]["jwt_token"], "legacy-private")
        self.assertFalse(self.editor.read()[2])

    def test_write_failure_restores_legacy_and_config(self):
        self.legacy.parent.mkdir()
        self.legacy.write_text('{"api_key":"legacy-private"}', encoding="utf-8")
        before = self.path.read_bytes()
        data = self.payload()
        with patch("core.config_editor.atomic_json", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                self.editor.save(self.editor.candidate(data), data["revision"])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(self.legacy.exists())

    def test_missing_file_uses_defaults_and_creates_on_save(self):
        editor = ConfigEditor(self.path.with_name("new.json"), self.legacy)
        config, revision, _, exists = editor.read()
        self.assertFalse(exists)
        editor.save(config, revision)
        self.assertEqual(editor.read()[0]["web_port"], 8777)
