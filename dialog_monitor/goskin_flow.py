# -*- coding: utf-8 -*-
"""Auto GoSkin orchestration: ensure dialog + OCR mouse skinning.

Proven UI sequence (global skin):
1. ensure window on 蒙皮 / 全局蒙皮
2. cleanup dirty lists if needed; dismiss leftover warning dialogs
3. Click list slot 「(选中后在编辑区添加)」 FIRST (required — otherwise 选定 pops a warning)
4. Max-select mesh(es) → click 选定 on 「在场景中选择模型」row
5. Focus list row (模型：N) → Max-select bone(s) → click 选定 on 「在场景中选择关节」
6. Optional: click 开始蒙皮 and wait OCR 「完成」 (gated by click_start / user confirm)
"""

from __future__ import annotations

import re
import time
from typing import Any, Sequence

from .click_button import (
    DEFAULT_OCR_BASE,
    _ensure_max_client,
    _exec_ms,
    click_between_texts,
    click_button_on_same_row,
    click_dialog_button,
    click_menu_path,
    dialog_exists,
    ensure_dialog_monitor_loaded,
    recognize_dialog,
    wait_for_dialog_ocr,
)

TITLE_PATTERN = "自动蒙皮4.*"
VENDOR_PATTERN = "*天晴数码"

_MESH_COUNT_RE = re.compile(r"模型\s*[:：]\s*(\d+)")
_JOINT_COUNT_RE = re.compile(r"关节\s*[:：]\s*(\d+)")

# UI chrome that is NOT edit-list content under the 选定 rows.
_EDIT_AREA_CHROME = {
    "选定",
    "删除",
    "清空",
    "绑定骨骼",
    "邦定骨骼",
    "在场景中选择模型",
    "在场景中选择关节",
    "编辑区",
    "开始蒙皮",
    "修复飞点",
    "全局蒙皮",
    "局部蒙皮",
    "裙摆蒙皮",
    "面部蒙皮",
    "算法模型",
    "合并网格",
    "骨骼架设",
    "蒙皮",
    "后处理",
    "配置",
}


def _ocr_texts(result: dict[str, Any]) -> list[str]:
    lines = result.get("ocr_lines") or []
    return [str(ln.get("text") or "") for ln in lines if isinstance(ln, dict)]


def _has_any_text(texts: Sequence[str], needles: Sequence[str]) -> bool:
    joined = " ".join(texts)
    return any(n in joined for n in needles)


def _parse_list_counts(texts: Sequence[str]) -> dict[str, int | None]:
    """Parse 模型：N / 关节：M from OCR lines (may be one or two lines)."""
    mesh_n: int | None = None
    joint_n: int | None = None
    for t in texts:
        m = _MESH_COUNT_RE.search(t)
        if m:
            mesh_n = int(m.group(1))
        j = _JOINT_COUNT_RE.search(t)
        if j:
            joint_n = int(j.group(1))
    return {"mesh": mesh_n, "joints": joint_n}


def _normalize_ui(text: str) -> str:
    return "".join(str(text).split())


def _is_edit_list_content(text: str) -> bool:
    """True if OCR line looks like an added mesh/bone name (not chrome)."""
    raw = str(text or "").strip()
    if not raw:
        return False
    norm = _normalize_ui(raw)
    if not norm:
        return False
    if "选中后在编辑区添加" in norm:
        return False
    if _MESH_COUNT_RE.search(raw) or _JOINT_COUNT_RE.search(raw):
        return False
    for chrome in _EDIT_AREA_CHROME:
        if _normalize_ui(chrome) == norm:
            return False
        if norm.startswith(_normalize_ui(chrome)) and len(norm) <= len(_normalize_ui(chrome)) + 2:
            return False
    # Merged button strips
    if "选定" in norm and ("删除" in norm or "清空" in norm or "骨骼" in norm):
        return False
    # Status / dropdown noise
    if norm.startswith("等待用户") or "general-v" in norm.lower():
        return False
    if norm in {"×", "X", "一", "□", "☑"}:
        return False
    return True


