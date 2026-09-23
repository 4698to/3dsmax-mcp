"""Multi-instance registry and per-session short leases for 3ds Max MCP.

Each configured 3ds Max instance is an exclusive resource: at most one MCP
session may hold it at a time. A session acquires an instance (optionally
waiting on a bounded FIFO queue), uses it through the shared `client` proxy,
then releases it — or the idle lease expires — so the instance becomes
available to the next waiter. New leases reset the scene by default for
multi-tenant isolation.

Instance sources (merged):

1. Constructor ``spec`` / ``MAXMCP_INSTANCES`` env — comma-separated
   ``host:port[:name]`` entries (pinned; always kept).

2. Config file ``max_instances.ini`` (see ``max_instances.ini.example``)::

       [instances]
       max1 = 192.168.139.45:8765

       [workspace]
       path = \\\\fileserver\\share\\3dsmax-mcp\\workspace

   Search order: ``MAXMCP_INSTANCES_FILE``, cwd / project-root
   ``max_instances.ini``, then ``%LOCALAPPDATA%\\3dsmax-mcp\\max_instances.ini``.
   These instance entries are pinned. Optional ``[workspace] path=`` (or env
   ``MAXMCP_WORKSPACE``) defines a share all Max hosts and Python can read/write;
   if omitted, there is no shared workspace.

3. Auto-discovery from the local registry file that each 3ds Max instance
   writes (JSONL). Path from ``MAXMCP_REGISTRY``, else
   ``%LOCALAPPDATA%\\3dsmax-mcp\\instances.jsonl``. Local-mode Max
   (``bindIP=127.0.0.1``) shows up here. Registry instances are merged with
   pinned ones and refreshed on a background watcher — they are *not*
   disabled just because an ini/env list exists.

4. Fallback (only when nothing is pinned and the registry is empty): a single
   ``127.0.0.1:8765`` instance with implicit session binding.

A background watcher thread applies the heartbeat TTL continuously: an instance
is marked offline and dropped from the managed list (its lock released) as soon
as its heartbeat expires, without waiting for the next tool call.

Lease / queue env knobs:

- ``MAXMCP_LOCK_TTL`` — idle lease seconds (default 180)
- ``MAXMCP_ACQUIRE_WAIT_SECONDS`` — max seconds to wait in FIFO queue (default 60)
- ``MAXMCP_ACQUIRE_QUEUE_MAX`` — max waiters before QUEUE_FULL (default 32)
- ``MAXMCP_RESET_ON_ACQUIRE`` — opt-in: reset Max scene on new lease (default false)
"""

from __future__ import annotations

import configparser
import json
import logging
import os
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .max_client import MaxClient, PROBE_SERIAL, _probe_named_pipe_unlocked

ENV_INSTANCES = "MAXMCP_INSTANCES"
ENV_INSTANCES_FILE = "MAXMCP_INSTANCES_FILE"
ENV_LOCK_TTL = "MAXMCP_LOCK_TTL"
ENV_ACQUIRE_WAIT = "MAXMCP_ACQUIRE_WAIT_SECONDS"
ENV_ACQUIRE_QUEUE_MAX = "MAXMCP_ACQUIRE_QUEUE_MAX"
ENV_RESET_ON_ACQUIRE = "MAXMCP_RESET_ON_ACQUIRE"
ENV_REGISTRY = "MAXMCP_REGISTRY"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
# Idle lease: no tool activity for this long -> auto-release (public multi-agent default).
DEFAULT_LOCK_TTL = 180
DEFAULT_ACQUIRE_WAIT_SECONDS = 60.0
DEFAULT_ACQUIRE_QUEUE_MAX = 32
DEFAULT_PURGE_INTERVAL = 15  # seconds between stale-lock sweep cycles
REGISTRY_HEARTBEAT_SECONDS = 30  # how often 3ds Max refreshes its entry
REGISTRY_TTL = 90  # seconds; an entry not refreshed within this is gone
REGISTRY_POLL_SECONDS = 5  # background poll interval for the registry file
INSTANCES_INI_NAME = "max_instances.ini"
# Must exceed Max idle poll (~1.5s) plus response write time on Max 2015.
PROBE_TIMEOUT = float(os.environ.get("MAXMCP_PROBE_TIMEOUT", "5.0"))
PROBE_CACHE_SECONDS = float(os.environ.get("MAXMCP_PROBE_CACHE", "5"))
# Background watcher probe cadence (list_instances still uses PROBE_CACHE_SECONDS).
PROBE_BACKGROUND_SECONDS = float(os.environ.get("MAXMCP_PROBE_BACKGROUND", "30"))

# Session identity: the FastMCP ServerSession object of the request currently
# being handled. Tools receive it through their injected Context (`ctx.session`)
# and the routing client proxy reads it via mcp.get_context().session. Because
# FastMCP sets/resets the request context inside the long-lived per-session task,
# the session object is always the correct one for the request being executed
# (an ASGI header + contextvar approach fails here: the tool runs in the task
# that was created on the first, session-less request, so later headers never
# reach it).


def _display(session: object) -> str:
    """Short human-readable tag for a session object in logs/messages."""
    return str(id(session) & 0xFFFFFFFF)  # stable-ish short id


class InstanceError(RuntimeError):
    """Base error for instance registry problems."""


class InstanceBusyError(InstanceError):
    """The requested instance is held by another session."""


class InstanceNotAcquiredError(InstanceError):
    """This session has not acquired any instance yet."""


class NoFreeInstanceError(InstanceError):
    """All configured instances are currently busy."""


class QueueFullError(InstanceError):
    """Acquire wait queue is at capacity (hard backpressure)."""


class AcquireWaitTimeoutError(InstanceError):
    """Timed out waiting for an idle instance."""


@dataclass
class _AcquireWaiter:
    """One FIFO wait-queue entry for a session blocked in acquire()."""

    session: object
    name: Optional[str]
    reset_scene: bool
    event: threading.Event
    enqueued_at: float
    position: int = 0
    result: Optional[MaxClient] = None
    error: Optional[BaseException] = None
    scene_reset: bool = False
    cancelled: bool = False


