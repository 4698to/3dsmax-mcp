# -*- coding: utf-8 -*-
"""Try absolute mouse_event click on 局部蒙皮."""
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
    _exec_ms,
    box_center,
    find_matching_line,
    image_to_screen,
    recognize_dialog,
)
from dialog_monitor.goskin_flow import _ocr_texts

TEMPLATE = (ROOT / "dialog_monitor" / "_click_sendinput.ms.in").read_text(encoding="utf-8")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    c = MaxClient(host="192.168.139.45", port=8765, transport="tcp", timeout=60.0)
    ensure_dialog_monitor_loaded(c)

    before = recognize_dialog(client=c)
    texts = _ocr_texts(before)
    print("before_edit", "编辑区" in texts)
    ln = find_matching_line(before["ocr_lines"], "局部蒙皮")
    center = box_center(ln["box"])
    cap = before["capture"]
    sx, sy = image_to_screen(
        center[0],
        center[1],
        screen_rect=cap["screen_rect"],
        image_width=int(cap["image_width"]),
        image_height=int(cap["image_height"]),
    )
    print("target", sx, sy, "hwnd", cap.get("hwnd"))

    code = (
        TEMPLATE.replace("__MCP_CLICK_X__", str(int(sx)))
        .replace("__MCP_CLICK_Y__", str(int(sy)))
        .replace("__MCP_CLICK_HWND__", str(int(cap.get("hwnd") or 0)))
    )
    raw = _exec_ms(c, code, timeout=30)
    print("abs_click", raw)
    time.sleep(1.0)
    after = _ocr_texts(recognize_dialog(client=c))
    print("after_edit", "编辑区" in after)
    print("after", json.dumps(after, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
