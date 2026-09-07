"""Process-local Max instance selection, available in every tool profile."""
from ..server import client, mcp


@mcp.tool()
def list_max_instances() -> dict:
    """List live Max instances in default order without selecting one."""
    return client.list_max_instances()


@mcp.tool()
def select_max_instance(pid: int) -> dict:
    """Bind this MCP process to a live Max PID until explicitly changed or released."""
    return client.select_max_instance(pid)


@mcp.tool()
def get_selected_max_instance() -> dict:
    """Read this MCP process's selected target and availability without switching it."""
    return client.get_selected_max_instance()


@mcp.tool()
def release_max_instance() -> dict:
    """Release this MCP process's target, including startup pinning; next connection uses the default."""
    return client.release_max_instance()
