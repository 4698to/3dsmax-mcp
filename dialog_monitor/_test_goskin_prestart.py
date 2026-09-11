# -*- coding: utf-8 -*-
"""Select mesh/bones then 选定; never click 开始蒙皮."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from maxmcp.max_client import MaxClient
from dialog_monitor.click_button import (
    _exec_ms,
    click_dialog_button,
    ensure_dialog_monitor_loaded,
    recognize_dialog,
)
from dialog_monitor.goskin_flow import ensure_goskin_ready, run_goskin_skin


def _dismiss_warning_if_any(client: MaxClient) -> dict:
    """Best-effort: click common warning buttons if a small dialog is visible."""
    for text in ("确定", "OK", "是", "Yes", "关闭"):
        try:
            # Probe via Max findDialog for common message boxes is hard;
            # try OCR click on GoSkin dialog first for 确定 if present.
            r = click_dialog_button(
                text,
                title_pattern="*",
                vendor_pattern="*",
                require_vendor=False,
                client=client,
                score_min=0.55,
            )
            if r.get("ok"):
                return {"ok": True, "clicked": text, "screen_xy": r.get("screen_xy")}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    return {"ok": False, "skipped": True}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    client = MaxClient(host="127.0.0.1", port=8765, transport="tcp")
    ensure_dialog_monitor_loaded(client)
    print("ping", client.send_command('"pong"', cmd_type="maxscript", timeout=5).get("result"))

    # Dismiss leftover warning from empty 选定 if any
    dismiss = _dismiss_warning_if_any(client)
    print("dismiss", json.dumps(dismiss, ensure_ascii=False))

    ensure = ensure_goskin_ready(client, open_wait_s=8.0)
    print("ensure", json.dumps({"ok": ensure.get("ok"), "error": ensure.get("error")}, ensure_ascii=False))
    if not ensure.get("ok"):
        return 1

    # Explicit names: mesh then bones — flow selects before each 选定
    skin = run_goskin_skin(
        client,
        mesh_names=["Box001"],
        bone_names=["Bone001", "Bone002", "Bone003"],
        click_start=False,
    )
    brief = {
        "ok": skin.get("ok"),
        "error": skin.get("error"),
        "awaiting_start_confirm": skin.get("awaiting_start_confirm"),
        "select_mesh_scene": skin.get("steps", {}).get("select_mesh_scene"),
        "select_mesh": {
            k: (skin.get("steps") or {}).get("select_mesh", {}).get(k)
            for k in ("ok", "screen_xy", "error")
        },
        "select_bones_scene": skin.get("steps", {}).get("select_bones_scene"),
        "select_bones": {
            k: (skin.get("steps") or {}).get("select_bones", {}).get(k)
            for k in ("ok", "screen_xy", "error")
        },
    }
    print(json.dumps(brief, ensure_ascii=False, indent=2))

    time.sleep(0.5)
    rec = recognize_dialog(client=client)
    texts = [ln.get("text") for ln in (rec.get("ocr_lines") or [])][:40]
    print("ocr_after", json.dumps(texts, ensure_ascii=False))

    out = Path(__file__).with_name("_last_goskin_prestart.json")
    out.write_text(
        json.dumps(
            {"ensure": ensure, "skin": skin, "ocr_after": texts, "dismiss": dismiss},
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print("wrote", out)
    print("STOPPED before 开始蒙皮 (as requested)")
    return 0 if skin.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
