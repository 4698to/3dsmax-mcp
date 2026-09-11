# -*- coding: utf-8 -*-
"""Sweep Y offsets to find working click mapping for GoSkin tabs."""
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
    box_center,
    click_at_screen,
    find_matching_line,
    image_to_screen,
    recognize_dialog,
)
from dialog_monitor.goskin_flow import _ocr_texts


def has_global_skin(texts: list[str]) -> bool:
    return "编辑区" in texts and any("在场景中选择模型" in t for t in texts)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    c = MaxClient(host="192.168.139.45", port=8765, transport="tcp", timeout=60.0)
    ensure_dialog_monitor_loaded(c)

    for dy in (0, 8, 16, 24, 32, 40, -8, -16):
        rec = recognize_dialog(client=c)
        texts = _ocr_texts(rec)
        if not has_global_skin(texts):
            # try return to global first with same offset later
            print("not on global at start of dy", dy, texts[:15])
        cap = rec["capture"]
        ln = find_matching_line(rec["ocr_lines"], "局部蒙皮")
        if not ln:
            print("no 局部蒙皮", dy)
            continue
        center = box_center(ln["box"])
        sx, sy = image_to_screen(
            center[0],
            center[1],
            screen_rect=cap["screen_rect"],
            image_width=int(cap["image_width"]),
            image_height=int(cap["image_height"]),
        )
        sy2 = sy + dy
        clk = click_at_screen(sx, sy2, hwnd=int(cap.get("hwnd") or 0), client=c)
        time.sleep(0.9)
        after = _ocr_texts(recognize_dialog(client=c))
        changed = has_global_skin(texts) and not has_global_skin(after)
        print(
            json.dumps(
                {
                    "dy": dy,
                    "xy": [sx, sy2],
                    "click_ok": clk.get("ok"),
                    "before_global": has_global_skin(texts),
                    "after_global": has_global_skin(after),
                    "changed": changed,
                    "after_sample": after[:18],
                },
                ensure_ascii=False,
            )
        )
        if changed:
            # restore 全局蒙皮 with same offset
            rec2 = recognize_dialog(client=c)
            ln2 = find_matching_line(rec2["ocr_lines"], "全局蒙皮")
            if ln2:
                c2 = box_center(ln2["box"])
                sx2, syb = image_to_screen(
                    c2[0],
                    c2[1],
                    screen_rect=rec2["capture"]["screen_rect"],
                    image_width=int(rec2["capture"]["image_width"]),
                    image_height=int(rec2["capture"]["image_height"]),
                )
                click_at_screen(sx2, syb + dy, hwnd=int(rec2["capture"].get("hwnd") or 0), client=c)
                time.sleep(0.8)
            print("FOUND_WORKING_DY", dy)
            return 0
    print("no working dy found")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
