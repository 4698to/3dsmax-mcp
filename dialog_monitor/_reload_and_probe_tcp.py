# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from maxmcp.max_client import MaxClient
from dialog_monitor._probe_tcp_stability import main as probe_main

HOST = "127.0.0.1"
PORT = 8765


def main() -> int:
    c = MaxClient(host=HOST, port=PORT, transport="tcp", timeout=20.0)
    ping = c.send_command("ping", cmd_type="ping", timeout=8)
    print("ping_ok", bool(ping.get("result")))
    code = (
        '(fileIn ((getDir #scripts) + "/mcp/mcp_dialog_monitor.ms"); '
        'if MCP_DialogMonitor != undefined then "dm-reloaded" else "dm-missing")'
    )
    rel = c.send_command(code, cmd_type="maxscript", timeout=30)
    print("reload", rel.get("result"), rel.get("error"))
    if "dm-reloaded" not in str(rel.get("result", "")):
        return 1
    return probe_main()


if __name__ == "__main__":
    raise SystemExit(main())