def detect_goskin_needs_cleanup(texts: Sequence[str]) -> dict[str, Any]:
    """Detect leftover list/edit content that must be cleared before skinning.

    Dirty when any of:
    - list summary shows 模型：N (N>=1) under 全局蒙皮
    - edit area under 在场景中选择模型 has content lines
    - edit area under 在场景中选择关节 has content lines
    """
    counts = _parse_list_counts(texts)
    reasons: list[str] = []
    mesh_n = counts.get("mesh")
    joint_n = counts.get("joints")
    if mesh_n is not None and int(mesh_n) >= 1:
        reasons.append(f"list_summary_mesh={mesh_n}")
    if joint_n is not None and int(joint_n) >= 1:
        reasons.append(f"list_summary_joints={joint_n}")

    # Slice OCR texts between section headers to find leftover names.
    idx_model = next((i for i, t in enumerate(texts) if "在场景中选择模型" in t), None)
    idx_joint = next((i for i, t in enumerate(texts) if "在场景中选择关节" in t), None)
    idx_start = next((i for i, t in enumerate(texts) if t.strip() == "开始蒙皮" or t.startswith("开始蒙皮")), None)

    mesh_area: list[str] = []
    joint_area: list[str] = []
    if idx_model is not None:
        end = idx_joint if idx_joint is not None else (idx_start if idx_start is not None else len(texts))
        mesh_area = [t for t in texts[idx_model + 1 : end] if _is_edit_list_content(t)]
    if idx_joint is not None:
        end = idx_start if idx_start is not None else len(texts)
        joint_area = [t for t in texts[idx_joint + 1 : end] if _is_edit_list_content(t)]

    if mesh_area:
        reasons.append(f"mesh_edit_area={mesh_area[:5]}")
    if joint_area:
        reasons.append(f"joint_edit_area={joint_area[:5]}")

    return {
        "needed": bool(reasons),
        "reasons": reasons,
        "counts": counts,
        "mesh_edit_area": mesh_area,
        "joint_edit_area": joint_area,
    }


def cleanup_goskin_lists(
    client: Any | None = None,
    *,
    title_pattern: str = TITLE_PATTERN,
    vendor_pattern: str = VENDOR_PATTERN,
    ocr_base: str = DEFAULT_OCR_BASE,
    force: bool = False,
) -> dict[str, Any]:
    """Clear mesh/joint edit lists when OCR shows leftover content.

    Clicks 清空 on 「在场景中选择模型」 and 「在场景中选择关节」 rows.
    """
    client = _ensure_max_client(client)
    ensure_dialog_monitor_loaded(client)
    steps: dict[str, Any] = {}

    snap = _snapshot_ocr(
        client,
        title_pattern=title_pattern,
        vendor_pattern=vendor_pattern,
        ocr_base=ocr_base,
    )
    steps["before"] = {
        "ok": snap.get("ok"),
        "counts": snap.get("counts"),
        "ocr_texts": snap.get("ocr_texts"),
        "error": snap.get("error"),
    }
    if not snap.get("ok"):
        return {"ok": False, "steps": steps, "error": snap.get("error") or "ocr failed", "cleaned": False}

    detect = detect_goskin_needs_cleanup(snap.get("ocr_texts") or [])
    steps["detect"] = detect
    if not detect.get("needed") and not force:
        return {
            "ok": True,
            "cleaned": False,
            "skipped": True,
            "steps": steps,
            "error": None,
            "counts": detect.get("counts"),
        }

    clear_mesh = click_button_on_same_row(
        "在场景中选择模型",
        "清空",
        title_pattern=title_pattern,
        vendor_pattern=vendor_pattern,
        ocr_base=ocr_base,
        client=client,
    )
    steps["clear_mesh"] = {
        "ok": clear_mesh.get("ok"),
        "screen_xy": clear_mesh.get("screen_xy"),
        "matched": clear_mesh.get("matched"),
        "error": clear_mesh.get("error"),
    }
    if not clear_mesh.get("ok"):
        return {
            "ok": False,
            "cleaned": False,
            "steps": steps,
            "error": clear_mesh.get("error") or "clear mesh 清空 failed",
        }
    time.sleep(0.35)

    clear_joints = click_button_on_same_row(
        "在场景中选择关节",
        "清空",
        title_pattern=title_pattern,
        vendor_pattern=vendor_pattern,
        ocr_base=ocr_base,
        client=client,
    )
    steps["clear_joints"] = {
        "ok": clear_joints.get("ok"),
        "screen_xy": clear_joints.get("screen_xy"),
        "matched": clear_joints.get("matched"),
        "error": clear_joints.get("error"),
    }
    if not clear_joints.get("ok"):
        return {
            "ok": False,
            "cleaned": False,
            "steps": steps,
            "error": clear_joints.get("error") or "clear joints 清空 failed",
        }

    time.sleep(0.45)
    after = _snapshot_ocr(
        client,
        title_pattern=title_pattern,
        vendor_pattern=vendor_pattern,
        ocr_base=ocr_base,
    )
    steps["after"] = {
        "ok": after.get("ok"),
        "counts": after.get("counts"),
        "ocr_texts": after.get("ocr_texts"),
        "error": after.get("error"),
    }
    after_detect = detect_goskin_needs_cleanup(after.get("ocr_texts") or [])
    steps["after_detect"] = after_detect
    if after_detect.get("needed"):
        return {
            "ok": False,
            "cleaned": True,
            "steps": steps,
            "error": f"cleanup_incomplete: still dirty ({after_detect.get('reasons')})",
            "counts": after_detect.get("counts"),
        }
    return {
        "ok": True,
        "cleaned": True,
        "steps": steps,
        "error": None,
        "counts": after_detect.get("counts"),
    }