@dataclass
class IMaxInstance:
    """A single configured 3ds Max instance."""

    name: str
    host: str
    port: int
    max_version: Optional[int] = None  # e.g. 2022; None when unknown
    locked_by: Optional[object] = None  # MCP ServerSession currently holding it
    locked_at: Optional[float] = None  # time.monotonic() of acquisition
    pinned: bool = False  # from env/ini; kept even when not in local registry
    pid: Optional[int] = None
    pipe: Optional[str] = None
    online: Optional[bool] = None  # reachable via TCP and/or native
    tcp_online: Optional[bool] = None
    native_online: Optional[bool] = None
    online_error: Optional[str] = None
    online_checked_at: Optional[float] = None  # time.monotonic()


def _probe_tcp(host: str, port: int, timeout: float = PROBE_TIMEOUT) -> tuple[bool, str]:
    """Protocol-level reachability: connect, send ping, expect a JSON line.

    A bare TCP connect (no payload) makes Max Accept then ReadLine an empty
    request and spam the Listener. Always send a real ping line.
    Timeout must exceed Max's TCP idle poll interval (~1.5s) so Accept can run.
    """
    req = (
        json.dumps(
            {
                "command": "",
                "type": "ping",
                "requestId": "probe",
                "protocolVersion": 2,
            },
            ensure_ascii=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(req)
            buf = b""
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    break
                if not chunk:
                    break
                buf += chunk
                if b"\n" in buf:
                    break
            if not buf:
                return False, "connected but no response"
            if buf.startswith(b"\xef\xbb\xbf"):
                buf = buf[3:]
            text = buf.decode("utf-8", errors="replace").strip()
            if text.startswith("{") and ("success" in text or "pong" in text):
                return True, ""
            return False, f"unexpected response: {text[:120]}"
    except OSError as exc:
        return False, str(exc)


def _is_local_host(host: str) -> bool:
    return (host or "").strip().lower() in ("127.0.0.1", "localhost", "::1", "")


def _pipe_name_for_pid(pid: int) -> str:
    return fr"\\.\pipe\3dsmax-mcp-pid-{int(pid)}"


def _env_lock_ttl() -> float:
    raw = os.environ.get(ENV_LOCK_TTL)
    if raw is None:
        return float(DEFAULT_LOCK_TTL)
    try:
        return max(30.0, float(raw))
    except ValueError:
        return float(DEFAULT_LOCK_TTL)


def _env_acquire_wait_seconds() -> float:
    raw = os.environ.get(ENV_ACQUIRE_WAIT)
    if raw is None:
        return DEFAULT_ACQUIRE_WAIT_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_ACQUIRE_WAIT_SECONDS


def _env_acquire_queue_max() -> int:
    raw = os.environ.get(ENV_ACQUIRE_QUEUE_MAX)
    if raw is None:
        return DEFAULT_ACQUIRE_QUEUE_MAX
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_ACQUIRE_QUEUE_MAX


def _env_reset_on_acquire() -> bool:
    """Opt-in only: unset / empty means do not reset on acquire."""
    raw = os.environ.get(ENV_RESET_ON_ACQUIRE)
    if raw is None or not str(raw).strip():
        return False
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _retry_after_seconds(queue_depth: int, lock_ttl: float) -> float:
    """Suggested client backoff when acquire is rejected."""
    if queue_depth <= 0:
        return min(15.0, max(5.0, lock_ttl / 12.0))
    return min(60.0, max(5.0, 5.0 + queue_depth * 2.0))


def _default_registry_path() -> str:
    """Registry file path: MAXMCP_REGISTRY env var, else %LOCALAPPDATA%\\3dsmax-mcp\\instances.jsonl."""
    raw = os.environ.get(ENV_REGISTRY)
    if raw and raw.strip():
        return raw.strip()
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = os.path.expanduser("~")
    return os.path.join(base, "3dsmax-mcp", "instances.jsonl")


def _default_user_config_dir() -> str:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = os.path.expanduser("~")
    return os.path.join(base, "3dsmax-mcp")


def _instances_config_candidates() -> list[Path]:
    """Ordered search paths for max_instances.ini."""
    paths: list[Path] = []
    env_path = os.environ.get(ENV_INSTANCES_FILE, "").strip()
    if env_path:
        paths.append(Path(env_path))
    cwd = Path.cwd() / INSTANCES_INI_NAME
    paths.append(cwd)
    # Repo root when developing (maxmcp/ -> project root); package dir when installed.
    pkg_dir = Path(__file__).resolve().parent
    paths.append(pkg_dir.parent / INSTANCES_INI_NAME)
    paths.append(pkg_dir / INSTANCES_INI_NAME)
    paths.append(Path(_default_user_config_dir()) / INSTANCES_INI_NAME)
    # De-dupe while preserving order
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _parse_host_port(value: str, *, name: str) -> tuple[str, int]:
    """Parse ``host:port`` (IPv4 / hostname). Raises ValueError on bad input."""
    text = value.strip()
    if not text:
        raise ValueError(f"Empty host:port for instance {name!r}")
    # Split from the right so hostnames stay intact; IPv6 is not supported here.
    if ":" not in text:
        raise ValueError(
            f"Invalid instance endpoint {text!r} for {name!r}: expected host:port"
        )
    host, port_str = text.rsplit(":", 1)
    host = host.strip()
    if not host:
        raise ValueError(f"Missing host for instance {name!r}")
    try:
        port = int(port_str.strip())
    except ValueError as exc:
        raise ValueError(f"Invalid port {port_str!r} for instance {name!r}") from exc
    if not (1 <= port <= 65535):
        raise ValueError(f"Port out of range for instance {name!r}: {port}")
    return host, port


def _load_instances_from_ini(path: Path) -> list[tuple[str, int, str]]:
    """Read ``[instances] name = host:port`` entries from an ini file.

    Returns [] when the file has no usable entries. Raises ValueError on
    malformed entries so misconfiguration fails loudly at startup.
    """
    parser = configparser.ConfigParser()
    parser.optionxform = str  # preserve instance name casing (max1 / MaxA)
    try:
        read_ok = parser.read(path, encoding="utf-8")
    except (OSError, configparser.Error, UnicodeError) as exc:
        raise ValueError(f"Could not read instances config {path}: {exc}") from exc
    if not read_ok:
        return []
    if not parser.has_section("instances"):
        return []
    entries: list[tuple[str, int, str]] = []
    for name, raw in parser.items("instances"):
        host, port = _parse_host_port(raw, name=name)
        entries.append((host, port, name))
    return entries


def _discover_instances_file() -> tuple[Optional[Path], list[tuple[str, int, str]]]:
    """Return (path, entries) for the first max_instances.ini that has entries."""
    for path in _instances_config_candidates():
        if not path.is_file():
            continue
        entries = _load_instances_from_ini(path)
        if entries:
            return path, entries
        logging.info(
            "Instances config %s has no [instances] entries; trying next source",
            path,
        )
    return None, []


class InstanceManager:
    """Thread-safe registry of 3ds Max instances with per-session short leases."""

    def __init__(
        self,
        spec: Optional[str] = None,
        lock_ttl: Optional[float] = None,
        *,
        acquire_wait_seconds: Optional[float] = None,
        acquire_queue_max: Optional[int] = None,
        reset_on_acquire: Optional[bool] = None,
    ):
        self._lock = threading.RLock()
        self._lock_ttl = lock_ttl if lock_ttl is not None else _env_lock_ttl()
        self._acquire_wait_seconds = (
            acquire_wait_seconds
            if acquire_wait_seconds is not None
            else _env_acquire_wait_seconds()
        )
        self._acquire_queue_max = (
            acquire_queue_max
            if acquire_queue_max is not None
            else _env_acquire_queue_max()
        )
        self._reset_on_acquire = (
            reset_on_acquire
            if reset_on_acquire is not None
            else _env_reset_on_acquire()
        )
        self._instances: list[IMaxInstance] = []
        self._clients: dict[str, MaxClient] = {}
        self._by_session: dict[object, str] = {}  # session object -> instance name
        self._wait_queue: list[_AcquireWaiter] = []
        self._auto_bind = False  # single-instance fallback binds implicitly
        env_spec = os.environ.get(ENV_INSTANCES)
        # Explicit env/constructor OR a populated max_instances.ini -> never
        # overwrite from the local auto-discovery registry.
        file_path, file_entries = (None, [])
        if not (spec and spec.strip()) and not (env_spec and env_spec.strip()):
            file_path, file_entries = _discover_instances_file()
        self._explicit = bool(spec) or bool(env_spec) or bool(file_entries)
        self._instances_file = file_path
        self._load(
            spec if spec is not None else env_spec,
            file_entries=file_entries,
            file_path=file_path,
        )
        self.start_watching()
        self._start_stale_purger()
        logging.info(
            "InstanceManager lease policy: idle_ttl=%ss acquire_wait=%ss "
            "queue_max=%s reset_on_acquire=%s",
            self._lock_ttl,
            self._acquire_wait_seconds,
            self._acquire_queue_max,
            self._reset_on_acquire,
        )

    # ------------------------------------------------------------------ #
    # Loading
    # ------------------------------------------------------------------ #

    def _build(
        self,
        entries: list[tuple[str, int, str, Optional[int]]],
        *,
        pinned: bool = False,
    ) -> None:
        """Register parsed (host, port, name, max_version) entries. Caller holds self._lock."""
        for host, port, name, max_version in entries:
            if any(i.name == name for i in self._instances):
                raise ValueError(f"Duplicate instance name {name!r}")
            self._instances.append(
                IMaxInstance(
                    name=name,
                    host=host,
                    port=port,
                    max_version=max_version,
                    pinned=pinned,
                )
            )
            self._clients[name] = MaxClient(host=host, port=port)
        self._attach_registry_identity_locked()
        logging.info(
            "InstanceManager loaded %d 3ds Max instance(s): %s",
            len(self._instances),
            ", ".join(
                f"{i.name}={i.host}:{i.port}"
                + (" [pinned]" if i.pinned else "")
                + (f" (3ds Max {i.max_version})" if i.max_version else "")
                for i in self._instances
            ),
        )

    def _read_registry_records_raw(self) -> list[dict[str, Any]]:
        """Every parseable line of the registry file, fresh or stale.

        Version lookup deliberately ignores the heartbeat TTL: the last version
        a Max-side script recorded stays useful even after the instance went
        idle. Never raises; on any failure returns [].
        """
        path = _default_registry_path()
        try:
            if not os.path.exists(path):
                return []
            found: list[dict[str, Any]] = []
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        found.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            return found
        except OSError as exc:
            logging.warning("Could not read registry file %s: %s", path, exc)
            return []

    def _registry_version_by_port(self) -> dict[int, int]:
        """Latest recorded maxVersion per port from the registry file.

        Lets explicitly-configured instances (MAXMCP_INSTANCES / ini) still
        report the 3ds Max version written by the running Max-side script.
        Never raises; returns {} on any failure.
        """
        versions: dict[int, int] = {}
        try:
            for rec in self._read_registry_records_raw():
                port = rec.get("port")
                ver = rec.get("maxVersion")
                if isinstance(port, int) and isinstance(ver, int):
                    versions[port] = ver  # later lines win (most recent)
        except Exception:
            return {}
        return versions

    def _unique_discovered_name(self, base: str, seen: set[str]) -> str:
        name = base
        n = 1
        while name in seen:
            name = f"{base}-{n}"
            n += 1
        return name

    def _connect_host_from_registry(self, rec: dict[str, Any]) -> str:
        """Pick a TCP connect address from a registry record.

        Local-mode Max writes bindIP 127.0.0.1; remote mode may write 0.0.0.0
        (listen-all), which is not a valid connect target — use loopback.
        """
        host = rec.get("host")
        if isinstance(host, str):
            host = host.strip()
        if not host or host in ("0.0.0.0", "::", "*"):
            return DEFAULT_HOST
        return host

    def _merge_registry_locked(self) -> None:
        """Add/update/drop non-pinned instances from the local registry.

        Pinned entries (env / max_instances.ini) are always kept. Caller holds
        self._lock.
        """
        discovered = self._discover_registry_instances()
        pinned = [i for i in self._instances if i.pinned]
        previous_discovered = {i.name: i for i in self._instances if not i.pinned}

        new_instances: list[IMaxInstance] = list(pinned)
        seen_names = {i.name for i in pinned}
        seen_endpoints = {(i.host, i.port) for i in pinned}

        for host, port, name, max_version in discovered:
            if (host, port) in seen_endpoints:
                continue
            name = self._unique_discovered_name(name, seen_names)
            old = previous_discovered.pop(name, None)
            if old is not None and old.host == host and old.port == port:
                if max_version and old.max_version != max_version:
                    old.max_version = max_version
                new_instances.append(old)
            else:
                if old is not None and old.locked_by:
                    self._by_session.pop(old.locked_by, None)
                new_instances.append(
                    IMaxInstance(
                        name=name,
                        host=host,
                        port=port,
                        max_version=max_version,
                        pinned=False,
                    )
                )
                self._clients[name] = MaxClient(host=host, port=port)
            seen_names.add(name)
            seen_endpoints.add((host, port))

        for old in previous_discovered.values():
            if old.locked_by:
                self._by_session.pop(old.locked_by, None)
                logging.warning(
                    "Instance %s disappeared from registry; released its lock",
                    old.name,
                )
            self._clients.pop(old.name, None)

        self._instances = new_instances
        self._attach_registry_identity_locked()
        # Single instance (pinned or discovered) can auto-bind; multiple need acquire.
        self._auto_bind = len(self._instances) == 1

    def _load(
        self,
        spec: Optional[str],
        *,
        file_entries: Optional[list[tuple[str, int, str]]] = None,
        file_path: Optional[Path] = None,
    ) -> None:
        with self._lock:
            if spec and spec.strip():
                versions = self._registry_version_by_port()
                entries: list[tuple[str, int, str, Optional[int]]] = []
                for idx, entry in enumerate(e.strip() for e in spec.split(",") if e.strip()):
                    parts = entry.split(":")
                    if len(parts) == 2:
                        host, port_str, name = parts[0], parts[1], f"max{idx + 1}"
                    elif len(parts) == 3:
                        host, port_str, name = parts
                    else:
                        raise ValueError(
                            f"Invalid MAXMCP_INSTANCES entry {entry!r}: expected host:port[:name]"
                        )
                    try:
                        port = int(port_str)
                    except ValueError:
                        raise ValueError(f"Invalid port {port_str!r} for instance {name!r}")
                    entries.append((host, port, name, versions.get(port)))
                self._build(entries, pinned=True)
                self._merge_registry_locked()
                return

            if file_entries:
                versions = self._registry_version_by_port()
                built = [
                    (host, port, name, versions.get(port))
                    for host, port, name in file_entries
                ]
                logging.info(
                    "Loading 3ds Max instances from config file: %s",
                    file_path or INSTANCES_INI_NAME,
                )
                self._build(built, pinned=True)
                self._merge_registry_locked()
                return

            discovered = self._discover_registry_instances()
            if discovered:
                self._build(discovered, pinned=False)
                self._auto_bind = len(discovered) == 1
                logging.info(
                    "Auto-discovered %d 3ds Max instance(s) from registry",
                    len(discovered),
                )
            else:
                # Fallback single instance: still report the version the Max-side
                # script last recorded for this port (registry may be stale).
                versions = self._registry_version_by_port()
                self._build(
                    [(DEFAULT_HOST, DEFAULT_PORT, "max1", versions.get(DEFAULT_PORT))],
                    pinned=False,
                )
                self._auto_bind = True

    def _find(self, name: str) -> Optional[IMaxInstance]:
        return next((i for i in self._instances if i.name == name), None)

    def _read_registry_records(self) -> list[dict[str, Any]]:
        """Fresh entries (heartbeat within REGISTRY_TTL) from the registry file.

        Never raises; on any failure returns [] so the caller can fall back.
        """
        path = _default_registry_path()
        try:
            if not os.path.exists(path):
                return []
            now = time.time()
            found: list[dict[str, Any]] = []
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    last_seen = rec.get("lastSeen")
                    if not isinstance(last_seen, (int, float)):
                        continue
                    if now - float(last_seen) > REGISTRY_TTL:
                        continue  # heartbeat stale -> 3ds Max instance gone
                    found.append(rec)
            return found
        except OSError as exc:
            logging.warning("Could not read registry file %s: %s", path, exc)
            return []

    def _discover_registry_instances(self) -> list[tuple[str, int, str, Optional[int]]]:
        """Read auto-registered instances from the JSONL registry file.

        Returns fresh entries (heartbeat within REGISTRY_TTL) as
        (host, port, name, max_version). Never raises; on any failure returns
        [] so the caller can fall back.
        """
        found: list[tuple[str, int, str, Optional[int]]] = []
        for rec in self._read_registry_records():
            port = rec.get("port")
            if not isinstance(port, int):
                continue
            name = rec.get("name") or f"max-{port}"
            ver = rec.get("maxVersion")
            host = self._connect_host_from_registry(rec)
            found.append((host, port, name, ver if isinstance(ver, int) else None))
        return found

    def _attach_registry_identity_locked(self) -> None:
        """Copy pid/pipe from the local heartbeat registry onto matching instances.

        Named pipes only exist on the Max host; remote TCP entries stay pid-less.
        Caller holds self._lock.
        """
        for rec in self._read_registry_records():
            rec_port = rec.get("port")
            rec_pid = rec.get("pid")
            if not isinstance(rec_port, int) or not isinstance(rec_pid, int) or rec_pid <= 0:
                continue
            rec_host = self._connect_host_from_registry(rec)
            rec_pipe = rec.get("pipe")
            pipe = rec_pipe if isinstance(rec_pipe, str) and rec_pipe else _pipe_name_for_pid(rec_pid)
            for inst in self._instances:
                if inst.port != rec_port:
                    continue
                same = inst.host == rec_host or (
                    _is_local_host(inst.host) and _is_local_host(rec_host)
                )
                if not same:
                    continue
                inst.pid = rec_pid
                inst.pipe = pipe

    def refresh_registry(self) -> None:
        """Re-sync non-pinned instances from the auto-discovery registry file.

        Pinned env/ini instances are preserved. Local-mode Max processes that
        register into instances.jsonl are added/removed as their heartbeats
        appear or expire.
        """
        with self._lock:
            # No pinned config and no live list yet beyond empty: keep legacy
            # "replace entire list from registry" semantics via merge (pinned=[]).
            self._merge_registry_locked()

    # ------------------------------------------------------------------ #
    # Registry watching
    # ------------------------------------------------------------------ #

    def start_watching(self) -> None:
        """Daemon thread: poll the registry file and announce new 3ds Max
        instances on the console. Always runs so local-mode Max is discovered
        even when max_instances.ini lists remote hosts."""
        with self._lock:
            if getattr(self, "_watcher", None) is not None:
                return
            self._watcher = threading.Thread(
                target=self._watch_loop, daemon=True, name="registry-watch"
            )
            self._watcher.start()

    def _watch_loop(self) -> None:
        """Poll every REGISTRY_POLL_SECONDS: announce newly joined instances and
        mark instances offline once their heartbeat goes stale (no fresh registry
        record within REGISTRY_TTL)."""
        known: dict[str, int] = {}  # instance name -> last seen port
        last_probe_at = 0.0
        # Startup snapshot: instances already running before the Python server
        # started are announced too (otherwise a Max-first start would be silent).
        for rec in self._read_registry_records():
            name = rec.get("name") or f"max-{rec.get('port')}"
            known[name] = rec.get("port")
            logging.info("3ds Max instance already running: %s", self._describe(rec))
        # One startup probe so list_instances has online flags quickly.
        try:
            self.probe_all(force=True)
            last_probe_at = time.monotonic()
        except Exception:
            pass
        while True:
            time.sleep(REGISTRY_POLL_SECONDS)
            try:
                records = self._read_registry_records()
            except Exception:
                continue  # keep polling; _read_registry_records already guards
            seen: set[str] = set()
            for rec in records:
                name = rec.get("name") or f"max-{rec.get('port')}"
                port = rec.get("port")
                seen.add(name)
                old_port = known.get(name)
                if old_port is None:
                    self._announce_join(rec)
                elif old_port != port:
                    logging.info(
                        "3ds Max instance %s restarted: port %s -> %s",
                        name,
                        old_port,
                        port,
                    )
                known[name] = port
            # Instances whose heartbeat expired are considered offline: drop
            # them from the known set so a later re-join is announced again,
            # and refresh the managed list so their locks are released and they
            # no longer show up in list_instances/acquire. No need to wait for
            # the next tool call.
            gone = [n for n in known if n not in seen]
            for name in gone:
                logging.warning(
                    "3ds Max instance %s stopped sending heartbeats (last seen "
                    "port %s); considered offline",
                    name,
                    known[name],
                )
                known.pop(name, None)
            if gone:
                self.refresh_registry()
            # Sparse background reachability probe (default 30s) so Listener
            # is not flooded with ping traffic every registry poll.
            now = time.monotonic()
            if now - last_probe_at >= PROBE_BACKGROUND_SECONDS:
                try:
                    self.probe_all(force=True)
                    last_probe_at = now
                except Exception:
                    continue

    def _describe(self, rec: dict[str, Any]) -> str:
        """Human-readable one-liner for a registry record."""
        name = rec.get("name") or f"max-{rec.get('port')}"
        port = rec.get("port")
        host = self._connect_host_from_registry(rec)
        details = []
        if rec.get("pid") is not None:
            details.append(f"pid={rec.get('pid')}")
        if rec.get("maxVersion") is not None:
            details.append(f"3ds Max {rec.get('maxVersion')}")
        suffix = f" ({', '.join(details)})" if details else ""
        return f"{name} at {host}:{port}{suffix}"

    def _announce_join(self, rec: dict[str, Any]) -> None:
        """Log a banner for a freshly discovered instance and fold it into
        the managed instance list right away (so list_instances/acquire see
        it with its version without waiting for the next explicit refresh)."""
        logging.info("New 3ds Max instance joined: %s", self._describe(rec))
        self.refresh_registry()

    # ------------------------------------------------------------------ #
    # Lock lifecycle
    # ------------------------------------------------------------------ #

    @property
    def lock_ttl(self) -> float:
        return self._lock_ttl

    @property
    def acquire_wait_seconds(self) -> float:
        return self._acquire_wait_seconds

    @property
    def acquire_queue_max(self) -> int:
        return self._acquire_queue_max

    @property
    def reset_on_acquire(self) -> bool:
        return self._reset_on_acquire

    def queue_depth(self) -> int:
        with self._lock:
            return len(self._wait_queue)

    def _hide_agent_banner(self, name: str) -> None:
        """Best-effort clear the Max Agent HUD after a lease ends (never raises)."""
        client = self._clients.get(name)
        if client is None:
            return
        try:
            client.send_command("MCP_SceneManage.hideAgentBanner()")
        except Exception as exc:  # noqa: BLE001
            logging.warning("hide agent banner on %s failed: %s", name, exc)

    def _purge_stale_locks(self) -> list[str]:
        """Release locks held idle longer than the TTL (abandoned sessions).

        Returns instance names that were freed so the caller can hide banners
        **outside** the manager lock (send_command must not run under the lock).
        """
        now = time.monotonic()
        stale_names: list[str] = []
        for inst in self._instances:
            if inst.locked_by and inst.locked_at and (now - inst.locked_at) > self._lock_ttl:
                logging.warning(
                    "Instance %s idle lease held by session %s exceeded TTL; releasing",
                    inst.name,
                    _display(inst.locked_by),
                )
                self._by_session.pop(inst.locked_by, None)
                inst.locked_by = None
                inst.locked_at = None
                stale_names.append(inst.name)
        if stale_names:
            self._dispatch_waiters_locked()
        return stale_names

    def _start_stale_purger(self) -> None:
        """Daemon thread: sweep expired locks every DEFAULT_PURGE_INTERVAL.

        Runs regardless of explicit/registry config so locks held by sessions
        that ended without calling release_instance are freed promptly (TTL
        ticks from the last activity, see get_for_session's renewal).
        """
        with self._lock:
            if getattr(self, "_purger", None) is not None:
                return
            self._purger = threading.Thread(
                target=self._purge_loop, daemon=True, name="lock-purge"
            )
            self._purger.start()

    def _purge_loop(self) -> None:
        while True:
            time.sleep(DEFAULT_PURGE_INTERVAL)
            try:
                with self._lock:
                    stale = self._purge_stale_locks()
                for name in stale:
                    self._hide_agent_banner(name)
            except Exception:  # keep sweeping on transient errors
                continue

    def _pick_idle_locked(self, name: Optional[str]) -> IMaxInstance:
        """Return an idle instance or raise. Caller holds self._lock."""
        if not self._instances:
            raise NoFreeInstanceError(
                "No 3ds Max instances discovered. Make sure the 3ds Max MCP "
                "server script is running in at least one 3ds Max instance."
            )

        if name:
            inst = self._find(name)
            if inst is None:
                raise InstanceError(
                    f"Unknown instance {name!r}. Configured: "
                    f"{[i.name for i in self._instances]}"
                )
            if inst.locked_by:
                raise InstanceBusyError(
                    f"Instance {name!r} is busy: held by another session "
                    f"({_display(inst.locked_by)})"
                )
            if inst.online is False:
                raise InstanceError(
                    f"Instance {name!r} is offline ({inst.host}:{inst.port})"
                    + (f": {inst.online_error}" if inst.online_error else "")
                )
            return inst

        # Prefer reachable idle instances; fall back to unknown (not yet probed).
        inst = next(
            (i for i in self._instances if not i.locked_by and i.online is True),
            None,
        )
        if inst is None:
            inst = next(
                (
                    i
                    for i in self._instances
                    if not i.locked_by and i.online is not False
                ),
                None,
            )
        if inst is None:
            raise NoFreeInstanceError(
                "No online idle 3ds Max instance available: "
                + ", ".join(
                    (
                        f"{i.name}({'busy' if i.locked_by else 'offline' if i.online is False else 'idle'})"
                        for i in self._instances
                    )
                )
            )
        return inst

    def _grant_locked(
        self, session: object, inst: IMaxInstance
    ) -> MaxClient:
        """Bind session to inst. Caller holds self._lock."""
        inst.locked_by = session
        inst.locked_at = time.monotonic()
        self._by_session[session] = inst.name
        logging.info(
            "Session %s acquired instance %s (%s:%s)",
            _display(session),
            inst.name,
            inst.host,
            inst.port,
        )
        return self._clients[inst.name]

    def _reset_scene(self, client: MaxClient) -> str:
        """Save current scene, then clear it (user-opt-in isolation).

        Always saves via ``MCP_SceneManage.saveScene`` first (handles unsaved
        scenes under ``%TEMP%\\3dsmax-mcp``), then resets.
        Returns the save result path / message.
        """
        save_raw = client.send_command("MCP_SceneManage.saveScene()")
        save_text = str(save_raw.get("result", "")).strip().strip('"')
        if save_text.startswith("ERROR"):
            raise InstanceError(f"Save before reset failed: {save_text}")
        if getattr(client, "native_available", False):
            client.send_command(
                json.dumps({"action": "reset"}),
                cmd_type="native:manage_scene",
            )
        else:
            client.send_command("MCP_SceneManage.resetScene()")
        return save_text

    def _busy_summary_locked(self) -> str:
        return ", ".join(
            (
                f"{i.name}({'busy' if i.locked_by else 'offline' if i.online is False else 'idle'})"
                for i in self._instances
            )
        )

    def _dispatch_waiters_locked(self) -> None:
        """Grant idle instances to FIFO waiters. Caller holds self._lock."""
        if not self._wait_queue:
            return
        remaining: list[_AcquireWaiter] = []
        for waiter in self._wait_queue:
            if waiter.cancelled:
                continue
            try:
                inst = self._pick_idle_locked(waiter.name)
            except InstanceBusyError:
                remaining.append(waiter)
                continue
            except NoFreeInstanceError:
                remaining.append(waiter)
                # Later waiters wanting "any" also cannot proceed; named ones
                # for other instances might, so keep scanning.
                continue
            except InstanceError as exc:
                waiter.error = exc
                waiter.event.set()
                continue
            waiter.result = self._grant_locked(waiter.session, inst)
            waiter.scene_reset = waiter.reset_scene
            waiter.event.set()
        self._wait_queue = remaining
        for idx, waiter in enumerate(self._wait_queue, start=1):
            waiter.position = idx

    def _cancel_waiters_locked(self, session: object) -> None:
        """Remove and wake any wait-queue entries for session."""
        kept: list[_AcquireWaiter] = []
        for waiter in self._wait_queue:
            if waiter.session is session:
                waiter.cancelled = True
                waiter.error = InstanceError("Acquire wait cancelled (session cleanup)")
                waiter.event.set()
            else:
                kept.append(waiter)
        self._wait_queue = kept
        for idx, waiter in enumerate(self._wait_queue, start=1):
            waiter.position = idx

    def _try_grant_now(
        self,
        session: object,
        name: Optional[str],
        *,
        reset_scene: bool,
    ) -> tuple[Optional[MaxClient], bool]:
        """Immediate acquire attempt. Returns (client, scene_reset_pending)."""
        hide_names: list[str] = []
        result: tuple[Optional[MaxClient], bool] = (None, False)
        with self._lock:
            self.refresh_registry()
            hide_names.extend(self._purge_stale_locks())
            held = self._by_session.get(session)
            if held is not None:
                if name is None or name == held:
                    result = (self._clients[held], False)
                else:
                    self._release_locked(session, held)
                    self._dispatch_waiters_locked()
                    hide_names.append(held)
                    try:
                        inst = self._pick_idle_locked(name)
                    except (InstanceBusyError, NoFreeInstanceError):
                        result = (None, False)
                    else:
                        client = self._grant_locked(session, inst)
                        result = (client, reset_scene)
            else:
                try:
                    inst = self._pick_idle_locked(name)
                except (InstanceBusyError, NoFreeInstanceError):
                    result = (None, False)
                else:
                    client = self._grant_locked(session, inst)
                    result = (client, reset_scene)
        for n in hide_names:
            self._hide_agent_banner(n)
        return result

    def acquire(
        self,
        session: object,
        name: Optional[str] = None,
        *,
        wait: bool = True,
        reset_scene: Optional[bool] = None,
    ) -> MaxClient:
        """Bind session to a free instance and return its client.

        When no instance is idle and ``wait`` is true, enqueues the session on
        a bounded FIFO wait list for up to ``acquire_wait_seconds``. Raises
        QueueFullError / AcquireWaitTimeoutError / NoFreeInstanceError /
        InstanceBusyError on failure.

        Idempotent: a session that already holds an instance keeps it. When a
        specific name is given and the session holds a different instance, the
        session switches: its current instance is released first, then the
        named one is acquired (if free).
        """
        client, _meta = self.acquire_with_meta(
            session, name, wait=wait, reset_scene=reset_scene
        )
        return client

    def acquire_with_meta(
        self,
        session: object,
        name: Optional[str] = None,
        *,
        wait: bool = True,
        reset_scene: Optional[bool] = None,
    ) -> tuple[MaxClient, dict[str, Any]]:
        """Like acquire(), also returning lease metadata for tool responses."""
        if session is None:
            raise InstanceError("No MCP session available for acquisition")

        do_reset = self._reset_on_acquire if reset_scene is None else bool(reset_scene)
        started = time.monotonic()
        wait_seconds = self._acquire_wait_seconds if wait else 0.0
        queue_position: Optional[int] = None

        client, needs_reset = self._try_grant_now(session, name, reset_scene=do_reset)
        if client is not None:
            scene_reset = False
            saved_before_reset = None
            if needs_reset:
                try:
                    saved_before_reset = self._reset_scene(client)
                    scene_reset = True
                except Exception:
                    logging.exception(
                        "Scene reset failed after acquire for session %s; releasing",
                        _display(session),
                    )
                    with self._lock:
                        held = self._by_session.get(session)
                        if held:
                            self._release_locked(session, held)
                            self._dispatch_waiters_locked()
                    raise InstanceError(
                        "Acquired instance but scene reset failed; lease released. Retry."
                    )
            return client, {
                "waited_seconds": round(time.monotonic() - started, 2),
                "queue_position": None,
                "lease_idle_seconds": self._lock_ttl,
                "scene_reset": scene_reset,
                "saved_before_reset": saved_before_reset,
            }

        # Named offline / unknown already raised inside _pick when no busy case;
        # _try_grant_now swallows only Busy/NoFree. Re-check hard errors now.
        with self._lock:
            if name:
                inst = self._find(name)
                if inst is None:
                    raise InstanceError(
                        f"Unknown instance {name!r}. Configured: "
                        f"{[i.name for i in self._instances]}"
                    )
                if inst.online is False:
                    raise InstanceError(
                        f"Instance {name!r} is offline ({inst.host}:{inst.port})"
                        + (f": {inst.online_error}" if inst.online_error else "")
                    )
            if not self._instances:
                raise NoFreeInstanceError(
                    "No 3ds Max instances discovered. Make sure the 3ds Max MCP "
                    "server script is running in at least one 3ds Max instance."
                )
            if not wait or wait_seconds <= 0:
                if name:
                    raise InstanceBusyError(
                        f"Instance {name!r} is busy: held by another session "
                        f"({_display(self._find(name).locked_by) if self._find(name) else '?'})"
                    )
                raise NoFreeInstanceError(
                    "No online idle 3ds Max instance available: "
                    + self._busy_summary_locked()
                )
            if len(self._wait_queue) >= self._acquire_queue_max:
                raise QueueFullError(
                    f"Acquire wait queue is full ({self._acquire_queue_max}). "
                    "Retry later."
                )
            # One wait slot per session.
            self._cancel_waiters_locked(session)
            waiter = _AcquireWaiter(
                session=session,
                name=name,
                reset_scene=do_reset,
                event=threading.Event(),
                enqueued_at=time.monotonic(),
                position=len(self._wait_queue) + 1,
            )
            self._wait_queue.append(waiter)
            queue_position = waiter.position
            logging.info(
                "Session %s queued for acquire (position=%s name=%r)",
                _display(session),
                queue_position,
                name,
            )

        # Block outside the manager lock so releasers can progress.
        remaining = wait_seconds
        while remaining > 0:
            if waiter.event.wait(timeout=min(remaining, 0.5)):
                break
            remaining = wait_seconds - (time.monotonic() - started)

        with self._lock:
            if waiter in self._wait_queue:
                self._wait_queue.remove(waiter)
                for idx, w in enumerate(self._wait_queue, start=1):
                    w.position = idx
            if waiter.result is not None:
                client = waiter.result
                needs_reset = waiter.scene_reset
            elif waiter.error is not None and not isinstance(
                waiter.error, (InstanceBusyError, NoFreeInstanceError)
            ):
                raise waiter.error
            else:
                raise AcquireWaitTimeoutError(
                    f"Timed out after {wait_seconds:.0f}s waiting for an idle "
                    f"3ds Max instance (queued at position {queue_position}). "
                    + self._busy_summary_locked()
                )

        scene_reset = False
        saved_before_reset = None
        if needs_reset:
            try:
                saved_before_reset = self._reset_scene(client)
                scene_reset = True
            except Exception:
                logging.exception(
                    "Scene reset failed after queued acquire for session %s; releasing",
                    _display(session),
                )
                with self._lock:
                    held = self._by_session.get(session)
                    if held:
                        self._release_locked(session, held)
                        self._dispatch_waiters_locked()
                raise InstanceError(
                    "Acquired instance but scene reset failed; lease released. Retry."
                )

        return client, {
            "waited_seconds": round(time.monotonic() - started, 2),
            "queue_position": queue_position,
            "lease_idle_seconds": self._lock_ttl,
            "scene_reset": scene_reset,
            "saved_before_reset": saved_before_reset,
        }

    def _release_locked(self, session: object, name: str) -> None:
        """Clear the lock for `name` held by session. Caller holds self._lock."""
        inst = self._find(name)
        if inst is not None and inst.locked_by == session:
            inst.locked_by = None
            inst.locked_at = None
        self._by_session.pop(session, None)

    def get_for_session(self, session: object) -> MaxClient:
        """Return the client of the instance the session holds.

        In single-instance fallback mode a free instance is bound implicitly.
        Otherwise the session must have called acquire_instance first.
        """
        with self._lock:
            if session is None:
                raise InstanceNotAcquiredError(
                    "No MCP session context available for this request"
                )
            held = self._by_session.get(session)
            if held is None and self._auto_bind and len(self._instances) == 1:
                # Auto-bind must not block on the public wait queue.
                pass
            elif held is None:
                raise InstanceNotAcquiredError(
                    "This session has not acquired any 3ds Max instance. "
                    "Call acquire_instance first, and release_instance when done."
                )
            else:
                inst = self._find(held)
                if inst is not None and inst.locked_by != session:
                    # Lock was lost (e.g. stale-purged while this session reused the id).
                    pass
                elif inst is not None:
                    # Activity renewal: every request from a holding session pushes the
                    # idle-lease deadline forward.
                    inst.locked_at = time.monotonic()
                    return self._clients[held]
                else:
                    raise InstanceNotAcquiredError(
                        "This session has not acquired any 3ds Max instance. "
                        "Call acquire_instance first, and release_instance when done."
                    )

        # Outside lock: implicit re-bind without waiting / without scene reset.
        return self.acquire(session, wait=False, reset_scene=False)

    def release(self, session: object, name: Optional[str] = None) -> dict[str, Any]:
        """Explicitly free the instance held by session.

        Only the holder may release. Raises InstanceNotAcquiredError when this
        session holds nothing, InstanceError on a name mismatch.
        """
        with self._lock:
            held = self._by_session.get(session)
            if held is None:
                raise InstanceNotAcquiredError("This session holds no instance to release.")
            if name and name != held:
                raise InstanceError(
                    f"Session holds instance {held!r}, not {name!r}."
                )
            self._release_locked(session, held)
            self._dispatch_waiters_locked()
            logging.info("Session %s released instance %s", _display(session), held)
        # Outside lock: clear Agent HUD so it does not stick after the lease.
        self._hide_agent_banner(held)
        return {"released": held, "idle": True}

    def release_all_for_session(self, session: object) -> None:
        """Defensive cleanup: cancel waits and free any lock (never raises)."""
        if session is None:
            return
        held = None
        with self._lock:
            self._cancel_waiters_locked(session)
            held = self._by_session.get(session)
            if held:
                self._release_locked(session, held)
                logging.info(
                    "Session %s cleanup released instance %s",
                    _display(session),
                    held,
                )
            self._dispatch_waiters_locked()
        if held:
            self._hide_agent_banner(held)

    def cancel_acquire_wait(self, session: object) -> None:
        """Cancel a pending acquire wait for this session (never raises)."""
        if session is None:
            return
        with self._lock:
            self._cancel_waiters_locked(session)

    def reject_payload(
        self,
        code: str,
        error: str,
        *,
        retryable: bool = True,
    ) -> dict[str, Any]:
        """Structured acquire failure for MCP tool responses."""
        with self._lock:
            depth = len(self._wait_queue)
        return {
            "acquired": False,
            "code": code,
            "error": error,
            "retryable": retryable,
            "retry_after_seconds": _retry_after_seconds(depth, self._lock_ttl),
            "queue_depth": depth,
            "queue_max": self._acquire_queue_max,
            "lease_idle_seconds": self._lock_ttl,
            "acquire_wait_seconds": self._acquire_wait_seconds,
        }

    # ------------------------------------------------------------------ #
    # Reachability
    # ------------------------------------------------------------------ #

    def probe_all(self, *, force: bool = False) -> None:
        """Probe TCP and native sequentially, one Max at a time.

        3ds Max is single-threaded and cannot answer overlapping requests.
        Uses process-wide ``PROBE_SERIAL`` shared with ``list_max_instances``
        so TCP pings and named-pipe checks never overlap. Native is an OS
        existence check; TCP is a one-line protocol ping. Remote hosts skip
        native (pipes are local to the Max machine).
        """
        with PROBE_SERIAL:
            with self._lock:
                now = time.monotonic()
                targets = [
                    (inst.name, inst.host, inst.port, inst.pid, inst.pipe)
                    for inst in self._instances
                    if force
                    or inst.online_checked_at is None
                    or (now - inst.online_checked_at) >= PROBE_CACHE_SECONDS
                ]
            if not targets:
                return

            results: dict[str, tuple[Optional[bool], Optional[bool], str]] = {}
            for name, host, port, pid, pipe in targets:
                native_online: Optional[bool] = None
                native_pipe = pipe or (_pipe_name_for_pid(pid) if pid else None)
                if native_pipe and _is_local_host(host):
                    native_online = _probe_named_pipe_unlocked(native_pipe)
                tcp_online, err = _probe_tcp(host, port)
                results[name] = (tcp_online, native_online, err)

            with self._lock:
                checked_at = time.monotonic()
                for inst in self._instances:
                    if inst.name not in results:
                        continue
                    tcp_online, native_online, err = results[inst.name]
                    prev = inst.online
                    inst.tcp_online = tcp_online
                    inst.native_online = native_online
                    inst.online = bool(tcp_online) or bool(native_online)
                    inst.online_error = None if inst.online else (err or None)
                    inst.online_checked_at = checked_at
                    if prev is True and inst.online is False:
                        logging.warning(
                            "Instance %s (%s:%s) went offline: %s",
                            inst.name,
                            inst.host,
                            inst.port,
                            err or "unreachable",
                        )
                    elif prev is not True and inst.online is True:
                        logging.info(
                            "Instance %s (%s:%s) is online tcp=%s native=%s",
                            inst.name,
                            inst.host,
                            inst.port,
                            tcp_online,
                            native_online,
                        )

    # ------------------------------------------------------------------ #
    # Introspection
    # ------------------------------------------------------------------ #

    def list_instances(self) -> list[dict[str, Any]]:
        """Public state of every instance: busy/idle, pinned, and online."""
        self.refresh_registry()
        self.probe_all(force=False)
        with self._lock:
            stale = self._purge_stale_locks()
            now = time.monotonic()
            out: list[dict[str, Any]] = []
            for inst in self._instances:
                checked_ago = (
                    round(now - inst.online_checked_at, 1)
                    if inst.online_checked_at is not None
                    else None
                )
                transports = []
                if inst.tcp_online:
                    transports.append("tcp")
                if inst.native_online:
                    transports.append("native")
                out.append(
                    {
                        "name": inst.name,
                        "host": inst.host,
                        "port": inst.port,
                        "pid": inst.pid,
                        "pipe": inst.pipe,
                        "max_version": inst.max_version,
                        "pinned": inst.pinned,
                        "online": inst.online,
                        "tcp_online": inst.tcp_online,
                        "native_online": inst.native_online,
                        "transports": transports,
                        "online_error": inst.online_error,
                        "checked_ago_seconds": checked_ago,
                        "busy": inst.locked_by is not None,
                        "locked_by": _display(inst.locked_by) if inst.locked_by else None,
                        "locked_for_seconds": (
                            round(now - inst.locked_at, 1) if inst.locked_at else 0.0
                        ),
                    }
                )
        for name in stale:
            self._hide_agent_banner(name)
        return out

    def get_my_instance(self, session: object) -> dict[str, Any]:
        """What this session holds, if anything."""
        with self._lock:
            held = self._by_session.get(session)
            if held is None:
                return {"acquired": False, "instance": None}
            inst = self._find(held)
            return {
                "acquired": True,
                "instance": {
                    "name": inst.name,
                    "host": inst.host,
                    "port": inst.port,
                    "pid": inst.pid,
                    "pipe": inst.pipe,
                    "max_version": inst.max_version,
                    "pinned": inst.pinned if inst else None,
                    "online": inst.online if inst else None,
                    "tcp_online": inst.tcp_online if inst else None,
                    "native_online": inst.native_online if inst else None,
                    "acquired_for_seconds": (
                        round(time.monotonic() - inst.locked_at, 1) if inst.locked_at else 0.0
                    ),
                }
                if inst
                else None,
            }


# Module-level singleton used by server.py and the tools.
manager = InstanceManager()
