"""Tools for managing multiple 3ds Max instances and per-user exclusivity.

These tools let a user discover the configured instances, explicitly acquire
(lock) one for exclusive use, and explicitly release it when done so the
instance becomes idle and available to other users.
"""

from __future__ import annotations

import json
from typing import Optional

from mcp.server.fastmcp import Context

from ..instance_manager import (
    InstanceBusyError,
    InstanceError,
    InstanceNotAcquiredError,
    NoFreeInstanceError,
    manager,
)
from ..server import mcp


@mcp.tool()
def list_instances() -> str:
    """List all configured 3ds Max instances and their busy/idle state.

    Shows each instance's name, host, port, whether it is currently held by a
    user, and for how long. Use this to discover which instances exist and
    which are free before acquiring one.
    """
    return json.dumps({"instances": manager.list_instances()}, ensure_ascii=False)


@mcp.tool()
def acquire_instance(ctx: Context, name: Optional[str] = None) -> str:
    """Explicitly acquire (lock) a 3ds Max instance for the current user.

    Each instance accepts only one user at a time. Call this before using any
    scene tool so commands are routed to your own instance. If no name is
    given, the first idle instance is chosen automatically. Calling it again
    while already holding an instance keeps your current one.

    Args:
        name: Optional instance name from list_instances to acquire. When
            omitted, an idle instance is picked automatically.
    """
    session = ctx.session
    try:
        instance_client = manager.acquire(session, name=name)
    except (InstanceBusyError, NoFreeInstanceError, InstanceError) as exc:
        return json.dumps({"acquired": False, "error": str(exc)}, ensure_ascii=False)
    return json.dumps(
        {
            "acquired": True,
            "instance": {
                "name": name or manager.get_my_instance(session)["instance"]["name"],
                "host": instance_client.host,
                "port": instance_client.port,
            },
        },
        ensure_ascii=False,
    )


@mcp.tool()
def release_instance(ctx: Context, name: Optional[str] = None) -> str:
    """Release the 3ds Max instance held by the current user.

    Call this when the user's task is complete so the instance becomes idle
    and available to other users. Only the holder can release; an instance is
    also released automatically after the lock timeout if a user disconnects
    without releasing.

    Args:
        name: Optional instance name to release. When omitted, releases
            whatever instance this session holds.
    """
    session = ctx.session
    try:
        result = manager.release(session, name=name)
    except (InstanceNotAcquiredError, InstanceError) as exc:
        return json.dumps({"released": False, "error": str(exc)}, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def get_my_instance(ctx: Context) -> str:
    """Return the 3ds Max instance currently held by the current user, if any.

    Reports whether this session has acquired an instance and, if so, its
    name, host, port and how long it has been held.
    """
    session = ctx.session
    return json.dumps(manager.get_my_instance(session), ensure_ascii=False)
