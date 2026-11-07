"""Container-local HTTP liveness probe; no login or upstream requests."""
from http.client import HTTPConnection, HTTPException
import json

from core.config import load_config


def main():
    connection = HTTPConnection("127.0.0.1", 8777, timeout=3)
    try:
        allowed = load_config(strict=True).get("allowed_hosts") or []
        host = allowed[0] if allowed and "*" not in allowed else "127.0.0.1:8777"
        connection.request("GET", "/api/health", headers={"Host": host})
        response = connection.getresponse()
        return 0 if response.status == 200 and json.loads(response.read(4096)) == {"status": "ok"} else 1
    except (OSError, HTTPException, ValueError):
        return 1
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
