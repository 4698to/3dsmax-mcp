"""Multi-instance registry and per-session exclusivity for 3ds Max MCP.

Each configured 3ds Max instance is an exclusive resource: at most one MCP
session may hold it at a time. A session explicitly acquires an instance,
uses it through the shared `client` proxy, then explicitly releases it so
the instance becomes idle and available to other users.

Instances are configured via the MAXMCP_INSTANCES environment variable as
comma-separated `host:port[:name]` entries, e.g.:

    MAXMCP_INSTANCES=127.0.0.1:8765:max1,127.0.0.1:8766:max2

When the variable is unset, instances are auto-discovered from the registry
file that each 3ds Max instance writes (JSONL, one line per instance with
port/pid/lastSeen). The registry path is taken from MAXMCP_REGISTRY, else
%LOCALAPPDATA%\\3dsmax-mcp\\instances.jsonl; entries whose heartbeat is older
than REGISTRY_TTL are considered gone. Only when no instance is discovered at
all does the manager fall back to a single 127.0.0.1:8765 instance with
implicit session binding (backwards compatible with the previous
single-instance behavior).

A background watcher thread applies the heartbeat TTL continuously: an instance
is marked offline and dropped from the managed list (its lock released) as soon
as its heartbeat expires, without waiting for the next tool call.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

from .max_client import MaxClient

ENV_INSTANCES = "MAXMCP_INSTANCES"
ENV_LOCK_TTL = "MAXMCP_LOCK_TTL"
ENV_REGISTRY = "MAXMCP_REGISTRY"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_LOCK_TTL = 30 * 60  # seconds; abandoned locks are auto-released
DEFAULT_PURGE_INTERVAL = 15  # seconds between stale-lock sweep cycles
REGISTRY_HEARTBEAT_SECONDS = 30  # how often 3ds Max refreshes its entry
REGISTRY_TTL = 90  # seconds; an entry not refreshed within this is gone
REGISTRY_POLL_SECONDS = 5  # background poll interval for the registry file

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


@dataclass
class IMaxInstance:
    """A single configured 3ds Max instance."""

    name: str
    host: str
    port: int
    max_version: Optional[int] = None  # e.g. 2022; None when unknown
    locked_by: Optional[object] = None  # MCP ServerSession currently holding it
    locked_at: Optional[float] = None  # time.monotonic() of acquisition


def _env_lock_ttl() -> float:
    raw = os.environ.get(ENV_LOCK_TTL)
    if raw is None:
        return DEFAULT_LOCK_TTL
    try:
        return max(60.0, float(raw))
    except ValueError:
        return DEFAULT_LOCK_TTL


def _default_registry_path() -> str:
    """Registry file path: MAXMCP_REGISTRY env var, else %LOCALAPPDATA%\\3dsmax-mcp\\instances.jsonl."""
    raw = os.environ.get(ENV_REGISTRY)
    if raw and raw.strip():
        return raw.strip()
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = os.path.expanduser("~")
    return os.path.join(base, "3dsmax-mcp", "instances.jsonl")


class InstanceManager:
    """Thread-safe registry of 3ds Max instances with per-session locks."""

    def __init__(self, spec: Optional[str] = None, lock_ttl: Optional[float] = None):
        self._lock = threading.RLock()
        self._lock_ttl = lock_ttl if lock_ttl is not None else _env_lock_ttl()
        self._instances: list[IMaxInstance] = []
        self._clients: dict[str, MaxClient] = {}
        self._by_session: dict[object, str] = {}  # session object -> instance name
        self._auto_bind = False  # single-instance fallback binds implicitly
        env_spec = os.environ.get(ENV_INSTANCES)
        self._explicit = bool(spec) or bool(env_spec)  # explicit config -> never touch registry
        self._load(spec if spec is not None else env_spec)
        self.start_watching()
        self._start_stale_purger()

    # ------------------------------------------------------------------ #
    # Loading
    # ------------------------------------------------------------------ #

    def _build(self, entries: list[tuple[str, int, str, Optional[int]]]) -> None:
        """Register parsed (host, port, name, max_version) entries. Caller holds self._lock."""
        for host, port, name, max_version in entries:
            if any(i.name == name for i in self._instances):
                raise ValueError(f"Duplicate instance name {name!r}")
            self._instances.append(
                IMaxInstance(name=name, host=host, port=port, max_version=max_version)
            )
            self._clients[name] = MaxClient(host=host, port=port)
        logging.info(
            "InstanceManager loaded %d 3ds Max instance(s): %s",
            len(self._instances),
            ", ".join(
                f"{i.name}={i.host}:{i.port}"
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

        Lets explicitly-configured instances (MAXMCP_INSTANCES) still report
        the 3ds Max version written by the running Max-side script. Never
        raises; returns {} on any failure.
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

    def _load(self, spec: Optional[str]) -> None:
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
                self._build(entries)
                return

            discovered = self._discover_registry_instances()
            if discovered:
                self._build(discovered)
                self._auto_bind = False
                logging.info("Auto-discovered %d 3ds Max instance(s) from registry", len(discovered))
            else:
                # Fallback single instance: still report the version the Max-side
                # script last recorded for this port (registry may be stale).
                versions = self._registry_version_by_port()
                self._build([(DEFAULT_HOST, DEFAULT_PORT, "max1", versions.get(DEFAULT_PORT))])
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
            found.append(("127.0.0.1", port, name, ver if isinstance(ver, int) else None))
        return found

    def refresh_registry(self) -> None:
        """Re-sync instances from the auto-discovery registry file.

        New instances are added, gone ones removed (releasing their locks),
        and the single-instance fallback upgrades to the discovered list as
        soon as instances appear. No-op when instances were configured
        explicitly via MAXMCP_INSTANCES.
        """
        with self._lock:
            if self._explicit:
                return
            discovered = self._discover_registry_instances()
            if not discovered:
                # Registry empty: if we were in discovery mode, every 3ds Max
                # instance is gone. Release all locks and clear the list.
                if not self._auto_bind and self._instances:
                    for inst in self._instances:
                        if inst.locked_by:
                            self._by_session.pop(inst.locked_by, None)
                            logging.warning(
                                "Instance %s disappeared from registry; released its lock",
                                inst.name,
                            )
                    self._clients.clear()
                    self._instances = []
                return

            current = {inst.name: inst for inst in self._instances}
            new_instances: list[IMaxInstance] = []
            seen: set[str] = set()
            for host, port, name, max_version in discovered:
                if name in seen:
                    continue
                seen.add(name)
                old = current.pop(name, None)
                if old is not None and old.port == port:
                    if max_version and old.max_version != max_version:
                        old.max_version = max_version
                    new_instances.append(old)
                else:
                    if old is not None and old.locked_by:
                        # Same name, different port (3ds Max restarted): drop old lock.
                        self._by_session.pop(old.locked_by, None)
                    new_instances.append(IMaxInstance(name=name, host=host, port=port, max_version=max_version))
                    self._clients[name] = MaxClient(host=host, port=port)

            for old in current.values():  # disappeared from registry
                if old.locked_by:
                    self._by_session.pop(old.locked_by, None)
                    logging.warning(
                        "Instance %s disappeared from registry; released its lock",
                        old.name,
                    )
                self._clients.pop(old.name, None)

            self._instances = new_instances
            self._auto_bind = False  # discovered instances -> explicit acquire required

    # ------------------------------------------------------------------ #
    # Registry watching
    # ------------------------------------------------------------------ #

    def start_watching(self) -> None:
        """Daemon thread: poll the registry file and announce new 3ds Max
        instances on the console. No-op for explicit MAXMCP_INSTANCES config."""
        if self._explicit:
            return
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
        # Startup snapshot: instances already running before the Python server
        # started are announced too (otherwise a Max-first start would be silent).
        for rec in self._read_registry_records():
            name = rec.get("name") or f"max-{rec.get('port')}"
            known[name] = rec.get("port")
            logging.info("3ds Max instance already running: %s", self._describe(rec))
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

    def _describe(self, rec: dict[str, Any]) -> str:
        """Human-readable one-liner for a registry record."""
        name = rec.get("name") or f"max-{rec.get('port')}"
        port = rec.get("port")
        details = []
        if rec.get("pid") is not None:
            details.append(f"pid={rec.get('pid')}")
        if rec.get("maxVersion") is not None:
            details.append(f"3ds Max {rec.get('maxVersion')}")
        suffix = f" ({', '.join(details)})" if details else ""
        return f"{name} at 127.0.0.1:{port}{suffix}"

    def _announce_join(self, rec: dict[str, Any]) -> None:
        """Log a banner for a freshly discovered instance and fold it into
        the managed instance list right away (so list_instances/acquire see
        it with its version without waiting for the next explicit refresh)."""
        logging.info("New 3ds Max instance joined: %s", self._describe(rec))
        self.refresh_registry()

    # ------------------------------------------------------------------ #
    # Lock lifecycle
    # ------------------------------------------------------------------ #

    def _purge_stale_locks(self) -> None:
        """Release locks held for longer than the TTL (abandoned sessions)."""
        now = time.monotonic()
        for inst in self._instances:
            if inst.locked_by and inst.locked_at and (now - inst.locked_at) > self._lock_ttl:
                logging.warning(
                    "Instance %s lock held by stale session %s exceeded TTL; releasing",
                    inst.name,
                    _display(inst.locked_by),
                )
                self._by_session.pop(inst.locked_by, None)
                inst.locked_by = None
                inst.locked_at = None

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
                    self._purge_stale_locks()
            except Exception:  # keep sweeping on transient errors
                continue

    def acquire(self, session: object, name: Optional[str] = None) -> MaxClient:
        """Bind session to a free instance and return its client.

        Idempotent: a session that already holds an instance keeps it. When a
        specific name is given and the session holds a different instance, the
        session switches: its current instance is released first, then the
        named one is acquired (if free). Raises InstanceBusyError if the named
        instance is held by another session, or NoFreeInstanceError when no
        instance is idle.
        """
        if session is None:
            raise InstanceError("No MCP session available for acquisition")
        with self._lock:
            self.refresh_registry()
            self._purge_stale_locks()
            held = self._by_session.get(session)
            if held is not None:
                if name is None or name == held:
                    return self._clients[held]
                self._release_locked(session, held)

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
            else:
                inst = next((i for i in self._instances if not i.locked_by), None)
                if inst is None:
                    raise NoFreeInstanceError(
                        "All 3ds Max instances are busy: "
                        + ", ".join(
                            f"{i.name} (held by {_display(i.locked_by)})" if i.locked_by else i.name
                            for i in self._instances
                        )
                    )

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
                return self.acquire(session)
            if held is None:
                raise InstanceNotAcquiredError(
                    "This session has not acquired any 3ds Max instance. "
                    "Call acquire_instance first, and release_instance when done."
                )
            inst = self._find(held)
            if inst is not None and inst.locked_by != session:
                # Lock was lost (e.g. stale-purged while this session reused the id).
                return self.acquire(session)
            if inst is not None:
                # Activity renewal: every request from a holding session pushes the
                # TTL deadline forward, so an abandoned session's lock expires
                # DEFAULT_LOCK_TTL after its last use rather than after acquire.
                inst.locked_at = time.monotonic()
            return self._clients[held]

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
            logging.info("Session %s released instance %s", _display(session), held)
            return {"released": held, "idle": True}

    def release_all_for_session(self, session: object) -> None:
        """Defensive cleanup of every lock held by a session (never raises)."""
        with self._lock:
            held = self._by_session.get(session)
            if held:
                self._release_locked(session, held)
                logging.info("Session %s cleanup released instance %s", _display(session), held)

    # ------------------------------------------------------------------ #
    # Introspection
    # ------------------------------------------------------------------ #

    def list_instances(self) -> list[dict[str, Any]]:
        """Public state of every instance, including busy/idle and holder."""
        with self._lock:
            self.refresh_registry()
            self._purge_stale_locks()
            now = time.monotonic()
            return [
                {
                    "name": inst.name,
                    "host": inst.host,
                    "port": inst.port,
                    "max_version": inst.max_version,
                    "busy": inst.locked_by is not None,
                    "locked_by": _display(inst.locked_by) if inst.locked_by else None,
                    "locked_for_seconds": (
                        round(now - inst.locked_at, 1) if inst.locked_at else 0.0
                    ),
                }
                for inst in self._instances
            ]

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
                    "max_version": inst.max_version,
                    "acquired_for_seconds": (
                        round(time.monotonic() - inst.locked_at, 1) if inst.locked_at else 0.0
                    ),
                }
                if inst
                else None,
            }


# Module-level singleton used by server.py and the tools.
manager = InstanceManager()
