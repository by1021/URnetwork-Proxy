"""Loop-confined multi-gateway lifecycle management (Python 3.10+).

All public methods except ``events`` are async. Validation/not-found/storage
errors raise ValueError/KeyError/OSError; operational failures return a gateway
snapshot with a sanitized ``last_error``. Local relay credentials are memory-only.
The state file contains configuration and an ownership journal, never upstreams.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import ipaddress
import json
import math
import os
from pathlib import Path
import random
import re
import tempfile
import time
from typing import Any, Awaitable, Callable
import uuid


DRAIN_SECONDS = 30.0
BACKOFF_MIN = 5.0
BACKOFF_MAX = 300.0
HOURLY_CREATES = 12
MAX_OWNED_PER_GATEWAY = 2
PROBE_TIMEOUT = 35.0
EVENT_LIMIT = 1000
CONFIG_DEFAULTS = {
    "name": "Gateway", "host": "127.0.0.1", "port": 1080, "mode": "best",
    "country_code": "", "location_id": None, "enabled": False,
    "auto_heal": True, "interval": 30, "failures_before_rotate": 3,
}


async def probe_control() -> bool:
    """Late binding permits integration with the parent's checker implementation."""
    from core.checker import probe_control as control
    return await control()


async def _default_probe(upstream: dict[str, Any]) -> dict[str, Any]:
    from core.checker import probe_upstream
    return await probe_upstream(upstream)


def _default_relay(*args: Any, **kwargs: Any) -> Any:
    from core.multiplexer import MixedProxyServer
    return MixedProxyServer(*args, **kwargs)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _delay(attempts: int) -> float:
    ceiling = min(BACKOFF_MAX, BACKOFF_MIN * 2 ** min(attempts, 6))
    return random.uniform(max(BACKOFF_MIN, ceiling / 2), ceiling)


def _http_status(error: Exception) -> int:
    status = getattr(error, "status", None)
    return status if type(status) is int and 100 <= status <= 599 else 0


def _error_code(prefix: str, error: Exception) -> str:
    status = _http_status(error)
    return f"{prefix}_http_{status}" if status else prefix


def _validate(data: dict[str, Any], base: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) - (set(CONFIG_DEFAULTS) | {"username", "password"}):
        raise ValueError("invalid gateway configuration fields")
    cfg = dict(CONFIG_DEFAULTS if base is None else base)
    cfg.update(data)
    for key in ("name", "host", "country_code", "mode", "username", "password"):
        if not isinstance(cfg.get(key, ""), str):
            raise ValueError(f"{key} must be a string")
    if not cfg["name"].strip() or len(cfg["name"]) > 128:
        raise ValueError("name must contain 1..128 characters")
    host = "127.0.0.1" if cfg["host"] == "localhost" else cfg["host"]
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError("host must be a loopback IP address") from None
    if not address.is_loopback or "%" in host:
        raise ValueError("host must be a loopback IP address")
    cfg["host"] = str(address)
    for key, minimum, maximum in (("port", 1, 65535), ("interval", 5, 3600),
                                  ("failures_before_rotate", 1, 100)):
        if type(cfg[key]) is not int or not minimum <= cfg[key] <= maximum:
            raise ValueError(f"{key} must be an integer in {minimum}..{maximum}")
    for key in ("enabled", "auto_heal"):
        if type(cfg[key]) is not bool:
            raise ValueError(f"{key} must be a boolean")
    cfg["country_code"] = cfg["country_code"].strip().upper()
    if cfg["mode"] not in ("best", "country"):
        raise ValueError("mode must be best or country")
    if cfg["mode"] == "country":
        if not re.fullmatch(r"[A-Z]{2}", cfg["country_code"]):
            raise ValueError("country mode requires a two-letter country_code")
        if not isinstance(cfg["location_id"], str) or not cfg["location_id"].strip():
            raise ValueError("country mode requires location_id")
    else:
        cfg["country_code"], cfg["location_id"] = "", None
    if cfg["location_id"] is not None and len(cfg["location_id"]) > 256:
        raise ValueError("location_id is too long")
    if bool(cfg.get("username")) != bool(cfg.get("password")):
        raise ValueError("username and password must be supplied together")
    if any(len(cfg.get(key, "").encode("utf-8")) > 255 for key in ("username", "password")):
        raise ValueError("local credentials must fit SOCKS5 authentication")
    return cfg


