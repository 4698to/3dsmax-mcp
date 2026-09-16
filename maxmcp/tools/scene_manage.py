import json as _json
from typing import Any, Optional

from ..coerce import IntList
from ..server import mcp, client


_NATIVE_SCENE_ACTIONS = frozenset({"hold", "fetch", "reset", "save", "info"})
_MS_SCENE_ACTIONS = {
    "hold": "MCP_SceneManage.holdState()",
    "fetch": "MCP_SceneManage.fetchState()",
    "reset": "MCP_SceneManage.resetScene()",
    "save": "MCP_SceneManage.saveScene()",
    "info": "MCP_SceneManage.getInfo()",
    "save_older": "MCP_SceneManage.saveScene()",
}


def _unwrap_maxscript_text(raw: Any) -> str:
    """Strip a MAXScript quoted string so JSON payloads can be parsed."""
    text = "" if raw is None else str(raw).strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1]
        text = (
            text.replace('\\"', '"')
            .replace("\\\\", "\\")
            .replace("\\n", "\n")
            .replace("\\r", "\r")
            .replace("\\t", "\t")
        )
    return text


def _call_scene_manage(maxscript: str) -> str:
    response = client.send_command(maxscript)
    return response.get("result", "")


def _parse_scene_manage_json(raw: Any) -> dict[str, Any]:
    text = _unwrap_maxscript_text(raw)
    try:
        data = _json.loads(text)
    except (_json.JSONDecodeError, TypeError):
        return {"ok": False, "error": f"bad MCP_SceneManage JSON: {raw!r}", "raw": raw}
    if not isinstance(data, dict):
        return {
            "ok": False,
            "error": f"expected JSON object, got {type(data).__name__}",
            "raw": raw,
        }
    data.setdefault("ok", True)
    return data


@mcp.tool()
def manage_scene(action: str) -> str:
    """Manage the 3ds Max scene state via MCP_SceneManage (or native hold/fetch/reset/save/info).

    Actions: hold, fetch, reset, save, info, save_older.
    ``save`` / ``save_older`` both call ``MCP_SceneManage.saveScene`` (3 versions
    older; unsaved scenes get a random name under ``%TEMP%\\3dsmax-mcp``).
    """
    from ..helpers.audit_log import clear_scene_path_cache, note_scene_path

    action = action.lower().strip()
    if action not in _MS_SCENE_ACTIONS:
        return "Unknown action: {0}. Use hold, fetch, reset, save, info, or save_older.".format(
            action
        )

    if client.native_available and action in _NATIVE_SCENE_ACTIONS:
        payload = _json.dumps({"action": action})
        response = client.send_command(payload, cmd_type="native:manage_scene")
        result = response.get("result", "")
    else:
        result = _call_scene_manage(_MS_SCENE_ACTIONS[action])

    if action == "reset":
        note_scene_path(None)
    elif action in {"save", "save_older", "fetch"}:
        clear_scene_path_cache()
    return result


@mcp.tool()
def load_scene(file_path: str) -> str:
    """Load a .max scene file into 3ds Max (replaces the current scene).

    Delegates to Max-side ``MCP_SceneManage.loadScene``, which guards against
    missing files / newer file versions and suppresses missing-XRefs and
    missing-external-files dialogs.

    Args:
        file_path: Absolute path to the .max file.
    """
    from ..helpers.audit_log import note_scene_path
    from ..helpers.maxscript import safe_value

    fp = safe_value(file_path)
    if not fp.startswith("@"):
        fp = '@"' + fp.replace('"', '""') + '"'
    result = _call_scene_manage(f"MCP_SceneManage.loadScene {fp}")
    # Cache the requested path for audit lines (Max may still fail; best-effort).
    note_scene_path(file_path)
    return result


@mcp.tool()
def get_unhidden_meshes_bones() -> dict[str, Any]:
    """List currently unhidden Editable_Poly/Mesh nodes and Bone/Biped nodes.

    Calls ``MCP_SceneManage.getunhidden_meshes_bones``. Returns names plus
    AnimHandle arrays (``meshes_handle``, ``bones_handle``) for
    ``select_by_handles`` / GoSkin.
    """
    raw = _call_scene_manage("MCP_SceneManage.getunhidden_meshes_bones()")
    data = _parse_scene_manage_json(raw)
    if not data.get("ok"):
        return data
    return {
        "ok": True,
        "mesh_count": int(data.get("mesh_count") or 0),
        "bone_count": int(data.get("bone_count") or 0),
        "meshes": list(data.get("meshes") or []),
        "bones": list(data.get("bones") or []),
        "meshes_handle": list(data.get("meshes_handle") or []),
        "bones_handle": list(data.get("bones_handle") or []),
    }


@mcp.tool()
def select_by_handles(handles: Optional[IntList] = None) -> dict[str, Any]:
    """Select scene nodes by AnimHandle (``MCP_SceneManage.selectByHandles``).

    Handles are stable only within the current loaded scene. An empty list
    clears the selection.
    """
    ints: list[int] = []
    for h in handles or []:
        try:
            ints.append(int(h))
        except (TypeError, ValueError):
            return {"ok": False, "error": f"invalid handle value: {h!r}", "handles": list(handles or [])}
    arr = "#(" + ", ".join(str(h) for h in ints) + ")"
    raw = _call_scene_manage(f"MCP_SceneManage.selectByHandles {arr}")
    data = _parse_scene_manage_json(raw)
    data.setdefault("handles", ints)
    if not ints:
        data["ok"] = True
        data["cleared"] = True
        data["count"] = int(data.get("count") or 0)
    return data


@mcp.tool()
def undo_last() -> str:
    """Undo the last 3ds Max scene operation."""
    if client.native_available:
        try:
            response = client.send_command("{}", cmd_type="native:undo_last")
            return response.get("result", "")
        except RuntimeError:
            pass

    response = client.send_command('max undo; "Undid last scene operation"')
    return response.get("result", "")
