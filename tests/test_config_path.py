"""Configuration paths are selected at process startup, including CLI imports."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ConfigPathTests(unittest.TestCase):
    def run_python(self, code, config_file=None):
        env = dict(os.environ)
        env.pop("URNETWORK_CONFIG_FILE", None)
        if config_file is not None:
            env["URNETWORK_CONFIG_FILE"] = str(config_file)
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT, env=env,
            text=True, capture_output=True, check=True,
        )
        return json.loads(result.stdout)

    def test_default_path_is_unchanged(self):
        for value in (None, ""):
            with self.subTest(value=value):
                path = self.run_python(
                    "from core.config import CONFIG_FILE; import json; print(json.dumps(str(CONFIG_FILE)))",
                    value,
                )
                self.assertEqual(path, str(ROOT / "config.json"))

    def test_override_is_shared_by_web_and_cli_and_can_be_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data" / "config.json"
            result = self.run_python("""
import json, os
from pathlib import Path
from core.config import CONFIG_FILE, DEFAULT_CONFIG, load_config, save_config
from core.web_server import CONFIG_FILE as WEB_CONFIG_FILE
from core.config_editor import ConfigEditor
assert CONFIG_FILE == Path(os.environ['URNETWORK_CONFIG_FILE'])
assert WEB_CONFIG_FILE == CONFIG_FILE
save_config({**DEFAULT_CONFIG, 'web_username': 'docker-user'})
editor = ConfigEditor(CONFIG_FILE, CONFIG_FILE.parent / 'state' / 'credentials.json')
config, revision, _, _ = editor.read()
config['web_password'] = 'docker-test-password'
editor.save(config, revision)
print(json.dumps(load_config(strict=True)))
""", path)
            self.assertEqual(result["web_username"], "docker-user")
            self.assertEqual(result["web_password"], "docker-test-password")
            self.assertEqual(json.loads(path.read_text()), result)
            self.assertEqual(list(path.parent.iterdir()), [path])
            if os.name == "posix":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_missing_override_uses_example_without_creating_root_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            result = self.run_python("""
import json, os
from pathlib import Path
from core.config import CONFIG_FILE, load_config
assert CONFIG_FILE == Path(os.environ['URNETWORK_CONFIG_FILE'])
print(json.dumps(load_config(strict=True)))
""", path)
            self.assertEqual(result["web_username"], "admin")
            self.assertFalse(path.exists())

    def test_invalid_override_fails_closed_in_web_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text("{broken", encoding="utf-8")
            result = self.run_python("""
import json
from core.config import ConfigError, load_config
try:
    load_config(strict=True)
except ConfigError:
    print(json.dumps('invalid'))
else:
    print(json.dumps('unexpected fallback'))
""", path)
            self.assertEqual(result, "invalid")


if __name__ == "__main__":
    unittest.main()
