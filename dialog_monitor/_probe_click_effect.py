# -*- coding: utf-8 -*-
"""Verify whether mouse clicks affect GoSkin UI on remote Max."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from maxmcp.max_client import MaxClient
from dialog_monitor.click_button import (
    ensure_dialog_monitor_loaded,
    click_dialog_button,
    recognize_dialog,
)
from dialog_monitor.goskin_flow import _ocr_texts


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    c = MaxClient(host="192.168.139.45", port=8765, transport="tcp", timeout=60.0)
    ensure_dialog_monitor_loaded(c)

    before = _ocr_texts(recognize_dialog(client=c))
    print("before_has_edit", "编辑区" in before, "局部" , any("局部" in t for t in before))

    r = click_dialog_button("局部蒙皮", client=c)
    print("click_local", json.dumps({k: r.get(k) for k in ("ok", "screen_xy", "error", "matched")}, ensure_ascii=False, default=str)[:500])
    time.sleep(1.0)
    after = _ocr_texts(recognize_dialog(client=c))
    print("after_has_edit", "编辑区" in after)
    print("after", json.dumps(after, ensure_ascii=False))

    # switch back
    r2 = click_dialog_button("全局蒙皮", client=c)
    print("click_global", r2.get("ok"), r2.get("screen_xy"))
    time.sleep(1.0)
    back = _ocr_texts(recognize_dialog(client=c))
    print("back_has_edit", "编辑区" in back)
    print("back", json.dumps(back, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