def _selection_count(client: Any) -> int:
    raw = _exec_ms(client, "(selection.count as string)", timeout=10.0).strip().strip('"')
    digits = "".join(ch for ch in raw if ch.isdigit())
    return int(digits) if digits else 0


def _select_objects_via_max(
    client: Any,
    names: Sequence[str] | None,
) -> dict[str, Any]:
    """Select scene nodes by name using MaxScript (works over TCP without native)."""
    if not names:
        return {"ok": True, "skipped": True, "names": [], "count": _selection_count(client)}
    escaped = []
    for n in names:
        s = str(n).replace("\\", "\\\\").replace('"', '\\"')
        escaped.append(f'"{s}"')
    arr = "#(" + ", ".join(escaped) + ")"
    code = f"""(
        clearSelection()
        local nameList = {arr}
        local found = #()
        for n in nameList do (
            local obj = getNodeByName n
            if obj != undefined do (
                selectMore obj
                append found n
            )
        )
        "ok|" + (found.count as string) + "|" + (found as string)
    )"""
    raw = _exec_ms(client, code, timeout=30.0).strip().strip('"')
    if not raw.startswith("ok|"):
        return {"ok": False, "error": raw, "names": list(names)}
    parts = raw.split("|", 2)
    count = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    if count <= 0:
        return {
            "ok": False,
            "error": f"no scene objects found for names={list(names)!r}",
            "names": list(names),
            "raw": raw,
        }
    return {"ok": True, "count": count, "names": list(names), "raw": raw}


def _require_selection(
    client: Any,
    *,
    names: Sequence[str] | None,
    role: str,
) -> dict[str, Any]:
    """Ensure Max has a non-empty selection before clicking 选定."""
    if names:
        sel = _select_objects_via_max(client, list(names))
        if not sel.get("ok"):
            return sel
        time.sleep(0.2)
        return sel
    count = _selection_count(client)
    if count <= 0:
        return {
            "ok": False,
            "error": (
                f"empty Max selection before 选定 ({role}); "
                f"pass {role}_names or select objects in the scene first "
                f"(GoSkin warns if 选定 is clicked with no selection)"
            ),
            "count": 0,
        }
    return {"ok": True, "skipped": True, "count": count, "names": []}


def _snapshot_ocr(
    client: Any,
    *,
    title_pattern: str,
    vendor_pattern: str,
    ocr_base: str,
) -> dict[str, Any]:
    recognized = recognize_dialog(
        title_pattern=title_pattern,
        vendor_pattern=vendor_pattern,
        ocr_base=ocr_base,
        client=client,
    )
    texts = _ocr_texts(recognized) if recognized.get("ok") else []
    return {
        "ok": bool(recognized.get("ok")),
        "ocr_texts": texts[:40],
        "counts": _parse_list_counts(texts),
        "error": recognized.get("error"),
        "capture": recognized.get("capture"),
        "ocr_lines": recognized.get("ocr_lines"),
    }


