# -*- coding: utf-8 -*-
"""Reload MCP_SceneManage then run_goskin_skin via local unhidden handles."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from maxmcp.max_client import MaxClient
from dialog_monitor._run_goskin_local_unhidden import main as run_main

HOST = "127.0.0.1"
PORT = 8765


def main() -> int:
    client = MaxClient(host=HOST, port=PORT, transport="tcp", timeout=20.0)
    ping = client.send_command("ping", cmd_type="ping", timeout=8)
    print("ping", bool(ping.get("result")))
    reload_code = (
        '(fileIn ((getDir #scripts) + "/mcp/mcp_SceneManage.ms"); '
        'if MCP_SceneManage != undefined then "sm-ok" else "sm-missing")'
    )
    rel = client.send_command(reload_code, cmd_type="maxscript", timeout=30)
    print("reload", rel.get("result"), rel.get("error"))
    if "sm-ok" not in str(rel.get("result", "")):
        return 1
    return run_main(["--skin"])


if __name__ == "__main__":
    raise SystemExit(main())
