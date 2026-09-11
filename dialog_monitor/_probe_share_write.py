# -*- coding: utf-8 -*-
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from maxmcp.max_client import MaxClient
from dialog_monitor.click_button import _exec_ms, ensure_dialog_monitor_loaded, _escape_ms_string
from maxmcp.workspace_config import get_shared_workspace, clear_workspace_cache, ensure_workspace_dir

clear_workspace_cache()
ws = get_shared_workspace()
print("shared", ws)
if ws is None:
    raise SystemExit("no shared workspace")
ensure_workspace_dir(ws)
probe = (ws / "_probe_max.txt").as_posix()
print("probe path", probe)

client = MaxClient(host="192.168.139.45", port=8765, transport="tcp", timeout=25.0)
ensure_dialog_monitor_loaded(client)
esc = _escape_ms_string(probe)
code = f'''(
local p = @"{esc}"
try (
  makeDir (getFilenamePath p) all:true
  local f = createFile p
  if f == undefined then "createFile undefined"
  else (
    format "ok from max\\n" to:f
    close f
    if doesFileExist p then ("wrote " + p) else "missing after write"
  )
) catch ("ERR " + (getCurrentException() as string))
)'''
print("write probe:", _exec_ms(client, code, timeout=25.0))
print("local exists", Path(probe).exists())
