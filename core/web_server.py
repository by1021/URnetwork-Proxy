"""Management API and static UI with password sessions; loopback by default."""
import asyncio
import ipaddress
import socket
import concurrent.futures
import hmac
import json
import secrets
import threading
import time
import webbrowser
import weakref
from collections import deque
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .client import URClient
from .gateway import GatewayManager
from .locator import CountryCatalog
from .storage import ProcessLock
from .config import CONFIG_FILE, CREDENTIAL_FIELDS, SECRET_FIELDS, DEFAULT_CONFIG, ConfigError, parse_config, validate_config
from .config_editor import ConfigEditor, ConfigConflict
from .runtime_address import detect_runtime_ips, host_allowed, runtime_info

ROOT = Path(__file__).resolve().parent.parent


def build_client(config):
    return URClient(**{k: config.get(k, "") for k in CREDENTIAL_FIELDS},
                    api_base=config.get("api_base_url", "https://api.bringyour.com"),
                    timeout=config.get("timeout_seconds", 15))


class ApiProblem(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message


class ManagementApp:
    def __init__(self, config, state_dir, *, manager=None, client=None, web_port=None, web_host=None, config_path=None):
        self.config = dict(config)
        # Validate local credentials even when load_config accepted a hand-edited file.
        auth = validate_config({k: self.config.get(k, DEFAULT_CONFIG[k])
                                for k in ("web_username", "web_password")})
        self.web_username = auth["web_username"]
        self.web_password = auth["web_password"]
        self.web_port = web_port if web_port is not None else self.config.get("web_port", 8777)
        self.web_host = web_host if web_host is not None else self.config.get("web_host", "127.0.0.1")
        self.runtime_ips = detect_runtime_ips(self.web_host)
        self.dual_stack = False
        self.state_dir = Path(state_dir)
        saved = self.state_dir / "credentials.json"
        self.config_editor = ConfigEditor(config_path or CONFIG_FILE, saved, self.config)
        if saved.exists():
            override = json.loads(saved.read_text(encoding="utf-8"))
            self.config.update({k: override.get(k, "") for k in CREDENTIAL_FIELDS})
        self.client = client or build_client(self.config)
        self.catalog = CountryCatalog(self.client)
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name="gateway-event-loop", daemon=True)
        self.manager = manager or GatewayManager(self.client, self.state_dir / "gateways.json")
        self.sessions = {}
        self.login_attempts = deque()
        self.auth_lock = threading.Lock()
        self.started = False
        self.updating_credentials = False
        self.gateway_operations = 0
        self.gateway_update_locks = weakref.WeakValueDictionary()
        self.account_cache = (0, None)
        self.account_lock = threading.Lock()

    def _load_gateway_auth(self):
        auth_file = self.state_dir / "gateway_auth.json"
        if not auth_file.exists():
            return {}
        try:
            records = json.loads(auth_file.read_text(encoding="utf-8"))
            return records if isinstance(records, dict) else {}
        except Exception:
            return {}

    def _save_gateway_auth(self, records):
        auth_file = self.state_dir / "gateway_auth.json"
        try:
            auth_file.parent.mkdir(parents=True, exist_ok=True)
            auth_file.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
            auth_file.chmod(0o600)
        except OSError:
            pass

    def _restore_gateway_auth(self):
        records = self._load_gateway_auth()
        if not records:
            return
        async def restore():
            gateways = getattr(self.manager, "_gateways", {})
            for gid, auth in records.items():
                g = gateways.get(gid)
                if g and auth.get("username") and auth.get("password"):
                    g.config["username"] = auth["username"]
                    g.config["password"] = auth["password"]
                    g.auth_missing = False
                    if g.wanted and g.status != "running":
                        try:
                            await self.manager.start_gateway(gid)
                        except Exception:
                            pass
        self.call(restore())

    def start(self):
        self.thread.start()
        self.started = True
        self.call(self.manager.initialize())
        self._restore_gateway_auth()

    def call(self, coroutine):
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        try:
            return future.result(timeout=120)
        except concurrent.futures.TimeoutError:
            # Never cancel an in-flight remote creation: its client_id must be recorded.
            raise ApiProblem(503, "操作仍在后台执行，请刷新列表查看状态") from None

    def close(self):
        if self.started:
            try:
                self.call(self.manager.close())
                self.call(self.loop.shutdown_asyncgens())
                self.call(self.loop.shutdown_default_executor())
            finally:
                self.loop.call_soon_threadsafe(self.loop.stop)
                self.thread.join(10)
                if not self.thread.is_alive():
                    self.loop.close()
                self.started = False
        elif not self.loop.is_closed():
            self.loop.close()

    def login(self, username, password):
        with self.auth_lock:
            now = time.monotonic()
            while self.login_attempts and self.login_attempts[0] < now - 60:
                self.login_attempts.popleft()
            if len(self.login_attempts) >= 10:
                raise ApiProblem(429, "登录尝试过多，请一分钟后再试")
            valid = isinstance(username, str) and isinstance(password, str)
            if valid:
                try:
                    # Compare both fields; preserve password whitespace exactly.
                    valid = (hmac.compare_digest(username.encode(), self.web_username.encode()) &
                             hmac.compare_digest(password.encode(), self.web_password.encode()))
                except UnicodeError:
                    valid = False
            if not valid:
                self.login_attempts.append(now)
                raise ApiProblem(401, "用户名或密码错误")
            self.sessions = {k: v for k, v in self.sessions.items() if v["expires"] > now}
            if len(self.sessions) >= 16:
                self.sessions.pop(next(iter(self.sessions)))
            session = secrets.token_urlsafe(32)
            self.sessions[session] = dict(csrf=secrets.token_urlsafe(32), expires=now + 43200)
            return session

    def session(self, cookie):
        try:
            jar = SimpleCookie()
            jar.load(cookie or "")
            key = jar["ur_session"].value
        except (KeyError, ValueError):
            raise ApiProblem(401, "请使用用户名和密码登录") from None
        with self.auth_lock:
            session = self.sessions.get(key)
            if not session or session["expires"] <= time.monotonic():
                raise ApiProblem(401, "管理会话已过期，请重新登录")
            return key, dict(session)

    def account(self):
        with self.account_lock:
            now = time.monotonic()
            if now - self.account_cache[0] > 60 or self.account_cache[1] is None:
                value = self.client.get_account_status()
                self.account_cache = (now, value)
            return self.account_cache[1]

    def settings(self):
        kind = next((k for k in CREDENTIAL_FIELDS if self.config.get(k)), "none")
        return dict(credential_type=kind, configured=kind != "none",
                    api_base_url=self.client.api_base,
                    web_host=getattr(self, "web_host", "127.0.0.1"),
                    web_port=getattr(self, "web_port", 8777))

    def runtime(self):
        return runtime_info(self.runtime_ips, self.web_host, self.web_port, dual_stack=self.dual_stack,
                            allowed_hosts=self.config.get("allowed_hosts"))

    def configuration(self, snapshot=None):
        config, revision, legacy, exists = snapshot or self.config_editor.read()
        redacted = self.config_editor.redacted(config)
        changed = [key for key, default in DEFAULT_CONFIG.items()
                   if config.get(key, default) != self.config.get(key, default)]
        return dict(config=redacted, source=json.dumps(redacted, ensure_ascii=False, indent=2),
                    revision=revision, file="config.json", exists=exists,
                    legacy_credentials=legacy, active_host=self.web_host, active_port=self.web_port,
                    restart_required=bool(changed), restart_fields=changed)

    async def _assert_account_idle(self):
        if await self.manager.list_gateways():
            raise ApiProblem(409, "变更认证或 API 地址前，请先删除网关并等待旧连接回收")
        if self.manager.has_owned_clients:
            raise ApiProblem(409, "旧连接回收或申请尚未完成，请稍后再试")
        state = self.state_dir / "gateways.json"
        if state.exists():
            stored = json.loads(state.read_text(encoding="utf-8"))
            if stored.get("owned_clients") or stored.get("owned"):
                raise ApiProblem(409, "旧连接回收尚未完成，请稍后再试")

    async def update_configuration(self, data):
        if self.updating_credentials or self.gateway_operations:
            raise ApiProblem(409, "网关或配置操作正在进行，请稍后再试")
        self.updating_credentials = True
        try:
            candidate = self.config_editor.candidate(data)
            identity_fields = (*CREDENTIAL_FIELDS, "api_base_url")
            if any(candidate.get(key, DEFAULT_CONFIG.get(key)) != self.config.get(key, DEFAULT_CONFIG.get(key))
                   for key in identity_fields):
                await self._assert_account_idle()
            snapshot = self.config_editor.save(candidate, data["revision"])
            return self.configuration(snapshot)
        finally:
            self.updating_credentials = False

    def validate_configuration(self, data):
        candidate = self.config_editor.candidate(data)
        submitted = parse_config(data["source"]) if "source" in data else data["config"]
        submitted = {**submitted, **data.get("fields", {})}
        # Return submitted secrets only, never resolved values from disk.
        for key in SECRET_FIELDS:
            candidate[key] = submitted.get(key, "")
        return {"config": candidate, "source": json.dumps(candidate, ensure_ascii=False, indent=2)}

    def clean_proxies(self):
        if self.updating_credentials:
            raise ApiProblem(409, "凭据更新正在进行，请稍后再试")
        try:
            if not getattr(self.client, "jwt", None) and not getattr(self.client, "api_key", None):
                self.client.login()
            res = self.client.clean_clients()
        except Exception as exc:
            raise ApiProblem(500, f"清理网页端 Proxies 失败: {exc}")
        with self.account_lock:
            self.account_cache = (0, None)
        return {
            "ok": True,
            "proxies_removed": res.get("proxies_removed", 0),
            "clients_reset": bool(res.get("clients_reset", False)),
            "message": f"成功删除网页端 PROXIES 代理实例: {res.get('proxies_removed', 0)} 个"
        }

    async def update_settings(self, data):
        if self.updating_credentials or self.gateway_operations:
            raise ApiProblem(409, "网关或凭据操作正在进行，请稍后再试")
        self.updating_credentials = True
        try:
            return await self._update_settings(data)
        finally:
            self.updating_credentials = False

    async def _update_settings(self, data):
        # Changing accounts while old-account clients still exist risks orphaning resources.
        if await self.manager.list_gateways():
            raise ApiProblem(409, "更新账号前请先删除网关，等待旧连接回收")
        if self.manager.has_owned_clients:
            raise ApiProblem(409, "旧连接回收或申请尚未完成，请稍后再试")
        state = self.state_dir / "gateways.json"
        if state.exists():
            stored = json.loads(state.read_text(encoding="utf-8"))
            if stored.get("owned_clients") or stored.get("owned"):
                raise ApiProblem(409, "旧连接回收尚未完成，请稍后再试")
        kind, value = data.get("credential_type"), data.get("credential")
        if kind not in ("api_key", "jwt_token", "seedphrase", "auth_code") or not isinstance(value, str) or not value.strip():
            raise ApiProblem(400, "请选择凭据类型并填写凭据")
        if len(value) > 8192:
            raise ApiProblem(400, "凭据过长")
        current, revision, _, _ = self.config_editor.read()
        candidate_config = dict(current)
        candidate_config.update({k: "" for k in CREDENTIAL_FIELDS})
        candidate_config[kind] = value.strip()
        candidate = build_client(candidate_config)
        await asyncio.to_thread(candidate.login)
        await asyncio.to_thread(candidate._request, "/network/user")
        if kind == "auth_code":
            # Persist exchanged session, not an already-consumed one-time code.
            candidate_config["auth_code"] = ""
            candidate_config["jwt_token"] = candidate.jwt
            candidate.auth_code = ""
            candidate.jwt_token = candidate.jwt
        self.config_editor.save(candidate_config, revision)
        # Only the upstream client is hot-reloaded here. Keep pending management
        # credentials/listener/Host settings distinct from the running snapshot.
        self.config.update({k: candidate_config[k]
                            for k in (*CREDENTIAL_FIELDS, "api_base_url", "timeout_seconds")})
        self.client = candidate
        self.manager.client = candidate
        self.catalog = CountryCatalog(candidate)
        self.account_cache = (0, None)
        return self.settings()

    async def validate_binding(self, data):
        if data.get("mode") == "country":
            snapshot = await asyncio.to_thread(self.catalog.get)
            match = next((c for c in snapshot["countries"]
                          if c["code"] == str(data.get("country_code", "")).upper()
                          and c["location_id"] == data.get("location_id")), None)
            if not match:
                raise ApiProblem(400, "国家与 location_id 不匹配，请刷新国家列表重新选择")

    async def gateway_mutation(self, method, gateway_id, action, data):
        if self.updating_credentials:
            raise ApiProblem(409, "凭据更新正在进行，请稍后操作网关")
        if method == "POST" and not gateway_id:
            saved, _, _, _ = self.config_editor.read()
            if any(saved.get(key, DEFAULT_CONFIG.get(key)) != self.config.get(key, DEFAULT_CONFIG.get(key))
                   for key in (*CREDENTIAL_FIELDS, "api_base_url")):
                raise ApiProblem(409, "认证或 API 配置已更新，请先重启服务，再新建网关")
        self.gateway_operations += 1
        try:
            if method == "PUT" and gateway_id:
                # Keep read/merge/catalog validation/update in one per-gateway transaction.
                lock = self.gateway_update_locks.get(gateway_id)
                if lock is None:
                    lock = asyncio.Lock()
                    self.gateway_update_locks[gateway_id] = lock
                async with lock:
                    return await self._gateway_mutation(method, gateway_id, action, data)
            return await self._gateway_mutation(method, gateway_id, action, data)
        finally:
            self.gateway_operations -= 1

    async def _gateway_mutation(self, method, gateway_id, action, data):
        records = self._load_gateway_auth()
        if method == "POST" and not gateway_id:
            await self.validate_binding(data)
            res = await self.manager.create_gateway(data)
            gid = res.get("id")
            if gid and data.get("username") and data.get("password"):
                records[gid] = {"username": data["username"], "password": data["password"]}
                self._save_gateway_auth(records)
                res["username"] = data["username"]
                res["password"] = data["password"]
            return res
        if method == "PUT":
            current = next((g for g in await self.manager.list_gateways() if g["id"] == gateway_id), None)
            if current is None:
                raise KeyError(gateway_id)
            await self.validate_binding({**current, **data})
            res = await self.manager.update_gateway(gateway_id, data)
            gid = gateway_id
            if data.get("username") and data.get("password"):
                records[gid] = {"username": data["username"], "password": data["password"]}
                self._save_gateway_auth(records)
                res["username"] = data["username"]
                res["password"] = data["password"]
            elif "username" in data and not data.get("username"):
                if gid in records:
                    records.pop(gid, None)
                    self._save_gateway_auth(records)
            return res
        if method == "DELETE":
            res = await self.manager.delete_gateway(gateway_id)
            if gateway_id in records:
                records.pop(gateway_id, None)
                self._save_gateway_auth(records)
            return res
        actions = dict(start=self.manager.start_gateway, stop=self.manager.stop_gateway,
                       rotate=self.manager.rotate_gateway, check=self.manager.check_gateway)
        if method == "POST" and action in actions:
            res = await actions[action](gateway_id)
            if gateway_id in records:
                res["username"] = records[gateway_id].get("username", "")
                res["password"] = records[gateway_id].get("password", "")
            return res
        raise ApiProblem(404, "接口不存在")

    def dispatch(self, method, path, query, data):
        if path == "/api/config":
            if method == "GET":
                return self.configuration()
            if method == "PUT":
                return self.call(self.update_configuration(data))
        if path == "/api/config/validate" and method == "POST":
            return self.validate_configuration(data)
        if path == "/api/gateways" and method == "GET":
            async def get_gateways():
                rows = await self.manager.list_gateways()
                records = self._load_gateway_auth()
                augmented = []
                for row in rows:
                    gid = row.get("id")
                    if gid in records:
                        row = dict(row, username=records[gid].get("username", ""),
                                   password=records[gid].get("password", ""))
                    augmented.append(row)
                return {"gateways": augmented}
            return self.call(get_gateways())
        if path == "/api/countries" and method == "GET":
            return self.catalog.get()
        if path == "/api/countries/refresh" and method == "POST":
            return self.catalog.get(refresh=True)
        if path == "/api/account" and method == "GET":
            return self.account()
        if path == "/api/proxies/clean" and method == "POST":
            return self.clean_proxies()
        if path == "/api/settings":
            if method == "GET":
                return self.settings()
            if method == "PUT":
                return self.call(self.update_settings(data))
        if path == "/api/events" and method == "GET":
            after = int(parse_qs(query).get("after", ["0"])[0])
            async def events():
                return {"events": self.manager.events(after=after)}
            return self.call(events())
        parts = path.strip("/").split("/")
        if parts[:2] == ["api", "gateways"] and len(parts) <= 4:
            return self.call(self.gateway_mutation(method, parts[2] if len(parts) > 2 else None,
                parts[3] if len(parts) > 3 else None, data))
        raise ApiProblem(404, "接口不存在")


class ManagementServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, app):
        self.app = app
        raw_host, port = address
        clean_host = raw_host.strip("[]") if isinstance(raw_host, str) else raw_host
        self.bind_host = clean_host
        self.slots = threading.BoundedSemaphore(64)
        self.is_dual_stack = clean_host in ("::", "0.0.0.0", "dual", "all", "")

        if self.is_dual_stack:
            try:
                self.address_family = socket.AF_INET6
                super().__init__(("::", port), ManagementHandler)
                self.app.web_host = "::"
                self.app.web_port = self.server_port
                self.app.dual_stack = True  # Verified before bind in server_bind().
                return
            except OSError:
                self.address_family = socket.AF_INET
                self.is_dual_stack = False
                super().__init__(("0.0.0.0", port), ManagementHandler)
                self.app.web_host = "0.0.0.0"
                self.app.web_port = self.server_port
                return
        elif ":" in clean_host:
            self.address_family = socket.AF_INET6
            super().__init__((clean_host, port), ManagementHandler)
        else:
            self.address_family = socket.AF_INET
            super().__init__((clean_host, port), ManagementHandler)

        self.app.web_host = clean_host
        self.app.web_port = self.server_port

    def server_bind(self):
        if getattr(self, "address_family", None) == socket.AF_INET6:
            dual = getattr(self, "is_dual_stack", False)
            try:
                self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0 if dual else 1)
                if dual and self.socket.getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY):
                    raise OSError("IPv6 listener does not support IPv4 connections")
            except (AttributeError, OSError):
                if dual:
                    # Let construction close the socket and take the IPv4 fallback.
                    raise OSError("IPv4/IPv6 dual-stack listener unavailable") from None
        super().server_bind()

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        import sys
        exc_type, _, _ = sys.exc_info()
        if exc_type and issubclass(exc_type, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


class ManagementHandler(BaseHTTPRequestHandler):
    server_version = "URnetwork-Local"
    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def handle(self):
        try:
            super().handle()
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            pass

    def log_message(self, *args):
        pass  # Never log cookies, token-bearing URLs or credential request bodies.

    def _send(self, code, body, content_type="application/json; charset=utf-8", cookie=None):
        # Windows may reset a socket closed with unread request bytes, losing a 4xx.
        # Drain only small, explicitly framed bodies, with a bounded deadline.
        if code >= 400 and not getattr(self, "_body_read", False) and not self.headers.get("Transfer-Encoding"):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if 0 < length <= 16384:
                    self.connection.settimeout(1)
                    self.rfile.read(length)
            except (OSError, ValueError):
                pass
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _handle(self):
        self._body_read = False
        try:
            port = self.server.server_address[1]
            bind_host = getattr(self.server, "bind_host", "127.0.0.1")
            is_loopback = bind_host in ("127.0.0.1", "localhost", "::1")

            req_host = self.headers.get("Host", "")
            if not req_host:
                raise ApiProblem(400, "缺少 Host 头")

            allowed = self.server.app.config.get("allowed_hosts") or []
            if is_loopback and not allowed:
                hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}", "127.0.0.1", "localhost", "[::1]"}
                if req_host not in hosts:
                    raise ApiProblem(403, "Host 校验失败")
            else:
                if not host_allowed(req_host, allowed):
                    raise ApiProblem(403, "Host 不在允许列表中")
                if any(ord(c) <= 32 or c == "/" or c == chr(92) for c in req_host):
                    raise ApiProblem(400, "非法 Host 头")

            origin = self.headers.get("Origin")
            if origin:
                origin_parsed = urlsplit(origin)
                # Host allowlisting is not permission for cross-origin writes.
                # Include the port: another service on the same host is a different origin.
                if (origin_parsed.scheme not in ("http", "https") or
                        origin_parsed.netloc.lower() != req_host.lower() or
                        origin_parsed.username is not None):
                    raise ApiProblem(403, "跨站请求已阻止")

            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                raise ApiProblem(403, "跨站请求已阻止")
            parsed = urlsplit(self.path)
            path = parsed.path
            app = self.server.app
            if self.command == "GET" and path in ("/", "/index.html", "/app.js", "/style.css"):
                name = "index.html" if path == "/" else path[1:]
                types = {"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8", "style.css": "text/css; charset=utf-8"}
                self._send(200, (ROOT / "web" / name).read_bytes(), types[name])
                return
            if self.command == "GET" and path == "/api/health":
                self._send(200, {"status": "ok"})
                return
            if self.command == "GET" and path == "/api/runtime":
                self._send(200, app.runtime())
                return
            data = {}
            if self.command in ("POST", "PUT", "DELETE"):
                if self.headers.get_content_type() != "application/json":
                    raise ApiProblem(415, "仅接受 application/json")
                if self.headers.get("Transfer-Encoding"):
                    raise ApiProblem(400, "不支持分块管理请求")
                length = int(self.headers.get("Content-Length", "0"))
                limit = 524288 if path in ("/api/config", "/api/config/validate") else 16384
                if not 0 <= length <= limit:
                    raise ApiProblem(413, "请求体过大")
                raw_body = self.rfile.read(length)
                self._body_read = True
                data = parse_config((raw_body or b"{}").decode("utf-8"),
                                    max_bytes=limit, allow_comments=False)
                if not isinstance(data, dict):
                    raise ApiProblem(400, "请求体应为 JSON 对象")
            if self.command == "POST" and path == "/api/login":
                session = app.login(data.get("username"), data.get("password"))
                self._send(200, {"ok": True}, cookie=f"ur_session={session}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200")
                return
            key, session = app.session(self.headers.get("Cookie"))
            if self.command in ("POST", "PUT", "DELETE"):
                if not hmac.compare_digest(self.headers.get("X-CSRF-Token", "").encode(), session["csrf"].encode()):
                    raise ApiProblem(403, "CSRF 校验失败，请刷新页面")
            if self.command == "GET" and path == "/api/session":
                self._send(200, {"csrf": session["csrf"]})
            elif self.command == "POST" and path == "/api/logout":
                with app.auth_lock:
                    app.sessions.pop(key, None)
                self._send(200, {"ok": True}, cookie="ur_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0")
            else:
                self._send(200, app.dispatch(self.command, path, parsed.query, data))
        except ApiProblem as exc:
            self._send(exc.code, {"error": exc.message})
        except ConfigConflict as exc:
            self._send(409, {"error": str(exc)})
        except ConfigError as exc:
            self._send(400, {"error": str(exc)})
        except KeyError:
            self._send(404, {"error": "网关或资源不存在"})
        except (ValueError, TypeError):
            self._send(400, {"error": "参数无效，请检查端口、国家绑定和检测间隔"})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            self._send(503, {"error": f"操作失败 ({type(exc).__name__})，请检查账号、端口和事件日志"})

    do_GET = _handle
    do_POST = _handle
    do_PUT = _handle
    do_DELETE = _handle


