# -*- coding: utf-8 -*-
"""Minimal Max ops: capture once, Python fetch/OCR, then clicks."""
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
    click_ocr_in_capture,
    ensure_dialog_monitor_loaded,
    resolve_capture_image,
)
from dialog_monitor.ocr_client import recognize


def _brief(step: str, result: dict) -> dict:
    return {
        "step": step,
        "ok": result.get("ok"),
        "error": result.get("error"),
        "matched": (result.get("matched") or {}).get("text")
        if isinstance(result.get("matched"), dict)
        else result.get("matched"),
        "screen_xy": result.get("screen_xy"),
        "fetched_via": (result.get("capture") or {}).get("fetched_via"),
        "title": (result.get("capture") or {}).get("title"),
    }


def _capture_and_ocr(client) -> dict:
    cap = capture_dialog_meta(client=client)
    if not cap.get("ok"):
        return {"ok": False, "capture": cap, "error": cap.get("error", "capture failed")}
    image = resolve_capture_image(client, cap)
    path_l = str(cap.get("file") or "").lower()
    mime = "image/jpeg" if path_l.endswith((".jpg", ".jpeg")) else "image/png"
    lines = recognize(image, mime=mime)
    return {"ok": True, "capture": cap, "ocr_lines": lines, "image_bytes": len(image)}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    client = MaxClient(host="127.0.0.1", port=8765, transport="tcp")
    ensure_dialog_monitor_loaded(client)

    print("--- capture+ocr ---")
    listed = _capture_and_ocr(client)
    texts = [ln.get("text") for ln in (listed.get("ocr_lines") or [])][:40]
    print(
        json.dumps(
            {
                **_brief("recognize", listed),
                "image_bytes": listed.get("image_bytes"),
                "ocr_texts": texts,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if not listed.get("ok"):
        return 1

    print("--- click 蒙皮 ---")
    step1 = click_ocr_in_capture(
        "蒙皮",
        listed["capture"],
        client=client,
        ocr_lines=listed["ocr_lines"],
    )
    print(json.dumps(_brief("蒙皮", step1), ensure_ascii=False, indent=2))
    if not step1.get("ok"):
        return 1

    time.sleep(1.0)

    print("--- capture+ocr (after tab) ---")
    listed2 = _capture_and_ocr(client)
    texts2 = [ln.get("text") for ln in (listed2.get("ocr_lines") or [])][:40]
    print(
        json.dumps(
            {
                **_brief("recognize2", listed2),
                "image_bytes": listed2.get("image_bytes"),
                "ocr_texts": texts2,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if not listed2.get("ok"):
        return 1

    print("--- click 面部蒙皮 ---")
    step2 = click_ocr_in_capture(
        "面部蒙皮",
        listed2["capture"],
        client=client,
        ocr_lines=listed2["ocr_lines"],
    )
    print(json.dumps(_brief("面部蒙皮", step2), ensure_ascii=False, indent=2))

    out = ROOT / "dialog_monitor" / "_last_goskin_tabs.json"
    payload = {
        "蒙皮": _brief("蒙皮", step1),
        "面部蒙皮": _brief("面部蒙皮", step2),
        "ocr_texts": texts,
        "ocr_texts2": texts2,
        "fetched_via": listed.get("capture", {}).get("fetched_via"),
        "fetched_via2": listed2.get("capture", {}).get("fetched_via"),
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("full ->", out)
    return 0 if step2.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
