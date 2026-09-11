# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from maxmcp.max_client import MaxClient
from dialog_monitor.click_button import ensure_dialog_monitor_loaded, _exec_ms

CODE = r'''(
local s = ""
try (
  local u = dotNetClass "System.Environment"
  s += "UserName=" + u.UserName + "\n"
  s += "UserInteractive=" + (u.UserInteractive as string) + "\n"
) catch ()
try (s += "desktopSize=" + (sysInfo.desktopSize as string) + "\n") catch ()
try (
  local fg = windows.getMAXHWND()
  s += "maxHwnd=" + (fg as string) + "\n"
) catch ()
s
)'''


def main() -> int:
    c = MaxClient(host="192.168.139.45", port=8765, transport="tcp", timeout=30.0)
    ensure_dialog_monitor_loaded(c)
    print(_exec_ms(c, CODE, timeout=20))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
