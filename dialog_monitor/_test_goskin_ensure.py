# -*- coding: utf-8 -*-
"""Smoke: ensure_goskin_ready against local Max (fallback remote)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from maxmcp.max_client import MaxClient
from dialog_monitor.goskin_flow import ensure_goskin_ready


def _client() -> MaxClient:
    for host in ("127.0.0.1", "192.168.139.45"):
        c = MaxClient(host=host, port=8765, transport="tcp")
        try:
            c.send_command('"pong"', cmd_type="maxscript", timeout=4)
            print("using", host)
            return c
        except Exception as exc:
            print("skip", host, type(exc).__name__)
    raise SystemExit("no Max bridge")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    client = _client()
    result = ensure_goskin_ready(client, open_wait_s=10.0)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    out = Path(__file__).with_name("_last_goskin_ensure.json")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("wrote", out)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
