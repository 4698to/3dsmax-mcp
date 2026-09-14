# -*- coding: utf-8 -*-
"""Lightweight TCP stability probe against local Max (no dialog monitor)."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from maxmcp.max_client import MaxClient

HOST = "127.0.0.1"
PORT = 8765

PROBES = [
    ("ping_type", "ping", None),
    ("string", "maxscript", '"ok"'),
    ("math", "maxscript", "(1+1)"),
    ("objects_count", "maxscript", "(objects.count as string)"),
    ("selection_count", "maxscript", "(selection.count as string)"),
    ("max_version", "maxscript", '((maxVersion())[1] as string)'),
    ("scene_manage", "maxscript", '(if MCP_SceneManage != undefined then "scene-ok" else "scene-missing")'),
    ("dialog_monitor_loaded", "maxscript", '(if MCP_DialogMonitor != undefined then "dm-ok" else "dm-missing")'),
    ("get_desktop_hwnd", "maxscript", '((windows.getDesktopHWND()) as string)'),
    ("get_max_hwnd", "maxscript", '((windows.getMAXHWND()) as string)'),
]


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    client = MaxClient(host=HOST, port=PORT, transport="tcp", timeout=10.0)
    results = []
    print(f"probing {HOST}:{PORT} ...")
    for name, cmd_type, code in PROBES:
        t0 = time.time()
        try:
            if cmd_type == "ping":
                resp = client.send_command("ping", cmd_type="ping", timeout=8)
            else:
                resp = client.send_command(code, cmd_type="maxscript", timeout=8)
            ms = int((time.time() - t0) * 1000)
            row = {
                "name": name,
                "ok": True,
                "ms": ms,
                "result": resp.get("result"),
                "error": resp.get("error"),
                "success": resp.get("success"),
            }
            print(f"OK  {name:24s} {ms:4d}ms  result={row['result']!r} error={row['error']!r}")
        except Exception as exc:
            ms = int((time.time() - t0) * 1000)
            row = {"name": name, "ok": False, "ms": ms, "error": f"{type(exc).__name__}: {exc}"}
            print(f"FAIL {name:24s} {ms:4d}ms  {row['error']}")
            results.append(row)
            out = Path(__file__).with_name("_last_tcp_stability.json")
            out.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            print("wrote", out)
            print("STOPPED after first failure (Max may have crashed)")
            return 1
        results.append(row)
        time.sleep(0.15)

    # Crash-risk probes (only if dialog monitor already loaded — do NOT fileIn).
    dm_loaded = any(
        r.get("name") == "dialog_monitor_loaded"
        and str(r.get("result", "")).strip().strip('"') == "dm-ok"
        for r in results
    )
    extra = []
    if dm_loaded:
        extra = [
            (
                "children_desktop_count",
                r'''(
try (
  local kids = windows.getChildrenHWND (windows.getDesktopHWND())
  if kids == undefined then "kids=undefined" else ("kids=" + (kids.count as string))
) catch ("EX:" + (getCurrentException() as string))
)''',
            ),
            (
                "findDialog_safe",
                r'''(
try (
  local matches = MCP_DialogMonitor.findDialog visibleOnly:true parentType:"desktop"
  "count=" + (matches.count as string) + " title=" + (MCP_DialogMonitor.lastTitle as string)
) catch ("EX:" + (getCurrentException() as string))
)''',
            ),
            (
                "dialog_exists_style",
                r'''(
try (
  MCP_DialogMonitor.titlePattern = "自动蒙皮4.*"
  MCP_DialogMonitor.vendorPattern = "*天晴数码"
  MCP_DialogMonitor.requireVendor = true
  local matches = MCP_DialogMonitor.findDialog visibleOnly:true parentType:"desktop"
  if matches.count == 0 then "NONE"
  else (
    local hwnd = MCP_DialogMonitor.asHwndInt (matches[1][1])
    local dlgTitle = MCP_DialogMonitor.normalizeTitle (matches[1][5])
    "OK|" + (hwnd as string) + "|" + dlgTitle + "|" + (matches.count as string)
  )
) catch ("EX:" + (getCurrentException() as string))
)''',
            ),
        ]
    for name, code in extra:
        t0 = time.time()
        try:
            resp = client.send_command(code, cmd_type="maxscript", timeout=15)
            ms = int((time.time() - t0) * 1000)
            row = {
                "name": name,
                "ok": True,
                "ms": ms,
                "result": resp.get("result"),
                "error": resp.get("error"),
            }
            print(f"OK  {name:24s} {ms:4d}ms  result={row['result']!r}")
            results.append(row)
        except Exception as exc:
            ms = int((time.time() - t0) * 1000)
            row = {"name": name, "ok": False, "ms": ms, "error": f"{type(exc).__name__}: {exc}"}
            print(f"FAIL {name:24s} {ms:4d}ms  {row['error']}")
            results.append(row)
            break
        time.sleep(0.15)

    out = Path(__file__).with_name("_last_tcp_stability.json")
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("wrote", out)
    failed = [r for r in results if not r.get("ok")]
    print(f"done: {len(results) - len(failed)}/{len(results)} ok")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
