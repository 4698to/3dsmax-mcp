# -*- coding: utf-8 -*-
"""Step probe for remote 自动蒙皮 capture."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from maxmcp.max_client import MaxClient
from dialog_monitor.click_button import ensure_dialog_monitor_loaded, _exec_ms, _escape_ms_string


def main() -> int:
    client = MaxClient(host="192.168.139.45", port=8765, transport="tcp", timeout=30.0)
    print("1 ping", client.send_command("1", cmd_type="ping").get("result", "")[:200])

    ensure_dialog_monitor_loaded(client, force=True)
    print("2 monitor ok")

    # Set patterns + find only
    code = """(
MCP_DialogMonitor.titlePattern = "自动蒙皮4.*"
MCP_DialogMonitor.vendorPattern = "*天晴数码"
MCP_DialogMonitor.requireVendor = true
local m = MCP_DialogMonitor.findDialog()
"count=" + (m.count as string) + " title=" + MCP_DialogMonitor.lastTitle + " hwnd=" + (MCP_DialogMonitor.lastHwnd as string)
)"""
    print("3 find", _exec_ms(client, code, timeout=30.0))

    # Capture to Max local temp (no shared path)
    code2 = """(
MCP_DialogMonitor.titlePattern = "自动蒙皮4.*"
MCP_DialogMonitor.vendorPattern = "*天晴数码"
MCP_DialogMonitor.requireVendor = true
MCP_DialogMonitor.captureDialogMeta()
)"""
    print("4 capture local temp...")
    raw = _exec_ms(client, code2, timeout=60.0)
    print("4 result", raw[:500] if isinstance(raw, str) else raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