def dismiss_goskin_warnings(
    client: Any | None = None,
    *,
    ocr_base: str = DEFAULT_OCR_BASE,
    max_clicks: int = 3,
) -> dict[str, Any]:
    """Best-effort: close GoSkin/system warning dialogs that block the main UI.

    Empty 选定 (or 选定 without list-slot focus) pops a modal that covers
    GoSkin; subsequent OCR/clicks then miss. Click common confirm buttons.
    """
    client = _ensure_max_client(client)
    ensure_dialog_monitor_loaded(client)
    clicked: list[dict[str, Any]] = []
    for _ in range(max(1, int(max_clicks))):
        hit = None
        for text in ("确定", "OK", "是", "Yes", "关闭"):
            # Prefer any top-level dialog (warning may not match GoSkin title).
            try:
                r = click_dialog_button(
                    text,
                    title_pattern="*",
                    vendor_pattern="*",
                    require_vendor=False,
                    ocr_base=ocr_base,
                    client=client,
                    score_min=0.55,
                )
            except Exception as exc:
                return {"ok": False, "clicked": clicked, "error": str(exc)}
            if r.get("ok"):
                hit = {"text": text, "screen_xy": r.get("screen_xy"), "title": (r.get("capture") or {}).get("title")}
                clicked.append(hit)
                time.sleep(0.35)
                break
        if hit is None:
            break
    return {"ok": True, "clicked": clicked, "dismissed": len(clicked) > 0}


def focus_goskin_list_slot(
    client: Any | None = None,
    *,
    title_pattern: str = TITLE_PATTERN,
    vendor_pattern: str = VENDOR_PATTERN,
    ocr_base: str = DEFAULT_OCR_BASE,
    required: bool = True,
) -> dict[str, Any]:
    """Click the global-skin list slot before 选定.

    Must click 「(选中后在编辑区添加)」 (or an existing ``模型：N`` row) so GoSkin
    knows which list entry receives the scene selection. Skipping this and
    clicking 选定 triggers a blocking warning dialog.
    """
    client = _ensure_max_client(client)
    ensure_dialog_monitor_loaded(client)

    for needle in ("(选中后在编辑区添加)", "选中后在编辑区添加", "模型："):
        r = click_dialog_button(
            needle,
            title_pattern=title_pattern,
            vendor_pattern=vendor_pattern,
            ocr_base=ocr_base,
            client=client,
        )
        if r.get("ok"):
            time.sleep(0.25)
            return {
                "ok": True,
                "matched": r.get("matched"),
                "screen_xy": r.get("screen_xy"),
                "needle": needle,
                "error": None,
            }

    # Midpoint fallback between 合并网格 / 编辑区 (dynamic list body).
    mid = click_between_texts(
        "合并网格",
        "编辑区",
        title_pattern=title_pattern,
        vendor_pattern=vendor_pattern,
        ocr_base=ocr_base,
        client=client,
        prefer_dynamic_list=True,
    )
    if mid.get("ok"):
        time.sleep(0.25)
        return {
            "ok": True,
            "matched": mid.get("matched"),
            "screen_xy": mid.get("screen_xy"),
            "needle": "between:合并网格..编辑区",
            "method": mid.get("method"),
            "error": None,
        }

    err = (
        mid.get("error")
        or "list_slot_not_found: could not click 「(选中后在编辑区添加)」"
    )
    if required:
        return {"ok": False, "error": err, "needle": None}
    return {"ok": True, "soft": True, "error": err, "needle": None}


