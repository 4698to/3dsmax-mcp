# -*- coding: utf-8 -*-
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
    click_button_on_same_row,
    recognize_dialog,
)
from dialog_monitor.goskin_flow import _ocr_texts, _parse_list_counts, _select_objects_via_max


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    c = MaxClient(host="192.168.139.45", port=8765, transport="tcp", timeout=60.0)
    ensure_dialog_monitor_loaded(c)
    print("ping", c.send_command('"pong"', cmd_type="maxscript", timeout=10).get("result"))

    sel = _select_objects_via_max(c, ["Box001"])
    print("sel", json.dumps(sel, ensure_ascii=False))
    print("count", _exec_ms(c, "(selection.count as string)", timeout=10))

    r = click_button_on_same_row("在场景中选择模型", "选定", client=c)
    brief = {
        "ok": r.get("ok"),
        "screen_xy": r.get("screen_xy"),
        "image_xy": r.get("image_xy"),
        "matched_row": (r.get("matched_row") or {}).get("text"),
        "matched": (r.get("matched") or {}).get("text"),
        "click": r.get("click"),
        "error": r.get("error"),
        "capture_rect": (r.get("capture") or {}).get("screen_rect"),
        "image_size": [
            (r.get("capture") or {}).get("image_width"),
            (r.get("capture") or {}).get("image_height"),
        ],
    }
    print("click", json.dumps(brief, ensure_ascii=False, indent=2))

    time.sleep(1.5)
    rec = recognize_dialog(client=c)
    texts = _ocr_texts(rec)
    print("counts", _parse_list_counts(texts))
    print("texts", json.dumps(texts, ensure_ascii=False))
    print("count_after", _exec_ms(c, "(selection.count as string)", timeout=10))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
