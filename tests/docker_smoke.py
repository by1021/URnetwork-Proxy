"""Opt-in Docker integration test; only its unique test project/volume is removed.

Run: python3 tests/docker_smoke.py
Requires Docker Engine and Compose v2+. No upstream credentials are used.
"""
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import time
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener
import uuid


ROOT = Path(__file__).resolve().parents[1]
SERVICE = "urnetwork-proxy"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    project = "urnetwork-smoke-" + uuid.uuid4().hex[:12]
    web_port, gateway_port = free_port(), free_port()
    while gateway_port == web_port:
        gateway_port = free_port()
    smoke_dir = tempfile.mkdtemp(prefix="urnetwork-smoke-")
    os.system(f'chown -R 10001:10001 {smoke_dir} && chmod -R 777 {smoke_dir}')
    env = {**os.environ, "URNETWORK_WEB_PORT": str(web_port),
           "URNETWORK_GATEWAY_PORTS": str(gateway_port),
           "URNETWORK_DATA_DIR": smoke_dir}
    base = ["docker", "compose", "-f", str(ROOT / "compose.yaml"), "-p", project]

    def run(args, timeout=180):
        result = subprocess.run(args, cwd=ROOT, env=env, text=True,
                                capture_output=True, timeout=timeout)
        if result.returncode:
            raise RuntimeError(f"Command failed ({result.returncode}): {args}\n{result.stdout}\n{result.stderr}")
        return result.stdout.strip()

    def compose(*args, **kwargs):
        return run([*base, *args], **kwargs)

    def execute(code):
        return compose("exec", "-T", SERVICE, "python", "-c", code)

    opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
    csrf = ""

    def request(path, data=None, method=None):
        body = json.dumps(data).encode() if data is not None else None
        req = Request(f"http://127.0.0.1:{web_port}{path}", data=body, method=method,
                      headers={"Content-Type": "application/json", "X-CSRF-Token": csrf})
        with opener.open(req, timeout=10) as response:
            raw = response.read()
            return json.loads(raw) if path.startswith("/api/") else raw

    def login(username, password):
        nonlocal csrf
        request("/api/login", {"username": username, "password": password})
        csrf = request("/api/session")["csrf"]

    def snapshot():
        return json.loads(execute("""
import hashlib, json
from pathlib import Path
files = ['config.json', 'state/gateways.json', 'state/gateway_auth.json',
         'state/gateway_tls.crt', 'state/gateway_tls.key']
print(json.dumps({name: hashlib.sha256((Path('/data') / name).read_bytes()).hexdigest() for name in files}))
"""))

    try:
        compose("up", "-d", "--build", "--wait", "--wait-timeout", "90", timeout=600)
        container = compose("ps", "-q", SERVICE)
        inspection = json.loads(run(["docker", "inspect", container]))[0]
        assert inspection["Config"]["User"] == "10001:10001"
        assert inspection["Config"]["StopSignal"] == "SIGINT"
        assert inspection["HostConfig"]["ReadonlyRootfs"]
        assert all(binding["HostIp"] in ("", "0.0.0.0", "::")
                   for bindings in inspection["HostConfig"]["PortBindings"].values() for binding in bindings)
        assert request("/api/health") == {"status": "ok"}
        assert b"<html" in request("/").lower()
        login("admin", "admin")
        config = request("/api/config")
        config["config"].update(web_username="smoke-user", web_password="smoke-password",
                                allowed_hosts=["console.example.test", "127.0.0.1"])
        request("/api/config", {"config": config["config"], "revision": config["revision"]}, "PUT")
        gateway = request("/api/gateways", {
            "name": "docker-smoke", "host": "0.0.0.0", "port": gateway_port,
            "mode": "best", "enabled": False, "username": "proxy-user", "password": "proxy-password",
        })
        execute("""
import os
from pathlib import Path
from core.tls_helper import get_ssl_context
assert os.getuid() == 10001
assert not any((Path('/app') / name).exists() for name in ['config.json', '.local', '.git', '.mindfs', '.env'])
get_ssl_context(state_dir='/data/state')
assert Path('/data/config.json').stat().st_mode & 0o777 == 0o600
assert Path('/data/state/gateway_tls.key').stat().st_mode & 0o777 == 0o600
""")
        before = snapshot()

        # Exercise the actual mixed-protocol listener through Docker's published
        # gateway port; authentication ends before any upstream is contacted.
        compose("exec", "-d", SERVICE, "python", "-c", f"""
import asyncio
from core.multiplexer import MixedProxyServer
async def main():
    relay = MixedProxyServer('0.0.0.0', {gateway_port}, lambda: None,
        username='proxy-user', password='proxy-password', tls_config={{'state_dir': '/data/state'}})
    await relay.start()
    await asyncio.Event().wait()
asyncio.run(main())
""")
        deadline = time.monotonic() + 15
        while True:
            try:
                with socket.create_connection(("127.0.0.1", gateway_port), timeout=1) as sock:
                    sock.sendall(b"\x05\x01\x02")
                    assert sock.recv(2) == b"\x05\x02"
                break
            except (OSError, AssertionError):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)
        cert = execute("from pathlib import Path; print(Path('/data/state/gateway_tls.crt').read_text())")
        context = ssl.create_default_context(cadata=cert)
        for tls in (False, True):
            with socket.create_connection(("127.0.0.1", gateway_port), timeout=5) as raw:
                with context.wrap_socket(raw, server_hostname="localhost") if tls else raw as sock:
                    sock.sendall(b"GET http://example.test/ HTTP/1.1\r\nHost: example.test\r\n\r\n")
                    assert sock.recv(4096).startswith(b"HTTP/1.1 407 ")

        compose("stop")
        stopped = json.loads(run(["docker", "inspect", container]))[0]["State"]
        assert stopped["ExitCode"] == 0, stopped
        compose("up", "-d", "--force-recreate", "--wait", "--wait-timeout", "90")
        assert compose("ps", "-q", SERVICE) != container
        assert snapshot() == before
        login("smoke-user", "smoke-password")
        restored = request("/api/gateways")["gateways"]
        assert len(restored) == 1 and restored[0]["id"] == gateway["id"]
        assert restored[0]["username"] == "proxy-user"
        assert restored[0]["password"] == "proxy-password"
        compose("exec", "-T", SERVICE, "python", "-m", "docker.healthcheck")
        assert "--state-dir" in compose("run", "--rm", "--no-deps", SERVICE, "--help")
        print("PASS: build, health, login, atomic config save, non-root, HTTP/HTTPS/SOCKS5 ports, "
              "TLS, graceful stop, recreate persistence, Host allowlist and CLI")
    except BaseException:
        print(compose("logs", "--no-color", "--tail", "60"))
        raise
    finally:
        # The random project name scopes deletion to resources this test created.
        compose("down", "--volumes", "--remove-orphans")
        import shutil
        shutil.rmtree(smoke_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