def ensure_goskin_ready(
    client: Any | None = None,
    *,
    title_pattern: str = TITLE_PATTERN,
    vendor_pattern: str = VENDOR_PATTERN,
    menu: str = "自动蒙皮",
    item: str = "GoSkinning",
    open_wait_s: float = 8.0,
    poll_s: float = 1.0,
    ocr_base: str = DEFAULT_OCR_BASE,
) -> dict[str, Any]:
    """Ensure GoSkin dialog is open on 蒙皮 / 全局蒙皮."""
    client = _ensure_max_client(client)
    ensure_dialog_monitor_loaded(client)
    steps: dict[str, Any] = {}

    exists = dialog_exists(
        title_pattern=title_pattern,
        vendor_pattern=vendor_pattern,
        client=client,
    )
    steps["detect"] = exists
    if not exists.get("ok"):
        return {"ok": False, "steps": steps, "error": exists.get("error", "detect failed")}

    opened_via_menu = False
    if not exists.get("exists"):
        menu_result = click_menu_path(
            menu,
            item,
            ocr_base=ocr_base,
            client=client,
        )
        steps["open_menu"] = {
            "ok": menu_result.get("ok"),
            "error": menu_result.get("error"),
            "menu": menu,
            "item": item,
        }
        if not menu_result.get("ok"):
            return {
                "ok": False,
                "steps": steps,
                "error": menu_result.get("error") or "failed to open menu",
            }
        opened_via_menu = True
        deadline = time.time() + max(5.0, float(open_wait_s))
        appeared = False
        while time.time() < deadline:
            time.sleep(max(0.3, float(poll_s)))
            probe = dialog_exists(
                title_pattern=title_pattern,
                vendor_pattern=vendor_pattern,
                client=client,
            )
            steps["wait_open"] = probe
            if probe.get("exists"):
                appeared = True
                break
        if not appeared:
            return {
                "ok": False,
                "steps": steps,
                "error": f"dialog did not appear within {open_wait_s}s after menu open",
            }

    recognized = recognize_dialog(
        title_pattern=title_pattern,
        vendor_pattern=vendor_pattern,
        ocr_base=ocr_base,
        client=client,
    )
    steps["pre_ocr"] = {
        "ok": recognized.get("ok"),
        "ocr_texts": _ocr_texts(recognized)[:40],
        "error": recognized.get("error"),
    }
    texts = _ocr_texts(recognized) if recognized.get("ok") else []
    already_ready = _has_any_text(texts, ("编辑区", "开始蒙皮")) and _has_any_text(
        texts, ("全局蒙皮",)
    )

    if not already_ready:
        tab1 = click_dialog_button(
            "蒙皮",
            title_pattern=title_pattern,
            vendor_pattern=vendor_pattern,
            ocr_base=ocr_base,
            client=client,
        )
        steps["tab_蒙皮"] = {
            "ok": tab1.get("ok"),
            "matched": tab1.get("matched"),
            "screen_xy": tab1.get("screen_xy"),
            "error": tab1.get("error"),
        }
        if not tab1.get("ok"):
            return {"ok": False, "steps": steps, "error": tab1.get("error") or "click 蒙皮 failed"}
        time.sleep(0.5)

        tab2 = click_dialog_button(
            "全局蒙皮",
            title_pattern=title_pattern,
            vendor_pattern=vendor_pattern,
            ocr_base=ocr_base,
            client=client,
        )
        steps["tab_全局蒙皮"] = {
            "ok": tab2.get("ok"),
            "matched": tab2.get("matched"),
            "screen_xy": tab2.get("screen_xy"),
            "error": tab2.get("error"),
        }
        if not tab2.get("ok"):
            return {
                "ok": False,
                "steps": steps,
                "error": tab2.get("error") or "click 全局蒙皮 failed",
            }
        time.sleep(0.5)

        verify = recognize_dialog(
            title_pattern=title_pattern,
            vendor_pattern=vendor_pattern,
            ocr_base=ocr_base,
            client=client,
        )
        steps["verify"] = {
            "ok": verify.get("ok"),
            "ocr_texts": _ocr_texts(verify)[:40],
            "error": verify.get("error"),
        }
        vtexts = _ocr_texts(verify) if verify.get("ok") else []
        if not _has_any_text(vtexts, ("编辑区", "开始蒙皮")):
            return {
                "ok": False,
                "steps": steps,
                "error": "tab_not_ready: 编辑区/开始蒙皮 not visible after tab clicks "
                "(session may be locked / ui_unchanged)",
            }
    else:
        steps["tabs_skipped"] = True

    return {
        "ok": True,
        "steps": steps,
        "opened_via_menu": opened_via_menu,
        "error": None,
    }


def _extract_edit_names(texts: Sequence[str]) -> dict[str, list[str]]:
    """Pull mesh/joint names from OCR edit-area slices."""
    detect = detect_goskin_needs_cleanup(list(texts))
    return {
        "mesh_names_ocr": list(detect.get("mesh_edit_area") or []),
        "bone_names_ocr": list(detect.get("joint_edit_area") or []),
    }


