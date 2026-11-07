"""Probe success must reflect the management API, not cloud connectivity."""
import unittest
from unittest.mock import MagicMock, patch

from docker import healthcheck


class HealthcheckTests(unittest.TestCase):
    def probe(self, allowed=None, status=200, body=b'{"status":"ok"}', error=None):
        connection = MagicMock()
        connection.getresponse.return_value.status = status
        connection.getresponse.return_value.read.return_value = body
        connection.request.side_effect = error
        with patch.object(healthcheck, "HTTPConnection", return_value=connection) as connect:
            with patch.object(healthcheck, "load_config", return_value={"allowed_hosts": allowed}):
                result = healthcheck.main()
        connect.assert_called_once_with("127.0.0.1", 8777, timeout=3)
        connection.close.assert_called_once_with()
        return result, connection

    def test_success_and_local_host(self):
        result, connection = self.probe()
        self.assertEqual(result, 0)
        connection.request.assert_called_once_with(
            "GET", "/api/health", headers={"Host": "127.0.0.1:8777"},
        )

    def test_host_allowlist(self):
        result, connection = self.probe(allowed=["console.example.test:8999"])
        self.assertEqual(result, 0)
        self.assertEqual(connection.request.call_args.kwargs["headers"]["Host"], "console.example.test:8999")

    def test_wildcard_allowlist(self):
        result, connection = self.probe(allowed=["*"])
        self.assertEqual(result, 0)
        self.assertEqual(connection.request.call_args.kwargs["headers"]["Host"], "127.0.0.1:8777")

    def test_failure_modes(self):
        for options in ({"status": 503}, {"body": b"not json"}, {"body": b'[]'},
                        {"body": b'{"status":"error"}'}, {"error": OSError("offline")}):
            with self.subTest(options=options):
                self.assertEqual(self.probe(**options)[0], 1)


if __name__ == "__main__":
    unittest.main()
