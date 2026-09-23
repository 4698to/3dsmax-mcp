import json as _json
from pathlib import Path
from typing import Any, Optional

from ..coerce import IntList, StrList
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
        return {"ok": False, "error": f"bad MaxScript JSON: {raw!r}", "raw": raw}
    if not isinstance(data, dict):
        return {
            "ok": False,
            "error": f"expected JSON object, got {type(data).__name__}",
            "raw": raw,
        }
    data.setdefault("ok", True)
    return data


def _maxscript_int_array(values: list[int]) -> str:
    return "#(" + ", ".join(str(v) for v in values) + ")"


def _maxscript_string_array(values: list[str]) -> str:
    parts: list[str] = []
    for s in values:
        esc = str(s).replace("\\", "\\\\").replace('"', '\\"')
        parts.append(f'"{esc}"')
    return "#(" + ", ".join(parts) + ")"


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
    ``select_by_handles`` / GoSkin. Meshes whose names start with ``v_`` are
    omitted, except the exact name ``v_body`` (case-insensitive). For skinning
    bone *filtering*, prefer ``propose_skin_bones`` after collecting mesh handles.
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
def propose_skin_bones(
    mesh_handles: IntList,
    bone_handles: Optional[IntList] = None,
    pad_ratio: float = 0.05,
    abs_padding: float = 0.0,
    sample_count: int = 5,
    exclude_prefixes: Optional[StrList] = None,
) -> dict[str, Any]:
    """Propose Bone/Biped nodes for skinning by mesh surface proximity.

    Calls ``MCP_SkinManage.proposeSkinBones`` (no Skin weights required). Uses
    signed distance (``closestFace`` / hit normal): samples at bone pivot, axis
    end, and world AABB center. A bone is kept if a sample is confirmed inside
    the mesh core AABB, or if the nearest outside distance is within
    ``0.5 * max(abs_padding, pad_ratio * mesh_diag)``. Ancestors of near bones
    are always filled in. Use returned ``bones_handle`` with ``goskin_run_skin``.

    Args:
        mesh_handles: Target mesh AnimHandles (required).
        bone_handles: Optional candidate bones; omit to scan unhidden Bone/Biped.
        pad_ratio: Distance threshold as a fraction of mesh AABB diagonal
            (outside keep uses half of that threshold).
        abs_padding: Absolute distance threshold (scene units); max with pad_ratio.
        sample_count: Kept for API compatibility; Max uses fixed 3-point sampling.
        exclude_prefixes: Name prefixes to skip (default keeps Max-side ``#("v_")``
            unless you pass an explicit list, including empty to disable).
    """
    mesh_ints: list[int] = []
    for h in mesh_handles or []:
        try:
            mesh_ints.append(int(h))
        except (TypeError, ValueError):
            return {"ok": False, "code": "BAD_PARAM", "error": f"invalid mesh handle: {h!r}"}
    if not mesh_ints:
        return {"ok": False, "code": "BAD_PARAM", "error": "mesh_handles is empty"}

    bone_arr = "undefined"
    if bone_handles:
        bone_ints: list[int] = []
        for h in bone_handles:
            try:
                bone_ints.append(int(h))
            except (TypeError, ValueError):
                return {"ok": False, "code": "BAD_PARAM", "error": f"invalid bone handle: {h!r}"}
        bone_arr = _maxscript_int_array(bone_ints)

    pr = float(pad_ratio)
    ap = float(abs_padding)
    sc = max(1, min(int(sample_count or 5), 32))
    mesh_arr = _maxscript_int_array(mesh_ints)

    prelude = ""
    if exclude_prefixes is not None:
        prelude = (
            f"MCP_SkinManage.excludePrefixes = {_maxscript_string_array(list(exclude_prefixes))}\n"
        )

    ms = (
        prelude
        + "(if MCP_SkinManage == undefined then "
        + '"{\\"ok\\":false,\\"code\\":\\"PLUGIN_MISSING\\",\\"error\\":\\"MCP_SkinManage not loaded; fileIn skin_Manage.ms\\"}" '
        + f"else MCP_SkinManage.proposeSkinBones {mesh_arr} boneHandles:{bone_arr} "
        + f"padRatio:{pr} absPadding:{ap} sampleCount:{sc})"
    )
    raw = _call_scene_manage(ms)
    data = _parse_scene_manage_json(raw)
    if data.get("ok") is False and data.get("code") is None and "error" in data:
        return data
    # Normalize list fields for callers
    if isinstance(data, dict) and data.get("ok") is not False:
        data.setdefault("ok", True)
        if "bones_handle" in data:
            data["bones_handle"] = list(data.get("bones_handle") or [])
        if "bones" in data:
            data["bones"] = list(data.get("bones") or [])
    return data


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
    arr = _maxscript_int_array(ints)
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
