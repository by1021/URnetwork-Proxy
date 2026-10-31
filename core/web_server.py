"""Loopback-only management API and static UI. No public deployment is implied."""
import asyncio
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
from .config import CONFIG_FILE, CREDENTIAL_FIELDS, DEFAULT_CONFIG, ConfigError, parse_config
from .config_editor import ConfigEditor, ConfigConflict

ROOT = Path(__file__).resolve().parent.parent


def build_client(config):
    return URClient(**{k: config.get(k, "") for k in CREDENTIAL_FIELDS},
                    api_base=config.get("api_base_url", "https://api.bringyour.com"),
                    timeout=config.get("timeout_seconds", 15))


class ApiProblem(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message


class ManagementApp:
    def __init__(self, config, state_dir, *, manager=None, client=None, web_port=None, config_path=None):
        self.config = dict(config)
        self.web_port = web_port if web_port is not None else self.config.get("web_port", 8777)
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
        self.token = secrets.token_urlsafe(32)
        self.sessions = {}
        self.login_attempts = deque()
        self.auth_lock = threading.Lock()
        self.started = False
        self.updating_credentials = False
        self.gateway_operations = 0
        self.gateway_update_locks = weakref.WeakValueDictionary()
        self.account_cache = (0, None)
        self.account_lock = threading.Lock()

    def start(self):
        self.thread.start()
        self.started = True
        self.call(self.manager.initialize())

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

    def login(self, token):
        with self.auth_lock:
            now = time.monotonic()
            while self.login_attempts and self.login_attempts[0] < now - 60:
                self.login_attempts.popleft()
            if len(self.login_attempts) >= 10:
                raise ApiProblem(429, "登录尝试过多，请一分钟后再试")
            if not isinstance(token, str) or not hmac.compare_digest(token.encode(), self.token.encode()):
                self.login_attempts.append(now)
                raise ApiProblem(401, "管理令牌错误")
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
            raise ApiProblem(401, "请使用启动窗口中的管理链接登录") from None
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
                    web_port=getattr(self, "web_port", 8777))

    def configuration(self, snapshot=None):
        config, revision, legacy, exists = snapshot or self.config_editor.read()
        redacted = self.config_editor.redacted(config)
        changed = [key for key, default in DEFAULT_CONFIG.items()
                   if config.get(key, default) != self.config.get(key, default)]
        return dict(config=redacted, source=json.dumps(redacted, ensure_ascii=False, indent=2),
                    revision=revision, file="config.json", exists=exists,
                    legacy_credentials=legacy, active_port=self.web_port,
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
        for key in CREDENTIAL_FIELDS:
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
        self.config = candidate_config
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
        if method == "POST" and not gateway_id:
            await self.validate_binding(data)
            return await self.manager.create_gateway(data)
        if method == "PUT":
            current = next((g for g in await self.manager.list_gateways() if g["id"] == gateway_id), None)
            if current is None:
                raise KeyError(gateway_id)
            await self.validate_binding({**current, **data})
            return await self.manager.update_gateway(gateway_id, data)
        if method == "DELETE":
            return await self.manager.delete_gateway(gateway_id)
        actions = dict(start=self.manager.start_gateway, stop=self.manager.stop_gateway,
                       rotate=self.manager.rotate_gateway, check=self.manager.check_gateway)
        if method == "POST" and action in actions:
            return await actions[action](gateway_id)
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
            return {"gateways": self.call(self.manager.list_gateways())}
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
    allow_reuse_address = False

    def __init__(self, address, app):
        self.app = app
        self.slots = threading.BoundedSemaphore(32)
        super().__init__(address, ManagementHandler)
        self.app.web_port = self.server_port

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


class ManagementHandler(BaseHTTPRequestHandler):
    server_version = "URnetwork-Local"
    def setup(self):
        super().setup()
        self.connection.settimeout(15)

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
            hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
            if self.headers.get("Host") not in hosts:
                raise ApiProblem(403, "Host 校验失败")
            origin = self.headers.get("Origin")
            if origin and origin not in {"http://" + host for host in hosts}:
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
                session = app.login(data.get("token"))
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
    if host not in ("127.0.0.1", "localhost"):
        raise ValueError("管理界面仅监听 127.0.0.1 / localhost")
    if not 1 <= port <= 65535:
        raise ValueError("管理端口应在 1–65535")
    state_dir = Path(state_dir or ROOT / ".local")
    lock = ProcessLock(state_dir / "manager.lock")
    app = ManagementApp(config, state_dir, web_port=port)
    server = None
    try:
        # Bind before restoring remote resources; a conflicting process does not allocate clients.
        server = ManagementServer(("127.0.0.1", port), app)
        app.start()
        url = f"http://127.0.0.1:{server.server_port}/#token={app.token}"
        print("本地管理链接（仅此机器，请勿分享）：", url, flush=True)
        print("HTTP 与 SOCKS5 共用每个网关的自定义端口。Ctrl+C 停止。", flush=True)
        if open_browser:
            webbrowser.open(url)
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        if server:
            server.server_close()
        app.close()
        lock.close()
