"""Process-local Max instance selection, available in every tool profile."""

from __future__ import annotations

from typing import Any, Optional

from mcp.server.fastmcp import Context

from ..server import client, mcp


@mcp.tool()
def list_max_instances() -> dict:
    """List live Max instances via local native named pipes (no TCP ping).

    Reads %LOCALAPPDATA%\\3dsmax-mcp\\instances\\*.json and probes each pipe
    under the same process-wide serial lock as list_instances — never overlap
    Max probes. Prefer list_instances for multi-host / TCP reachability.
    """
    return client.list_max_instances()


@mcp.tool()
def select_max_instance(
    ctx: Context,
    pid: Optional[int] = None,
    name: Optional[str] = None,
) -> Any:
    """Bind this session/process to a Max instance by PID and/or instance name.

    - ``pid``: local native named-pipe bind (``\\\\.\\pipe\\3dsmax-mcp-pid-<pid>``).
    - ``name``: name from ``list_instances`` (e.g. ``max-8765``). Acquires a
      session lease on that instance with ``reset_scene=false`` so remote TCP
      Max works the same way agents expect from ``acquire_instance``.
    - Digit-only ``name`` (e.g. ``\"12345\"``) is treated as ``pid``.
    - Prefer ``name`` for multi-host / ini-configured instances; use ``pid`` for
      local native-only selection.
    """
    name_s = (name or "").strip() or None
    if name_s is not None and name_s.isdigit() and pid is None:
        pid = int(name_s)
        name_s = None

    if name_s is not None:
        return _select_by_instance_name(ctx, name_s, pid_hint=pid)

    if pid is None:
        raise ValueError(
            'select_max_instance requires pid= or name= '
            '(e.g. name="max-8765" from list_instances)'
        )
    return client.select_max_instance(int(pid))


def _select_by_instance_name(
    ctx: Context,
    name: str,
    *,
    pid_hint: Optional[int] = None,
) -> dict[str, Any]:
    """Acquire session lease by name; optionally also bind local native PID."""
    from ..instance_manager import (
        AcquireWaitTimeoutError,
        InstanceBusyError,
        InstanceError,
        NoFreeInstanceError,
        QueueFullError,
        manager,
    )

    session = ctx.session
    try:
        bound, meta = manager.acquire_with_meta(
            session, name=name, wait=True, reset_scene=False
        )
    except QueueFullError as exc:
        return {"ok": False, "error": "QUEUE_FULL", "message": str(exc), "retryable": True}
    except AcquireWaitTimeoutError as exc:
        return {"ok": False, "error": "WAIT_TIMEOUT", "message": str(exc), "retryable": True}
    except InstanceBusyError as exc:
        return {"ok": False, "error": "INSTANCE_BUSY", "message": str(exc), "retryable": True}
    except NoFreeInstanceError as exc:
        return {"ok": False, "error": "NO_FREE_INSTANCE", "message": str(exc), "retryable": True}
    except InstanceError as exc:
        return {"ok": False, "error": "INSTANCE_ERROR", "message": str(exc), "retryable": False}

    mine = manager.get_my_instance(session).get("instance") or {}
    result: dict[str, Any] = {
        "ok": True,
        "selected_via": "name",
        "name": mine.get("name") or name,
        "host": getattr(bound, "host", mine.get("host")),
        "port": getattr(bound, "port", mine.get("port")),
        "pid": mine.get("pid") or pid_hint,
        "acquired": True,
        "scene_reset": bool(meta.get("scene_reset")),
        **{k: v for k, v in meta.items() if k != "scene_reset"},
    }

    # Same HUD as acquire_instance (name-based select is also a lease).
    from .instances import _best_effort_agent_banner

    banner = _best_effort_agent_banner(bound, show=True)
    if banner is not None:
        result["agent_banner"] = banner

    # Local native bind when we know a live PID (same machine as this MCP process).
    native_pid = pid_hint or mine.get("pid")
    if native_pid:
        try:
            native = client.select_max_instance(int(native_pid))
            result["native"] = native
            result["target_pid"] = native.get("target_pid", native_pid)
            result["target_pipe"] = native.get("target_pipe")
        except (ConnectionError, ValueError, TypeError) as exc:
            result["native_bind"] = f"skipped: {exc}"

    return result


@mcp.tool()
def get_selected_max_instance() -> dict:
    """Read this MCP process's selected target and availability without switching it."""
    return client.get_selected_max_instance()


@mcp.tool()
def release_max_instance() -> dict:
    """Release this MCP process's target, including startup pinning; next connection uses the default."""
    return client.release_max_instance()
