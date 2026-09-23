"""Tools for managing multiple 3ds Max instances and per-user exclusivity.

These tools let a user discover the configured instances, explicitly acquire
(lock) one for exclusive use, and explicitly release it when done so the
instance becomes idle and available to other users.

Public multi-agent mode uses a bounded FIFO wait queue and a short idle lease:
``acquire_instance`` waits up to ``MAXMCP_ACQUIRE_WAIT_SECONDS`` for capacity,
rejects with ``QUEUE_FULL`` when the wait list is saturated, and auto-releases
after ``MAXMCP_LOCK_TTL`` seconds without tool activity (activity renews the
lease). Scene reset on acquire is **opt-in** (``reset_scene=true`` or
``MAXMCP_RESET_ON_ACQUIRE=true``); when requested, the scene is saved first.

On acquire the Max viewport shows an Agent-takeover HUD; release hides it
(best-effort MaxScript; never blocks the lease).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from mcp.server.fastmcp import Context

from ..instance_manager import (
    AcquireWaitTimeoutError,
    InstanceBusyError,
    InstanceError,
    InstanceNotAcquiredError,
    NoFreeInstanceError,
    QueueFullError,
    manager,
)
from ..server import mcp

_log = logging.getLogger("maxmcp.instances")


def _instance_payload(session: object, client) -> dict:
    mine = manager.get_my_instance(session).get("instance") or {}
    return {
        "name": mine.get("name"),
        "host": getattr(client, "host", mine.get("host")),
        "port": getattr(client, "port", mine.get("port")),
    }


def _best_effort_agent_banner(client: Any, *, show: bool) -> Optional[str]:
    """Show/hide viewport Agent HUD; never raise into the lease path."""
    if client is None:
        return None
    ms = (
        "MCP_SceneManage.showAgentBanner()"
        if show
        else "MCP_SceneManage.hideAgentBanner()"
    )
    try:
        response = client.send_command(ms)
        return str(response.get("result", "") or "")
    except Exception as exc:  # noqa: BLE001
        _log.warning("agent banner %s failed: %s", "show" if show else "hide", exc)
        return str(exc)


@mcp.tool()
def list_instances() -> str:
    """List all configured 3ds Max instances and their busy/online state.

    Shows each instance's name, host, port, pid/pipe when known, TCP
    (`tcp_online`) and native-pipe (`native_online`) reachability, whether it
    is pinned, held (`busy`), and for how long. ``online`` is true if either
    transport answered. Also reports the acquire wait-queue depth. Probes are
    process-wide serial (one Max at a time; never overlap with
    list_max_instances). Use this before acquire_instance. Remote hosts only
    get a TCP ping; named pipes are local to the Max machine.
    """
    return json.dumps(
        {
            "instances": manager.list_instances(),
            "queue_depth": manager.queue_depth(),
            "queue_max": manager.acquire_queue_max,
            "lease_idle_seconds": manager.lock_ttl,
            "acquire_wait_seconds": manager.acquire_wait_seconds,
            "reset_on_acquire": manager.reset_on_acquire,
        },
        ensure_ascii=False,
    )


@mcp.tool()
def acquire_instance(
    ctx: Context,
    name: Optional[str] = None,
    reset_scene: Optional[bool] = None,
) -> str:
    """Acquire a short lease on a 3ds Max instance for this MCP session.

    If no instance is idle, waits up to the configured acquire timeout on a
    bounded FIFO queue (does not busy-poll). On success the lease is exclusive
    until ``release_instance``, idle TTL expiry, or session disconnect. Activity
    on scene tools renews the idle timer.

    Scene reset is **opt-in** only (default off). Pass ``reset_scene=true`` or
    set ``MAXMCP_RESET_ON_ACQUIRE=true`` to clear the scene after acquire; the
    current file is saved first (unnamed scenes go under ``%TEMP%\\3dsmax-mcp``).

    On success, best-effort shows a viewport HUD: 「正在被 AI Agent 接管，请勿操作」.

    Args:
        name: Optional instance name from list_instances. When omitted, the
            first idle online instance is chosen.
        reset_scene: When true, save then reset after acquire. None uses the
            server default (MAXMCP_RESET_ON_ACQUIRE, default false).
    """
    session = ctx.session
    try:
        client, meta = manager.acquire_with_meta(
            session, name=name, wait=True, reset_scene=reset_scene
        )
    except QueueFullError as exc:
        return json.dumps(
            manager.reject_payload("QUEUE_FULL", str(exc), retryable=True),
            ensure_ascii=False,
        )
    except AcquireWaitTimeoutError as exc:
        return json.dumps(
            manager.reject_payload("WAIT_TIMEOUT", str(exc), retryable=True),
            ensure_ascii=False,
        )
    except InstanceBusyError as exc:
        return json.dumps(
            manager.reject_payload("INSTANCE_BUSY", str(exc), retryable=True),
            ensure_ascii=False,
        )
    except NoFreeInstanceError as exc:
        return json.dumps(
            manager.reject_payload("NO_FREE_INSTANCE", str(exc), retryable=True),
            ensure_ascii=False,
        )
    except InstanceError as exc:
        return json.dumps(
            manager.reject_payload("INSTANCE_ERROR", str(exc), retryable=False),
            ensure_ascii=False,
        )
    banner = _best_effort_agent_banner(client, show=True)
    payload = {
        "acquired": True,
        "instance": _instance_payload(session, client),
        **meta,
    }
    if banner is not None:
        payload["agent_banner"] = banner
    return json.dumps(payload, ensure_ascii=False)


@mcp.tool()
def release_instance(ctx: Context, name: Optional[str] = None) -> str:
    """Release the 3ds Max instance held by the current user.

    Call this when the user's task is complete so the instance becomes idle
    and available to other users (FIFO waiters are woken immediately). Only the
    holder can release; an idle lease also expires after MAXMCP_LOCK_TTL without
    tool activity, and session disconnect auto-releases.

    Best-effort hides the Agent viewport HUD before releasing the lease.

    Args:
        name: Optional instance name to release. When omitted, releases
            whatever instance this session holds.
    """
    session = ctx.session
    banner = None
    try:
        if manager.get_my_instance(session).get("acquired"):
            held_client = manager.get_for_session(session)
            banner = _best_effort_agent_banner(held_client, show=False)
    except Exception:  # noqa: BLE001
        pass
    try:
        result = manager.release(session, name=name)
    except (InstanceNotAcquiredError, InstanceError) as exc:
        return json.dumps({"released": False, "error": str(exc)}, ensure_ascii=False)
    if banner is not None:
        result = dict(result)
        result["agent_banner"] = banner
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def get_my_instance(ctx: Context) -> str:
    """Return the 3ds Max instance currently held by the current user, if any.

    Reports whether this session has acquired an instance and, if so, its
    name, host, port and how long it has been held.
    """
    session = ctx.session
    return json.dumps(manager.get_my_instance(session), ensure_ascii=False)