def serve(config, *, host="127.0.0.1", port=8777, state_dir=None, open_browser=False):
    clean_host = host.strip("[]") if isinstance(host, str) else host
    if clean_host not in ("localhost", "dual", "all"):
        try:
            ipaddress.ip_address(clean_host)
        except ValueError:
            raise ValueError(f"管理界面 host 须为有效 IP 地址（如 127.0.0.1、0.0.0.0、::）或 localhost")
    if not 1 <= port <= 65535:
        raise ValueError("管理端口应在 1–65535")
    state_dir = Path(state_dir or ROOT / ".local")
    lock = ProcessLock(state_dir / "manager.lock")
    app = None
    server = None
    try:
        app = ManagementApp(config, state_dir, web_host=host, web_port=port)
        # Bind before restoring remote resources; a conflicting process does not allocate clients.
        try:
            server = ManagementServer((host, port), app)
        except OSError as exc:
            raise ConfigError(
                f"管理监听失败（{host}:{port}，系统错误 {exc.errno}）。"
                "请检查地址是否属于本机、端口是否被占用；可用 --web-port 更换端口，"
                "或用 --web-host 0.0.0.0 监听网卡地址。"
            ) from None
        app.start()
        runtime = app.runtime()
        listen_host = f"[{app.web_host}]" if ":" in app.web_host else app.web_host
        print(f"Web 管理控制台已监听 {listen_host}:{server.server_port}", flush=True)
        print("运行环境 IP：" + "、".join(runtime["ips"]), flush=True)
        if runtime["loopback_fallback"]:
            print("未检测到非回环地址，已回退到本机回环 IP。", flush=True)
        print("运行 IP 为主机/容器路由地址，不代表公网 NAT 地址或代理出口 IP。", flush=True)
        for url in runtime["management_urls"]:
            print("管理访问地址：" + url, flush=True)
        if not runtime["management_urls"]:
            print("Host 白名单未匹配访问地址，请检查 allowed_hosts。", flush=True)
        if runtime["loopback_only"]:
            print("当前仅本机可访问；运行环境 IP 不改变监听范围。", flush=True)
            print("其他设备直连管理页面需使用 --web-host 0.0.0.0，并放行对应管理端口。", flush=True)
        print("页面内访问地址随浏览器地址栏自适应；网关代理仍按各自实际监听范围使用。", flush=True)
        print("登录方式：用户名 / 密码（可在配置面板修改 web_username / web_password，重启生效）", flush=True)
        if app.web_password == DEFAULT_CONFIG["web_password"]:
            print("提示：正在使用默认管理密码，请在配置面板修改。", flush=True)
        print("HTTP 与 SOCKS5 共用每个网关的自定义端口。Ctrl+C 停止。", flush=True)
        if open_browser and runtime["management_urls"]:
            webbrowser.open(runtime["management_urls"][0])
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        if server:
            server.server_close()
        if app:
            app.close()
        lock.close()