def build_start_confirmation(
    *,
    counts: dict[str, Any] | None,
    ocr_texts: Sequence[str] | None = None,
    mesh_names: Sequence[str] | None = None,
    bone_names: Sequence[str] | None = None,
    select_mesh_scene: dict[str, Any] | None = None,
    select_bones_scene: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Human-readable summary shown before 开始蒙皮; requires explicit user OK."""
    texts = list(ocr_texts or [])
    ocr_names = _extract_edit_names(texts)
    mesh_list = list(mesh_names or []) or list((select_mesh_scene or {}).get("names") or []) or ocr_names["mesh_names_ocr"]
    bone_list = list(bone_names or []) or list((select_bones_scene or {}).get("names") or []) or ocr_names["bone_names_ocr"]
    counts = counts or {}
    mesh_n = counts.get("mesh")
    joint_n = counts.get("joints")
    if mesh_n is None:
        mesh_n = len(mesh_list) if mesh_list else None
    if joint_n is None:
        joint_n = len(bone_list) if bone_list else None

    summary_lines = [
        "即将点击「开始蒙皮」，请确认以下信息：",
        f"- 模型数量: {mesh_n if mesh_n is not None else '?'}",
        f"- 模型名称: {', '.join(mesh_list) if mesh_list else '(未识别到名称)'}",
        f"- 关节/骨骼数量: {joint_n if joint_n is not None else '?'}",
        f"- 关节/骨骼名称: {', '.join(bone_list) if bone_list else '(未识别到名称)'}",
        "确认后请调用 goskin_confirm_start(user_confirmed=true)。未确认不会点击「开始蒙皮」。",
    ]
    return {
        "need_user_confirm": True,
        "action": "开始蒙皮",
        "mesh_count": mesh_n,
        "joint_count": joint_n,
        "mesh_names": mesh_list,
        "bone_names": bone_list,
        "summary": "\n".join(summary_lines),
        "prompt_zh": summary_lines[0],
    }


def confirm_goskin_start(
    client: Any | None = None,
    *,
    user_confirmed: bool = False,
    title_pattern: str = TITLE_PATTERN,
    vendor_pattern: str = VENDOR_PATTERN,
    ocr_base: str = DEFAULT_OCR_BASE,
    complete_timeout_s: float = 180.0,
    poll_s: float = 2.0,
) -> dict[str, Any]:
    """Click 「开始蒙皮」 only after explicit user confirmation.

    ``user_confirmed`` must be True; otherwise returns without clicking.
    """
    if not user_confirmed:
        return {
            "ok": False,
            "clicked": False,
            "error": "user_confirmed=false: refused to click 开始蒙皮",
            "awaiting_start_confirm": True,
        }

    client = _ensure_max_client(client)
    ensure_dialog_monitor_loaded(client)
    steps: dict[str, Any] = {}

    start = click_dialog_button(
        "开始蒙皮",
        title_pattern=title_pattern,
        vendor_pattern=vendor_pattern,
        ocr_base=ocr_base,
        client=client,
    )
    steps["start"] = {
        "ok": start.get("ok"),
        "matched": start.get("matched"),
        "screen_xy": start.get("screen_xy"),
        "error": start.get("error"),
    }
    if not start.get("ok"):
        return {
            "ok": False,
            "clicked": False,
            "steps": steps,
            "error": start.get("error"),
        }

    done = wait_for_dialog_ocr(
        "完成",
        title_pattern=title_pattern,
        vendor_pattern=vendor_pattern,
        ocr_base=ocr_base,
        timeout_s=complete_timeout_s,
        poll_s=poll_s,
        max_match_len=12,
        client=client,
    )
    steps["wait_complete"] = {
        "ok": done.get("ok"),
        "matched": done.get("matched"),
        "attempts": done.get("attempts"),
        "error": done.get("error"),
        "ocr_texts": _ocr_texts(done)[:30] if done.get("ocr_lines") else None,
    }
    if not done.get("ok"):
        return {
            "ok": False,
            "clicked": True,
            "steps": steps,
            "error": done.get("error"),
        }
    return {"ok": True, "clicked": True, "steps": steps, "error": None}


def run_goskin_skin(
    client: Any | None = None,
    *,
    mesh_names: Sequence[str] | None = None,
    bone_names: Sequence[str] | None = None,
    title_pattern: str = TITLE_PATTERN,
    vendor_pattern: str = VENDOR_PATTERN,
    ocr_base: str = DEFAULT_OCR_BASE,
    complete_timeout_s: float = 180.0,
    poll_s: float = 2.0,
    click_start: bool = False,
    require_counts: bool = True,
    auto_cleanup: bool = True,
) -> dict[str, Any]:
    """Global-skin mouse flow with cleanup, selection guards, and OCR checks.

    Order (do not reorder):
      cleanup(清空) if dirty → select mesh → 选定(模型行) → focus 模型：N
      → select bones → 选定(关节行) → pause for user confirm → [optional] 开始蒙皮

    Default ``click_start=False``: returns ``confirmation`` summary and does NOT
    click 「开始蒙皮」. After the user agrees, call ``confirm_goskin_start(user_confirmed=True)``.
    """
    client = _ensure_max_client(client)
    ensure_dialog_monitor_loaded(client)
    steps: dict[str, Any] = {}

    # ----- Cleanup leftover list/edit content before adding -----
    if auto_cleanup:
        cleaned = cleanup_goskin_lists(
            client,
            title_pattern=title_pattern,
            vendor_pattern=vendor_pattern,
            ocr_base=ocr_base,
        )
        steps["cleanup"] = cleaned
        if not cleaned.get("ok"):
            return {"ok": False, "steps": steps, "error": cleaned.get("error")}

    # ----- Step 1: mesh -----
    sel_mesh = _require_selection(client, names=mesh_names, role="mesh")
    steps["select_mesh_scene"] = sel_mesh
    if not sel_mesh.get("ok"):
        return {"ok": False, "steps": steps, "error": sel_mesh.get("error")}

    add_mesh = click_button_on_same_row(
        "在场景中选择模型",
        "选定",
        title_pattern=title_pattern,
        vendor_pattern=vendor_pattern,
        ocr_base=ocr_base,
        client=client,
    )
    steps["select_mesh"] = {
        "ok": add_mesh.get("ok"),
        "matched": add_mesh.get("matched"),
        "screen_xy": add_mesh.get("screen_xy"),
        "image_xy": add_mesh.get("image_xy"),
        "error": add_mesh.get("error"),
    }
    if not add_mesh.get("ok"):
        return {"ok": False, "steps": steps, "error": add_mesh.get("error")}

    time.sleep(0.45)
    after_mesh = _snapshot_ocr(
        client,
        title_pattern=title_pattern,
        vendor_pattern=vendor_pattern,
        ocr_base=ocr_base,
    )
    steps["verify_mesh"] = {
        "ok": after_mesh.get("ok"),
        "counts": after_mesh.get("counts"),
        "ocr_texts": after_mesh.get("ocr_texts"),
        "error": after_mesh.get("error"),
    }
    mesh_count = (after_mesh.get("counts") or {}).get("mesh")
    if require_counts and (mesh_count is None or int(mesh_count) < 1):
        return {
            "ok": False,
            "steps": steps,
            "error": (
                "mesh_not_added: OCR did not show 模型：N>=1 after 选定 "
                f"(counts={after_mesh.get('counts')})"
            ),
        }

    # ----- Step 2: focus list entry, then bones -----
    focus = click_between_texts(
        "合并网格",
        "编辑区",
        title_pattern=title_pattern,
        vendor_pattern=vendor_pattern,
        ocr_base=ocr_base,
        client=client,
        prefer_dynamic_list=True,
    )
    steps["list_focus"] = {
        "ok": focus.get("ok"),
        "method": focus.get("method"),
        "screen_xy": focus.get("screen_xy"),
        "error": focus.get("error"),
    }
    if not focus.get("ok"):
        fb = click_dialog_button(
            "模型：",
            title_pattern=title_pattern,
            vendor_pattern=vendor_pattern,
            ocr_base=ocr_base,
            client=client,
        )
        steps["list_focus_fallback"] = {
            "ok": fb.get("ok"),
            "screen_xy": fb.get("screen_xy"),
            "error": fb.get("error"),
        }
        if not fb.get("ok"):
            steps["list_focus"]["soft"] = True

    time.sleep(0.3)

    sel_bones = _require_selection(client, names=bone_names, role="bone")
    steps["select_bones_scene"] = sel_bones
    if not sel_bones.get("ok"):
        return {"ok": False, "steps": steps, "error": sel_bones.get("error")}

    add_bones = click_button_on_same_row(
        "在场景中选择关节",
        "选定",
        title_pattern=title_pattern,
        vendor_pattern=vendor_pattern,
        ocr_base=ocr_base,
        client=client,
    )
    steps["select_bones"] = {
        "ok": add_bones.get("ok"),
        "matched": add_bones.get("matched"),
        "screen_xy": add_bones.get("screen_xy"),
        "image_xy": add_bones.get("image_xy"),
        "error": add_bones.get("error"),
    }
    if not add_bones.get("ok"):
        return {"ok": False, "steps": steps, "error": add_bones.get("error")}

    time.sleep(0.45)
    after_bones = _snapshot_ocr(
        client,
        title_pattern=title_pattern,
        vendor_pattern=vendor_pattern,
        ocr_base=ocr_base,
    )
    steps["verify_bones"] = {
        "ok": after_bones.get("ok"),
        "counts": after_bones.get("counts"),
        "ocr_texts": after_bones.get("ocr_texts"),
        "error": after_bones.get("error"),
    }
    joint_count = (after_bones.get("counts") or {}).get("joints")
    if require_counts and (joint_count is None or int(joint_count) < 1):
        return {
            "ok": False,
            "steps": steps,
            "error": (
                "bones_not_added: OCR did not show 关节：N>=1 after 选定 "
                f"(counts={after_bones.get('counts')})"
            ),
        }

    confirmation = build_start_confirmation(
        counts=after_bones.get("counts"),
        ocr_texts=after_bones.get("ocr_texts"),
        mesh_names=mesh_names,
        bone_names=bone_names,
        select_mesh_scene=sel_mesh,
        select_bones_scene=sel_bones,
    )
    steps["confirmation"] = confirmation

    # Default: stop and ask the user. Never click 开始蒙皮 until confirmed.
    if not click_start:
        return {
            "ok": True,
            "steps": steps,
            "error": None,
            "awaiting_start_confirm": True,
            "confirmation": confirmation,
            "counts": after_bones.get("counts"),
            "user_prompt": confirmation.get("summary"),
        }

    # Explicit click_start=True still goes through confirm helper with user_confirmed=True
    # (caller opted in to skip the interactive gate).
    started = confirm_goskin_start(
        client,
        user_confirmed=True,
        title_pattern=title_pattern,
        vendor_pattern=vendor_pattern,
        ocr_base=ocr_base,
        complete_timeout_s=complete_timeout_s,
        poll_s=poll_s,
    )
    steps["start_phase"] = started.get("steps")
    if not started.get("ok"):
        return {
            "ok": False,
            "steps": steps,
            "error": started.get("error"),
            "confirmation": confirmation,
            "counts": after_bones.get("counts"),
        }
    return {
        "ok": True,
        "steps": steps,
        "error": None,
        "confirmation": confirmation,
        "counts": after_bones.get("counts"),
    }


def run_goskin_auto(
    client: Any | None = None,
    *,
    mesh_names: Sequence[str] | None = None,
    bone_names: Sequence[str] | None = None,
    menu: str = "自动蒙皮",
    item: str = "GoSkinning",
    open_wait_s: float = 8.0,
    complete_timeout_s: float = 180.0,
    click_start: bool = False,
    require_counts: bool = True,
    auto_cleanup: bool = True,
    ocr_base: str = DEFAULT_OCR_BASE,
) -> dict[str, Any]:
    """ensure_goskin_ready then run_goskin_skin (default stops for user confirm)."""
    client = _ensure_max_client(client)
    ensure = ensure_goskin_ready(
        client,
        menu=menu,
        item=item,
        open_wait_s=open_wait_s,
        ocr_base=ocr_base,
    )
    if not ensure.get("ok"):
        return {
            "ok": False,
            "steps": {"ensure": ensure.get("steps"), "ensure_error": ensure.get("error")},
            "error": ensure.get("error"),
        }
    skin = run_goskin_skin(
        client,
        mesh_names=mesh_names,
        bone_names=bone_names,
        complete_timeout_s=complete_timeout_s,
        click_start=click_start,
        require_counts=require_counts,
        auto_cleanup=auto_cleanup,
        ocr_base=ocr_base,
    )
    return {
        "ok": bool(skin.get("ok")),
        "steps": {"ensure": ensure, **(skin.get("steps") or {})},
        "error": skin.get("error"),
        "awaiting_start_confirm": skin.get("awaiting_start_confirm"),
        "confirmation": skin.get("confirmation"),
        "user_prompt": skin.get("user_prompt"),
        "counts": skin.get("counts"),
    }
