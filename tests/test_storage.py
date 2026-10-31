import json
import tempfile
import unittest
from pathlib import Path
from core.storage import ProcessLock, atomic_json


class StorageTests(unittest.TestCase):
    def test_atomic_write_and_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            atomic_json(path / "state.json", {"enabled": True})
            self.assertTrue(json.loads((path / "state.json").read_text())["enabled"])
            lock = ProcessLock(path / "state.lock")
            try:
                with self.assertRaises(RuntimeError):
                    ProcessLock(path / "state.lock")
            finally:
                lock.close()
            second = ProcessLock(path / "state.lock")
            second.close()
