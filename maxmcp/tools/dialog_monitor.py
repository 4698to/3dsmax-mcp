"""MCP tools for plugin dialog / menu OCR click via dialog_monitor."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# Checkout layout: dialog_monitor/ sits next to maxmcp/ at the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if (_REPO_ROOT / "dialog_monitor").is_dir() and str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ..server import mcp, client
from dialog_monitor.ocr_client import DEFAULT_OCR_BASE, OcrError, health as ocr_health
from dialog_monitor.click_button import (
    click_dialog_button as _click_dialog_button,
    click_menu_path as _click_menu_path,
    recognize_dialog as _recognize_dialog,
)


def _ocr_base(override: str = "") -> str:
    value = (override or "").strip()
    if value:
        return value.rstrip("/")
    env = (os.environ.get("MAXMCP_OCR_BASE") or "").strip()
    if env:
        return env.rstrip("/")
    return DEFAULT_OCR_BASE


def _trim_ocr_payload(result: dict[str, Any], include_ocr_lines: bool) -> dict[str, Any]:
    """Keep MCP replies compact unless the caller asks for full OCR lines."""
    out = dict(result)
    if include_ocr_lines:
        return out

    lines = out.get("ocr_lines")
    if isinstance(lines, list):
        out["ocr_texts"] = [ln.get("text") for ln in lines if isinstance(ln, dict)]
        out.pop("ocr_lines", None)

    select = out.get("select")
    if isinstance(select, dict) and "ocr_texts" not in select:
        # click_menu_path already stores ocr_texts on select
        pass

    open_step = out.get("open")
    if isinstance(open_step, dict):
        open_step = dict(open_step)
        open_step.pop("ocr_lines", None)
        out["open"] = open_step

    return out


def _workspace_status() -> dict[str, Any]:
    try:
        from ..workspace_config import workspace_info

        return workspace_info()
    except Exception as exc:
        return {"shared_configured": False, "error": str(exc)}


@mcp.tool()
def check_dialog_ocr_health(ocr_base: str = "") -> dict[str, Any]:
    """Check the external dialog OCR service (/v1/ocr/health).

    Default base is MAXMCP_OCR_BASE or http://192.168.139.130:8000.
    Also reports shared workspace status from max_instances.ini [workspace]
    (required for remote Max screenshot → OCR on another host).
    """
    base = _ocr_base(ocr_base)
    ws = _workspace_status()
    try:
        payload = ocr_health(base)
        return {"ok": True, "ocr_base": base, "health": payload, "workspace": ws}
    except OcrError as exc:
        return {"ok": False, "ocr_base": base, "error": str(exc), "workspace": ws}


@mcp.tool()
def recognize_plugin_dialog(
    title_pattern: str = "自动蒙皮4.*",
    vendor_pattern: str = "*天晴数码",
    require_vendor: bool = True,
    ocr_base: str = "",
    include_ocr_lines: bool = False,
) -> dict[str, Any]:
    """Find a plugin dialog by title, screenshot it, and OCR visible text (no click).

    title_pattern / vendor_pattern use Max matchPattern (e.g. "柔体工具*", "*集合").
    Returns capture geometry plus OCR texts. Set include_ocr_lines=true for boxes/scores.
    """
    result = _recognize_dialog(
        title_pattern=title_pattern,
        vendor_pattern=vendor_pattern,
        require_vendor=require_vendor,
        ocr_base=_ocr_base(ocr_base),
        client=client,
    )
    return _trim_ocr_payload(result, include_ocr_lines)


@mcp.tool()
def click_plugin_dialog_button(
    text: str,
    title_pattern: str = "自动蒙皮4.*",
    vendor_pattern: str = "*天晴数码",
    require_vendor: bool = True,
    ocr_base: str = "",
    score_min: float = 0.5,
    include_ocr_lines: bool = False,
) -> dict[str, Any]:
    """Screenshot a plugin dialog, OCR-match button text, and simulate a mouse click.

    Prefers exact OCR text matches over longer labels that merely contain the
    substring (e.g. "计算" will not prefer "计算范围").
    Example: text="计算", title_pattern="柔体工具*", vendor_pattern="*集合".
    """
    if not (text or "").strip():
        raise ValueError("text is required")
    result = _click_dialog_button(
        text.strip(),
        title_pattern=title_pattern,
        vendor_pattern=vendor_pattern,
        require_vendor=require_vendor,
        ocr_base=_ocr_base(ocr_base),
        score_min=float(score_min),
        client=client,
    )
    return _trim_ocr_payload(result, include_ocr_lines)


@mcp.tool()
def click_plugin_menu_path(
    menu: str,
    item: str,
    menu_bar_height: int = 140,
    open_wait_s: float = 0.45,
    ocr_base: str = "",
    score_min: float = 0.5,
    include_ocr_lines: bool = False,
) -> dict[str, Any]:
    """Open a 3ds Max menubar entry via OCR, then click a popup menu item.

    Captures the Max top menu strip, clicks `menu` (e.g. "NDBox"), waits for the
    Win32 popup (#32768), then OCR-clicks `item` (e.g. "天晴盒子").
    """
    if not (menu or "").strip():
        raise ValueError("menu is required")
    if not (item or "").strip():
        raise ValueError("item is required")
    result = _click_menu_path(
        menu.strip(),
        item.strip(),
        menu_bar_height=max(40, int(menu_bar_height)),
        open_wait_s=max(0.05, float(open_wait_s)),
        ocr_base=_ocr_base(ocr_base),
        score_min=float(score_min),
        client=client,
    )
    return _trim_ocr_payload(result, include_ocr_lines)
