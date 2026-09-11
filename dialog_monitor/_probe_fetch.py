# -*- coding: utf-8 -*-
"""Safe stepwise probe: capture -> size -> fetch -> OCR texts only."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from maxmcp.max_client import MaxClient
from dialog_monitor.click_button import (
    capture_dialog_meta,
    ensure_dialog_monitor_loaded,
    fetch_file_bytes_via_max,
    recognize_dialog,
)
from dialog_monitor.ocr_client import recognize


def main() -> int:
    c = MaxClient(host="192.168.139.45", port=8765, transport="tcp")
    ensure_dialog_monitor_loaded(c)

    t0 = time.time()
    cap = capture_dialog_meta(client=c)
    print("capture", round(time.time() - t0, 2), "s")
    print(
        json.dumps(
            {
                k: cap.get(k)
                for k in (
                    "ok",
                    "file",
                    "title",
                    "image_width",
                    "image_height",
                    "error",
                )
            },
            ensure_ascii=False,
        )
    )
    if not cap.get("ok"):
        return 1

    t0 = time.time()
    data = fetch_file_bytes_via_max(c, str(cap["file"]), timeout=20.0, chunk_size=12_000)
    print("fetched", len(data), "bytes in", round(time.time() - t0, 2), "s")
    # magic
    print("magic", data[:8])

    t0 = time.time()
    lines = recognize(data, mime="image/png")
    print("ocr", len(lines), "lines in", round(time.time() - t0, 2), "s")
    texts = [ln.get("text") for ln in lines][:50]
    print(json.dumps(texts, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
