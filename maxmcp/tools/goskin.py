"""MCP tools for Auto GoSkin OCR mouse workflow."""

from __future__ import annotations

import os
import sys
from typing import Any, Optional

from ..coerce import StrList
from ..server import mcp, client

_REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]
if (_REPO_ROOT / "dialog_monitor").is_dir() and str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dialog_monitor.click_button import DEFAULT_OCR_BASE  # noqa: E402
from dialog_monitor.goskin_flow import (  # noqa: E402
    cleanup_goskin_lists as _cleanup_goskin_lists,
    confirm_goskin_start as _confirm_goskin_start,
    ensure_goskin_ready as _ensure_goskin_ready,
    run_goskin_auto as _run_goskin_auto,
    run_goskin_skin as _run_goskin_skin,
)


def _ocr_base(override: str = "") -> str:
    value = (override or "").strip()
    if value:
        return value.rstrip("/")
    env = (os.environ.get("MAXMCP_OCR_BASE") or "").strip()
    if env:
        return env.rstrip("/")
    return DEFAULT_OCR_BASE


def _strip_ocr_lines(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip_ocr_lines(v) for k, v in obj.items() if k != "ocr_lines"}
    if isinstance(obj, list):
        return [_strip_ocr_lines(v) for v in obj]
    return obj


def _trim_steps(result: dict[str, Any], include_ocr: bool) -> dict[str, Any]:
    """Drop bulky ocr_lines from nested step payloads unless requested."""
    if include_ocr:
        return result
    return _strip_ocr_lines(result)  # type: ignore[return-value]


@mcp.tool()
def goskin_ensure_ready(
    menu: str = "自动蒙皮",
    item: str = "GoSkinning",
    open_wait_s: float = 8.0,
    ocr_base: str = "",
    include_ocr: bool = False,
) -> dict[str, Any]:
    """Open GoSkin if needed and switch to 蒙皮 / 全局蒙皮 via OCR mouse clicks.

    Requires an interactive (unlocked) desktop session on the Max host.
    If the dialog is missing, opens Max menu ``menu`` -> ``item`` and waits
    up to open_wait_s seconds for the window.
    """
    result = _ensure_goskin_ready(
        client,
        menu=(menu or "自动蒙皮").strip(),
        item=(item or "GoSkinning").strip(),
        open_wait_s=float(open_wait_s),
        ocr_base=_ocr_base(ocr_base),
    )
    return _trim_steps(result, include_ocr)


@mcp.tool()
def goskin_cleanup_lists(
    force: bool = False,
    ocr_base: str = "",
    include_ocr: bool = False,
) -> dict[str, Any]:
    """Clear GoSkin mesh/joint edit lists when OCR shows leftover 模型：N or names.

    Clicks 清空 on 「在场景中选择模型」 and 「在场景中选择关节」 rows.
    No-op when already clean unless force=true.
    """
    result = _cleanup_goskin_lists(
        client,
        force=bool(force),
        ocr_base=_ocr_base(ocr_base),
    )
    return _trim_steps(result, include_ocr)


@mcp.tool()
def goskin_run_skin(
    mesh_names: Optional[StrList] = None,
    bone_names: Optional[StrList] = None,
    complete_timeout_s: float = 180.0,
    click_start: bool = False,
    require_counts: bool = True,
    auto_cleanup: bool = True,
    ocr_base: str = "",
    include_ocr: bool = False,
) -> dict[str, Any]:
    """Prepare GoSkin lists (cleanup→选定 mesh/bones) then PAUSE for user confirm.

    Default click_start=false: returns confirmation summary (mesh/joint names &
    counts) and does NOT click 「开始蒙皮」. After the user agrees, call
    goskin_confirm_start(user_confirmed=true).
    """
    result = _run_goskin_skin(
        client,
        mesh_names=list(mesh_names) if mesh_names else None,
        bone_names=list(bone_names) if bone_names else None,
        complete_timeout_s=float(complete_timeout_s),
        click_start=bool(click_start),
        require_counts=bool(require_counts),
        auto_cleanup=bool(auto_cleanup),
        ocr_base=_ocr_base(ocr_base),
    )
    return _trim_steps(result, include_ocr)


@mcp.tool()
def goskin_confirm_start(
    user_confirmed: bool = False,
    complete_timeout_s: float = 180.0,
    ocr_base: str = "",
    include_ocr: bool = False,
) -> dict[str, Any]:
    """Click 「开始蒙皮」 only when user_confirmed=true; then wait for OCR 「完成」.

    Always ask the user first using the confirmation summary from goskin_run_skin.
    Calling with user_confirmed=false is a no-op refusal.
    """
    result = _confirm_goskin_start(
        client,
        user_confirmed=bool(user_confirmed),
        complete_timeout_s=float(complete_timeout_s),
        ocr_base=_ocr_base(ocr_base),
    )
    return _trim_steps(result, include_ocr)


@mcp.tool()
def goskin_run_auto(
    mesh_names: Optional[StrList] = None,
    bone_names: Optional[StrList] = None,
    menu: str = "自动蒙皮",
    item: str = "GoSkinning",
    open_wait_s: float = 8.0,
    complete_timeout_s: float = 180.0,
    click_start: bool = False,
    auto_cleanup: bool = True,
    ocr_base: str = "",
    include_ocr: bool = False,
) -> dict[str, Any]:
    """Full Auto GoSkin prepare: ensure_ready + run_skin; default pauses before 开始蒙皮."""
    result = _run_goskin_auto(
        client,
        mesh_names=list(mesh_names) if mesh_names else None,
        bone_names=list(bone_names) if bone_names else None,
        menu=(menu or "自动蒙皮").strip(),
        item=(item or "GoSkinning").strip(),
        open_wait_s=float(open_wait_s),
        complete_timeout_s=float(complete_timeout_s),
        click_start=bool(click_start),
        auto_cleanup=bool(auto_cleanup),
        ocr_base=_ocr_base(ocr_base),
    )
    return _trim_steps(result, include_ocr)
