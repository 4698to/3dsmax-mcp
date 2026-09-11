# -*- coding: utf-8 -*-
"""Click list slot then mesh 选定; print counts."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from maxmcp.max_client import MaxClient
from dialog_monitor.click_button import ensure_dialog_monitor_loaded
from dialog_monitor.goskin_flow import (
    dismiss_goskin_warnings,
    ensure_goskin_ready,
    focus_goskin_list_slot,
    run_goskin_skin,
)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    c = MaxClient(host="192.168.139.45", port=8765, transport="tcp", timeout=60.0)
    ensure_dialog_monitor_loaded(c)
    print("dismiss", dismiss_goskin_warnings(c))
    print("ensure", ensure_goskin_ready(c).get("ok"))
    skin = run_goskin_skin(
        c,
        mesh_names=["Box001"],
        bone_names=["Bone001", "Bone002", "Bone003"],
        click_start=False,
    )
    brief = {
        "ok": skin.get("ok"),
        "error": skin.get("error"),
        "awaiting": skin.get("awaiting_start_confirm"),
        "prompt": skin.get("user_prompt"),
        "focus": skin.get("steps", {}).get("focus_list_slot"),
        "select_mesh": {
            k: (skin.get("steps") or {}).get("select_mesh", {}).get(k)
            for k in ("ok", "screen_xy", "image_xy")
        },
        "verify_mesh": (skin.get("steps") or {}).get("verify_mesh", {}).get("counts"),
        "verify_bones": (skin.get("steps") or {}).get("verify_bones", {}).get("counts"),
        "dismiss_fail": (skin.get("steps") or {}).get("dismiss_after_mesh_fail"),
    }
    print(json.dumps(brief, ensure_ascii=False, indent=2, default=str))
    out = Path(__file__).with_name("_last_goskin_slotfix.json")
    out.write_text(json.dumps(skin, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("wrote", out)
    return 0 if skin.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
