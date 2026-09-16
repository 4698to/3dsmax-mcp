import json as _json
from pathlib import Path
from typing import Any, Optional

from ..coerce import IntList
from ..server import WORKSPACE_DIR, mcp, client


_NATIVE_SCENE_ACTIONS = frozenset({"hold", "fetch", "reset", "save", "info"})
_MS_SCENE_ACTIONS = {
    "hold": "MCP_SceneManage.holdState()",
    "fetch": "MCP_SceneManage.fetchState()",
    "reset": "MCP_SceneManage.resetScene()",
    "save": "MCP_SceneManage.saveScene()",
    "info": "MCP_SceneManage.getInfo()",
    "show_agent_banner": "MCP_SceneManage.showAgentBanner()",
    "hide_agent_banner": "MCP_SceneManage.hideAgentBanner()",
}
# Path-required actions handled by manage_scene(..., file_path=) → saveSceneAs
_SAVE_AS_ACTIONS = frozenset({"save_as", "save_scene_as"})
_ALL_MANAGE_ACTIONS = frozenset(_MS_SCENE_ACTIONS) | _SAVE_AS_ACTIONS


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


def _resolve_save_as_path(file_path: str) -> str:
    """Keep only the basename; always save under WORKSPACE_DIR.

    Agents often invent absolute / ``/workspace/...`` / nonsense directories. Those
    directories are ignored — only the file name is used so Max writes into the
    shared (or fallback) workspace root.
    """
    raw = (file_path or "").strip()
    if not raw:
        raise ValueError("file_path is empty")

    # Strip quotes agents sometimes wrap around paths.
    if (raw.startswith('"') and raw.endswith('"')) or (
        raw.startswith("'") and raw.endswith("'")
    ):
        raw = raw[1:-1].strip()
    if not raw:
        raise ValueError("file_path is empty")

    name = Path(raw.replace("\\", "/")).name
    name = name.strip().lstrip(".")
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError(f"file_path has no usable file name: {file_path!r}")

    # Block path tricks that survive basename on odd inputs.
    if ".." in name:
        raise ValueError(f"invalid file name: {name!r}")

    dest = Path(str(WORKSPACE_DIR)) / name
    if not dest.name.lower().endswith(".max"):
        dest = Path(str(dest) + ".max")

    return str(dest).replace("\\", "/")


def _save_scene_as_impl(file_path: str) -> str:
    from ..helpers.audit_log import clear_scene_path_cache, note_scene_path
    from ..helpers.maxscript import safe_value

    try:
        resolved = _resolve_save_as_path(file_path)
    except ValueError as exc:
        return f"ERROR: {exc}"

    fp = safe_value(resolved)
    if not fp.startswith("@"):
        fp = '@"' + fp.replace('"', '""') + '"'
    result = _call_scene_manage(f"MCP_SceneManage.saveSceneAs {fp}")
    text = _unwrap_maxscript_text(result)
    if not str(text).startswith("ERROR"):
        note_scene_path(resolved)
        clear_scene_path_cache()
    return text if text else result


@mcp.tool()
def manage_scene(
    action: str,
    file_path: str = "",
    path: str = "",
) -> str:
    """Manage the 3ds Max scene state via MCP_SceneManage (or native hold/fetch/reset/save/info).

    Actions: hold, fetch, reset, save, info, save_as, save_scene_as,
    show_agent_banner, hide_agent_banner.
    ``save`` with no path re-saves the current scene file (or Temp if unsaved).
    ``save`` / ``save_as`` / ``save_scene_as`` with ``file_path`` or ``path`` keep
    only the **basename** and write under the shared workspace root (agent
    directories are ignored). Prefer ``file_path``; ``path`` is accepted as an alias
    because agents often pass that name.
    ``reset`` always saves first, then clears the scene (user-initiated only).
    ``show_agent_banner`` / ``hide_agent_banner`` toggle the viewport HUD warning
    (also auto-shown on acquire_instance / auto-hidden on release_instance).
    """
    from ..helpers.audit_log import clear_scene_path_cache, note_scene_path

    action = action.lower().strip()
    # Tolerate accidental "save_as:/path" by splitting once.
    embedded_path = ""
    if action.startswith("save_as:") or action.startswith("save_scene_as:"):
        action, _, embedded_path = action.partition(":")
        action = action.strip()
        embedded_path = embedded_path.strip()

    # Agents often pass path= instead of file_path=
    requested = (file_path or path or embedded_path or "").strip()

    if action not in _ALL_MANAGE_ACTIONS:
        return (
            "Unknown action: {0}. Use hold, fetch, reset, save, info, save_as, "
            "save_scene_as, show_agent_banner, or hide_agent_banner.".format(
                action
            )
        )

    # save + a name → Save As (basename → WORKSPACE_DIR), not the broken relative cwd
    if action in _SAVE_AS_ACTIONS or (action == "save" and requested):
        if not requested:
            return (
                'ERROR: save_as requires file_path or path '
                '(e.g. manage_scene(action="save_as", file_path="box_origin.max"))'
            )
        return _save_scene_as_impl(requested)

    # Banner is MaxScript-only; native manage_scene has no banner actions.
    if client.native_available and action in _NATIVE_SCENE_ACTIONS:
        if action == "reset":
            # User-initiated reset: save first (handles unsaved → Temp\3dsmax-mcp).
            save_raw = _call_scene_manage("MCP_SceneManage.saveScene()")
            save_text = _unwrap_maxscript_text(save_raw)
            if str(save_text).startswith("ERROR"):
                return f"ERROR: save before reset failed: {save_text}"
        payload = _json.dumps({"action": action})
        response = client.send_command(payload, cmd_type="native:manage_scene")
        result = response.get("result", "")
    else:
        if action == "reset":
            save_raw = _call_scene_manage("MCP_SceneManage.saveScene()")
            save_text = _unwrap_maxscript_text(save_raw)
            if str(save_text).startswith("ERROR"):
                return f"ERROR: save before reset failed: {save_text}"
        result = _call_scene_manage(_MS_SCENE_ACTIONS[action])

    if action == "reset":
        note_scene_path(None)
    elif action in {"save", "fetch"}:
        clear_scene_path_cache()
    return result


@mcp.tool()
def save_scene_as(file_path: str) -> str:
    """Save the current scene under the shared workspace using only the file name.

    Any directory in ``file_path`` is ignored (agents often invent paths). Example:
    ``/workspace/output/foo.max`` and ``D:/tmp/foo.max`` both become
    ``{WORKSPACE_DIR}/foo.max``. Missing ``.max`` is appended. Equivalent to
    ``manage_scene(action="save_as", file_path=...)``.
    """
    return _save_scene_as_impl(file_path)


@mcp.tool()
def save_as(file_path: str) -> str:
    """Alias of ``save_scene_as`` — basename only, under the shared workspace."""
    return _save_scene_as_impl(file_path)


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