@dataclass
class _Gateway:
    id: str
    config: dict[str, Any]
    created_at: str = field(default_factory=_timestamp)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    upstream: dict[str, Any] | None = None
    relay: Any = None
    auth_missing: bool = False
    status: str = "stopped"
    exit_ip: str = ""
    latency_ms: float = 0.0
    last_check: str | None = None
    failures: int = 0
    rotations: int = 0
    last_error: str = ""
    generation: int = 0
    revision: int = 0
    wanted: bool = False
    deleting: bool = False
    attempts: int = 0
    next_create: float = 0.0
    next_check: float = 0.0
    creates: deque[float] = field(default_factory=deque)
    maintenance: asyncio.Task[Any] | None = None


@dataclass
class _Owned:
    gateway_id: str
    due: float | None = None  # None = active; number = cleanup/drain deadline
    attempts: int = 0


class GatewayManager:
    def __init__(self, client: Any, state_path: str | os.PathLike[str], *,
                 probe: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
                 relay_factory: Callable[..., Any] | None = None) -> None:
        self.client = client
        self.state_path = Path(state_path)
        self._probe = probe or _default_probe
        self._relay_factory = relay_factory or _default_relay
        self._gateways: dict[str, _Gateway] = {}
        self._owned: dict[str, _Owned] = {}
        self._reservations: dict[tuple[str, int], str] = {}
        self._events: deque[dict[str, Any]] = deque(maxlen=EVENT_LIMIT)
        self._event_id = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._initialized = False
        self._restored = False
        self._closing = False
        self._init_lock = asyncio.Lock()
        self._create_lock = asyncio.Lock()
        self._cleanup_lock = asyncio.Lock()
        self._control_lock = asyncio.Lock()
        self._global_attempts = 0
        self._inflight_creates = 0
        self._global_next_create = 0.0
        self._offline = False
        self._control_due = 0.0
        self._control_attempts = 0
        self._tasks: set[asyncio.Task[Any]] = set()
        self._worker: asyncio.Task[Any] | None = None
        self._close_task: asyncio.Task[Any] | None = None
        self._shutdown = asyncio.Event()

    @property
    def has_owned_clients(self) -> bool:
        """Credential guard: active/draining/pending IDs or an in-flight allocation."""
        return bool(self._owned or self._inflight_creates)

    @staticmethod
    def _clock() -> float:
        return time.monotonic()

    def _assert_loop(self, *, ready: bool = True) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        if self._loop is not loop:
            raise RuntimeError("GatewayManager must stay on its owning asyncio loop")
        if ready and (not self._initialized or self._closing):
            raise RuntimeError("GatewayManager is not open")

    def _event(self, gateway_id: str, level: str, message: str) -> None:
        self._event_id += 1
        self._events.append(dict(id=self._event_id, time=_timestamp(),
                                 gateway_id=gateway_id, level=level, message=message))

    def events(self, after: int = 0) -> list[dict[str, Any]]:
        """Return copies of the last 1000 events; call on the manager's loop."""
        if type(after) is not int or after < 0:
            raise ValueError("after must be a nonnegative integer")
        return [dict(event) for event in self._events if event["id"] > after]

    def _snapshot(self, g: _Gateway) -> dict[str, Any]:
        result = {key: g.config[key] for key in CONFIG_DEFAULTS}
        result.update(id=g.id, created_at=g.created_at, status=g.status, exit_ip=g.exit_ip,
                      latency_ms=g.latency_ms, last_check=g.last_check, failures=g.failures,
                      rotations=g.rotations, last_error=g.last_error,
                      connections=int(g.relay.connections) if g.relay else 0)
        return result

    def _get(self, gateway_id: str) -> _Gateway:
        self._assert_loop()
        g = self._gateways[gateway_id]
        if g.deleting:
            raise KeyError(gateway_id)
        return g

    def _spawn(self, coroutine: Awaitable[Any]) -> asyncio.Task[Any]:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)

        def finished(done: asyncio.Task[Any]) -> None:
            self._tasks.discard(done)
            if not done.cancelled() and done.exception() is not None:
                self._event("", "error", "operation_failed")

        task.add_done_callback(finished)
        return task

    async def _finish(self, coroutine: Awaitable[Any]) -> Any:
        # Cancelling an HTTP request must not orphan an in-flight to_thread create.
        return await asyncio.shield(self._spawn(coroutine))

    def _persist(self) -> None:
        state = dict(version=1, gateways=[dict(
            id=g.id, created_at=g.created_at,
            auth_required=bool(g.auth_missing or g.config.get("username")),
            **{key: g.config[key] for key in CONFIG_DEFAULTS}) for g in self._gateways.values()],
            owned=[dict(client_id=cid, gateway_id=item.gateway_id)
                   for cid, item in self._owned.items()])
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{self.state_path.name}.",
                                         dir=self.state_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(state, output, ensure_ascii=False, allow_nan=False)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.state_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _load(self) -> None:
        if not self.state_path.exists():
            return
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            if (not isinstance(state, dict) or state.get("version") != 1
                    or not isinstance(state.get("gateways"), list)
                    or not isinstance(state.get("owned"), list)):
                raise ValueError("invalid state schema")
            gateways, owned, endpoints = {}, {}, set()
            for item in state["gateways"]:
                cfg = _validate({key: item[key] for key in CONFIG_DEFAULTS})
                gid, created = item["id"], item["created_at"]
                if not isinstance(gid, str) or not gid or not isinstance(created, str):
                    raise ValueError("invalid gateway metadata")
                endpoint = (cfg["host"], cfg["port"])
                if gid in gateways or endpoint in endpoints:
                    raise ValueError("duplicate gateway in state")
                if type(item.get("auth_required", False)) is not bool:
                    raise ValueError("invalid authentication requirement")
                gateways[gid] = _Gateway(gid, cfg, created, wanted=cfg["enabled"],
                                         auth_missing=item.get("auth_required", False))
                endpoints.add(endpoint)
            for item in state["owned"]:
                cid, gid = item["client_id"], item["gateway_id"]
                if not self._valid_client_id(cid) or not isinstance(gid, str) or cid in owned:
                    raise ValueError("invalid ownership journal")
                owned[cid] = _Owned(gid, due=0.0)
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError("invalid gateway state file") from None
        self._gateways, self._owned = gateways, owned

    async def initialize(self) -> dict[str, Any]:
        self._assert_loop(ready=False)
        return await self._finish(self._initialize())

    async def _initialize(self) -> dict[str, Any]:
        async with self._init_lock:
            if self._closing:
                raise RuntimeError("GatewayManager is closed; create a new instance")
            if self._restored:
                return dict(initialized=True, gateways=len(self._gateways))
            if not self._initialized:
                self._load()
                self._initialized = True
            try:
                await self._restore()
                self._restored = True
            finally:
                if self._worker is None and not self._closing:
                    self._worker = asyncio.create_task(self._maintain())
            return dict(initialized=True, gateways=len(self._gateways))

    async def _restore(self) -> None:
        await self._cleanup_due()
        for g in self._gateways.values():
            if g.wanted and not self._closing:
                async with g.lock:
                    await self._start(g, g.generation)

    async def list_gateways(self) -> list[dict[str, Any]]:
        self._assert_loop()
        return [self._snapshot(g) for g in self._gateways.values() if not g.deleting]

    def _reserve(self, config: dict[str, Any], gateway_id: str) -> tuple[str, int]:
        endpoint = (config["host"], config["port"])
        if any(g.id != gateway_id and (g.config["host"], g.config["port"]) == endpoint
               for g in self._gateways.values()):
            raise ValueError("gateway endpoint already configured")
        if self._reservations.get(endpoint, gateway_id) != gateway_id:
            raise ValueError("gateway endpoint is reserved")
        self._reservations[endpoint] = gateway_id
        return endpoint

    async def create_gateway(self, data: dict[str, Any]) -> dict[str, Any]:
        self._assert_loop()
        cfg = _validate(data)
        g = _Gateway(uuid.uuid4().hex, cfg, wanted=cfg["enabled"])
        endpoint = self._reserve(cfg, g.id)
        try:
            self._gateways[g.id] = g
            try:
                self._persist()
            except OSError:
                del self._gateways[g.id]
                raise
        finally:
            self._reservations.pop(endpoint, None)
        self._event(g.id, "info", "created")
        if cfg["enabled"]:
            return await self.start_gateway(g.id)
        return self._snapshot(g)

    async def start_gateway(self, gateway_id: str) -> dict[str, Any]:
        g = self._get(gateway_id)
        return await self._finish(self._start_locked(g, g.generation))

    async def _start_locked(self, g: _Gateway, generation: int) -> dict[str, Any]:
        async with g.lock:
            if g.generation != generation or g.deleting or self._closing:
                return self._snapshot(g)
            old_enabled = g.config["enabled"]
            g.config["enabled"] = True
            try:
                self._persist()
            except OSError:
                g.config["enabled"] = old_enabled
                raise
            g.wanted = True
            await self._start(g, generation)
            return self._snapshot(g)

    def _alive(self, g: _Gateway, generation: int) -> bool:
        return (g.wanted and not g.deleting and not self._closing
                and generation == g.generation)

    def _make_relay(self, g: _Gateway, cfg: dict[str, Any]) -> Any:
        # A pre-bound replacement must not publish the old country's upstream.
        relay = self._relay_factory(cfg["host"], cfg["port"],
                                   lambda: g.upstream if g.relay is relay else None,
                                   username=cfg.get("username", ""),
                                   password=cfg.get("password", ""),
                                   on_result=lambda ok: self._on_result(g, ok))
        return relay

    async def _bind(self, g: _Gateway, cfg: dict[str, Any]) -> Any:
        relay = self._make_relay(g, cfg)
        try:
            await relay.start()
        except Exception:
            await relay.close()
            raise
        return relay

    async def _start(self, g: _Gateway, generation: int) -> None:
        if not self._alive(g, generation) or g.upstream is not None:
            return
        if g.auth_missing:
            self._failure(g, "local_credentials_required", "error")
            g.next_check = self._clock() + g.config["interval"]
            return
        if g.relay is None:
            try:
                g.relay = await self._bind(g, g.config)
            except Exception:
                self._failure(g, "local_bind_failed", "error")
                g.next_check = self._clock() + g.config["interval"]
                return
        g.status = "starting"
        try:
            await self._rotate(g, generation)
        except OSError:
            self._failure(g, "state_write_failed", "error")
            raise
        finally:
            if g.upstream is None and g.relay is not None:
                await g.relay.close()
                g.relay = None
            g.next_check = self._clock() + g.config["interval"]

    def _failure(self, g: _Gateway, message: str, status: str = "degraded") -> None:
        g.last_error, g.status = message, status
        self._event(g.id, "warning", message)

    def _backoff(self, g: _Gateway) -> None:
        g.attempts += 1
        g.next_create = self._clock() + _delay(g.attempts)

    async def _control_available(self) -> bool:
        async with self._control_lock:
            if self._clock() < self._control_due:
                return not self._offline
            try:
                available = await asyncio.wait_for(probe_control(), PROBE_TIMEOUT)
            except Exception:
                available = False
            self._offline = available is not True
            self._control_attempts = self._control_attempts + 1 if self._offline else 0
            self._control_due = self._clock() + (_delay(self._control_attempts)
                                                if self._offline else BACKOFF_MIN)
            return not self._offline

    async def _can_create(self, g: _Gateway) -> bool:
        now = self._clock()
        if self._offline and not await self._control_available():
            self._failure(g, "network_offline", "offline")
            return False
        if now < max(g.next_create, self._global_next_create):
            self._failure(g, "creation_backoff", "degraded" if g.upstream else "backoff")
            return False
        # Deleting/recreating a gateway must not bypass the per-gateway leak cap.
        if any(item.gateway_id not in self._gateways
               or self._gateways[item.gateway_id].deleting for item in self._owned.values()):
            self._failure(g, "orphan_cleanup_pending", "degraded" if g.upstream else "backoff")
            return False
        owned = [item for item in self._owned.values() if item.gateway_id == g.id]
        if len(owned) >= MAX_OWNED_PER_GATEWAY or any(item.attempts for item in owned):
            self._failure(g, "owned_cleanup_pending", "degraded" if g.upstream else "backoff")
            return False
        while g.creates and g.creates[0] <= now - 3600:
            g.creates.popleft()
        if len(g.creates) >= HOURLY_CREATES:
            self._failure(g, "hourly_creation_limit", "degraded" if g.upstream else "backoff")
            return False
        return True

    @staticmethod
    def _valid_client_id(value: Any) -> bool:
        return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", value) is not None

    async def _candidate(self, g: _Gateway, cfg: dict[str, Any], generation: int
                         ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        async with self._create_lock:
            if not self._alive(g, generation) or not await self._can_create(g):
                return None
            if not self._alive(g, generation):
                return None
            g.creates.append(self._clock())
            self._inflight_creates += 1
            try:
                upstream = await asyncio.to_thread(self.client.create_proxy, mode=cfg["mode"],
                                                   target=cfg["country_code"],
                                                   location_id=cfg["location_id"])
            except Exception as exc:
                self._global_attempts += 1
                self._global_next_create = self._clock() + _delay(self._global_attempts)
                self._backoff(g)
                self._failure(g, _error_code("remote_create_failed", exc),
                              "degraded" if g.upstream else "error")
                return None
            finally:
                self._inflight_creates -= 1
        if not isinstance(upstream, dict) or not self._valid_client_id(upstream.get("client_id")):
            self._backoff(g)
            self._failure(g, "invalid_create_response", "degraded" if g.upstream else "error")
            return None
        cid = upstream["client_id"]
        if cid in self._owned:
            self._backoff(g)
            self._failure(g, "duplicate_client_id", "degraded" if g.upstream else "error")
            return None
        self._owned[cid] = _Owned(g.id)
        try:
            self._persist()  # Register ownership before any cancellable probe.
        except OSError:
            self._owned[cid].due = 0.0
            await self._remove_owned(cid)
            raise
        if not self._alive(g, generation):
            await self._discard(cid)
            return None
        result = await self._probe_result(upstream)
        if not result["ok"] or not self._alive(g, generation):
            await self._discard(cid)
            self._backoff(g)
            online = await self._control_available() if not result["ok"] else True
            self._failure(g, "candidate_probe_failed" if online else "network_offline",
                          "degraded" if g.upstream and online else "error" if online else "offline")
            return None
        self._global_attempts, self._global_next_create = 0, 0.0
        return upstream, result

    async def _probe_result(self, upstream: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await asyncio.wait_for(self._probe(upstream), PROBE_TIMEOUT)
            ok = all(result.get(key) is True for key in ("ok", "http_ok", "socks_ok"))
            ip = str(ipaddress.ip_address(result["exit_ip"])) if result.get("exit_ip") else ""
            latency = float(result.get("latency_ms", 0.0))
            if not math.isfinite(latency) or latency < 0:
                raise ValueError("invalid probe latency")
            return dict(ok=ok, exit_ip=ip, latency_ms=latency)
        except Exception:
            return dict(ok=False, exit_ip="", latency_ms=0.0)

    def _switch(self, g: _Gateway, candidate: tuple[dict[str, Any], dict[str, Any]]) -> None:
        upstream, result = candidate
        previous = g.upstream
        g.upstream = upstream  # Relay get_upstream observes this atomic assignment.
        if previous is not None:
            self._owned[previous["client_id"]].due = self._clock() + DRAIN_SECONDS
            g.rotations += 1
        g.revision += 1
        g.status, g.last_error, g.failures = "running", "", 0
        g.attempts, g.next_create = 0, 0.0
        self._apply_probe(g, result)
        self._event(g.id, "info", "upstream_ready")

    async def rotate_gateway(self, gateway_id: str) -> dict[str, Any]:
        g = self._get(gateway_id)
        return await self._finish(self._rotate_locked(g, g.generation, g.revision))

    async def _rotate_locked(self, g: _Gateway, generation: int, revision: int) -> dict[str, Any]:
        async with g.lock:
            if self._alive(g, generation) and revision == g.revision:
                if g.relay is None:
                    await self._start(g, generation)
                else:
                    await self._rotate(g, generation)
            return self._snapshot(g)

    async def _rotate(self, g: _Gateway, generation: int) -> None:
        candidate = await self._candidate(g, g.config, generation)
        if candidate is not None:
            self._switch(g, candidate)

    def _apply_probe(self, g: _Gateway, result: dict[str, Any]) -> None:
        g.last_check = _timestamp()
        g.exit_ip, g.latency_ms = result["exit_ip"], result["latency_ms"]

    def _on_result(self, g: _Gateway, ok: bool) -> None:
        # Traffic is only a hint to schedule a full probe, never a rotation vote.
        if not ok and g.wanted and not g.deleting and not self._closing:
            g.next_check = min(g.next_check, self._clock() + BACKOFF_MIN)

    async def check_gateway(self, gateway_id: str) -> dict[str, Any]:
        g = self._get(gateway_id)
        return await self._finish(self._check_locked(g, g.generation))

    async def _check_locked(self, g: _Gateway, generation: int) -> dict[str, Any]:
        async with g.lock:
            if not self._alive(g, generation):
                return self._snapshot(g)
            if g.upstream is None:
                if g.config["auto_heal"]:
                    await self._start(g, generation)
                return self._snapshot(g)
            result = await self._probe_result(g.upstream)
            if not self._alive(g, generation):
                return self._snapshot(g)
            self._apply_probe(g, result)
            g.next_check = self._clock() + g.config["interval"]
            if result["ok"]:
                g.failures, g.last_error, g.status = 0, "", "running"
            else:
                g.failures += 1
                online = await self._control_available()
                self._failure(g, "upstream_probe_failed" if online else "network_offline",
                              "degraded" if online else "offline")
                if online and g.config["auto_heal"] and g.failures >= g.config["failures_before_rotate"]:
                    await self._rotate(g, generation)
            return self._snapshot(g)

    async def update_gateway(self, gateway_id: str, data: dict[str, Any]) -> dict[str, Any]:
        g = self._get(gateway_id)
        _validate(data, g.config)
        if data.get("enabled") is False:
            g.wanted = False
            g.generation += 1
        return await self._finish(self._update_locked(g, data, g.generation))

    async def _update_locked(self, g: _Gateway, data: dict[str, Any], generation: int) -> dict[str, Any]:
        async with g.lock:
            if g.deleting or self._closing or generation != g.generation:
                return self._snapshot(g)
            cfg = _validate(data, g.config)
            endpoint = self._reserve(cfg, g.id)
            try:
                await self._update(g, cfg, generation)
            except OSError:
                if generation == g.generation and not g.deleting and not self._closing:
                    g.wanted = g.config["enabled"]
                raise
            finally:
                self._reservations.pop(endpoint, None)
            return self._snapshot(g)

    async def _update(self, g: _Gateway, cfg: dict[str, Any], generation: int) -> None:
        previous, new_relay, candidate = g.config, None, None
        transport_changed = any(cfg.get(key) != previous.get(key)
                                for key in ("host", "port", "username", "password"))
        binding_changed = any(cfg[key] != previous[key] for key in ("mode", "country_code", "location_id"))
        if g.relay is not None and cfg["enabled"] and transport_changed:
            if (cfg["host"], cfg["port"]) == (previous["host"], previous["port"]):
                raise ValueError("stop gateway before changing credentials on the same port")
            try:
                new_relay = await self._bind(g, cfg)
            except Exception:
                g.last_error = "local_bind_failed"
                self._event(g.id, "warning", g.last_error)
                return
        try:
            if g.upstream is not None and cfg["enabled"] and binding_changed:
                candidate = await self._candidate(g, cfg, generation)
                if candidate is None:
                    return
            if g.deleting or self._closing or generation != g.generation:
                return
            g.config = cfg
            try:
                self._persist()
            except OSError:
                g.config = previous
                raise
            g.wanted = cfg["enabled"]
            if cfg.get("username") and cfg.get("password"):
                g.auth_missing = False
            if candidate is not None:
                self._switch(g, candidate)
                candidate = None
            if new_relay is not None:
                old_relay, g.relay, new_relay = g.relay, new_relay, None
                await old_relay.close()
            if not cfg["enabled"]:
                await self._stop(g)
            elif g.upstream is None:
                await self._start(g, generation)
            g.next_check = self._clock() + cfg["interval"]
            self._event(g.id, "info", "updated")
        finally:
            if new_relay is not None:
                await new_relay.close()
            if candidate is not None:
                await self._discard(candidate[0]["client_id"])

    async def stop_gateway(self, gateway_id: str) -> dict[str, Any]:
        g = self._get(gateway_id)
        g.wanted = False
        g.generation += 1
        return await self._finish(self._stop_locked(g))

    async def _stop_locked(self, g: _Gateway) -> dict[str, Any]:
        async with g.lock:
            previous_enabled = g.config["enabled"]
            g.config["enabled"] = False
            try:
                self._persist()
            except OSError:
                g.config["enabled"] = previous_enabled
                g.wanted = previous_enabled and not g.deleting and not self._closing
                g.last_error = "state_write_failed"
                self._event(g.id, "error", g.last_error)
                raise
            await self._stop(g)
            return self._snapshot(g)

    async def _stop(self, g: _Gateway) -> None:
        g.upstream = None
        if g.relay is not None:
            await g.relay.close()
            g.relay = None
        for cid, item in list(self._owned.items()):
            if item.gateway_id == g.id:
                item.due = 0.0
        await self._cleanup_due(gateway_id=g.id)
        g.status, g.exit_ip, g.latency_ms, g.failures = "stopped", "", 0.0, 0
        g.last_error = "owned_cleanup_pending" if any(o.gateway_id == g.id for o in self._owned.values()) else ""
        self._event(g.id, "info", "stopped")

    async def delete_gateway(self, gateway_id: str) -> dict[str, Any]:
        g = self._get(gateway_id)
        g.deleting, g.wanted = True, False
        g.generation += 1
        return await self._finish(self._delete_locked(g))

    async def _delete_locked(self, g: _Gateway) -> dict[str, Any]:
        async with g.lock:
            old_enabled = g.config["enabled"]
            g.config["enabled"] = False
            try:
                self._persist()
            except OSError:
                g.config["enabled"] = old_enabled
                g.wanted, g.deleting = old_enabled and not self._closing, False
                raise
            try:
                await self._stop(g)
            except OSError:
                g.deleting = False
                self._failure(g, "state_write_failed", "error")
                raise
            del self._gateways[g.id]
            try:
                self._persist()
            except OSError:
                self._gateways[g.id] = g
                g.deleting = False
                raise
            g.status = "deleted"
            self._event(g.id, "info", "deleted")
            return self._snapshot(g)

    async def _discard(self, client_id: str) -> None:
        self._owned[client_id].due = 0.0
        async with self._cleanup_lock:
            await self._remove_owned(client_id)

    async def _remove_owned(self, client_id: str) -> None:
        item = self._owned.get(client_id)
        if item is None:
            return
        try:
            response = await asyncio.to_thread(self.client.remove_proxy, client_id)
            if response is False or isinstance(response, dict) and (response.get("error") or response.get("ok") is False):
                raise RuntimeError("remote removal rejected")
        except Exception as exc:
            if _http_status(exc) not in (404, 410):
                item.attempts += 1
                item.due = self._clock() + _delay(item.attempts)
                self._event(item.gateway_id, "warning", _error_code("owned_cleanup_pending", exc))
                return
        del self._owned[client_id]
        try:
            self._persist()
        except OSError:
            # Keep the journal entry until disk catches up; removal is idempotent.
            self._owned[client_id] = item
            item.attempts += 1
            item.due = self._clock() + _delay(item.attempts)
            self._event(item.gateway_id, "error", "state_write_failed")
            raise

    async def _cleanup_due(self, *, gateway_id: str | None = None) -> None:
        async with self._cleanup_lock:
            for cid, item in list(self._owned.items()):
                if (item.due is not None and item.due <= self._clock()
                        and (gateway_id is None or item.gateway_id == gateway_id)):
                    await self._remove_owned(cid)

    async def _maintain(self) -> None:
        while not self._closing:
            try:
                await asyncio.wait_for(self._shutdown.wait(), 1)
            except asyncio.TimeoutError:
                pass
            if self._closing:
                break
            try:
                await self._cleanup_due()
                for g in list(self._gateways.values()):
                    if (g.wanted and not g.deleting and self._clock() >= g.next_check
                            and (g.maintenance is None or g.maintenance.done())):
                        g.next_check = self._clock() + g.config["interval"]
                        g.maintenance = self._spawn(self._check_locked(g, g.generation))
            except OSError:
                self._event("", "error", "state_write_failed")

    async def close(self) -> dict[str, Any]:
        """Preserve enabled configuration; wait for pending creates and clean owned IDs."""
        self._assert_loop(ready=False)
        if not self._initialized:
            self._closing = True
            self._shutdown.set()
            if self._tasks:
                await asyncio.gather(*list(self._tasks), return_exceptions=True)
            return dict(closed=True, pending_cleanup=len(self._owned))
        if self._close_task is None:
            self._closing = True
            self._shutdown.set()
            for g in self._gateways.values():
                g.wanted = False
                g.generation += 1
            self._close_task = asyncio.create_task(self._close())
        return await asyncio.shield(self._close_task)

    async def _close(self) -> dict[str, Any]:
        if self._worker is not None:
            # Do not cancel a to_thread cleanup; let it finish and journal the result.
            await self._worker
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
        for g in list(self._gateways.values()):
            async with g.lock:
                await self._stop(g)
        self._persist()
        return dict(closed=True, pending_cleanup=len(self._owned))
