# -*- coding: utf-8 -*-
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from maxmcp.max_client import MaxClient
from dialog_monitor.click_button import _exec_ms, ensure_dialog_monitor_loaded, fetch_file_bytes_via_http_upload

c = MaxClient(host="192.168.139.45", port=8765, transport="tcp")
ensure_dialog_monitor_loaded(c)

code = r'''
(
MCP_DialogMonitor.titlePattern = "自动蒙皮4.*"
MCP_DialogMonitor.vendorPattern = "*天晴数码"
MCP_DialogMonitor.requireVendor = true
local matches = MCP_DialogMonitor.findDialog()
if matches.count == 0 then "ERROR: none"
else (
  local hwnd = MCP_DialogMonitor.asHwndInt matches[1][1]
  local geom = MCP_DialogMonitor.getWindowGeom hwnd
  local dir = (getDir #temp)
  dir = substituteString dir "\\" "/"
  if dir[dir.count] != "/" do dir += "/"
  local results = #()
  try (
    local pA = dir + "dm_A.png"
    local bmp = windows.snapshot hwnd
    bmp.filename = pA
    local ok = save bmp
    close bmp
    append results ("A ok=" + (ok as string) + " size=" + ((getFileSize pA) as string) + " path=" + pA)
  ) catch (append results ("AERR " + (getCurrentException() as string)))
  try (
    local pC = dir + "dm_C.png"
    local bmp = dotNetObject "System.Drawing.Bitmap" (geom[3] as integer) (geom[4] as integer)
    local gfx = (dotNetClass "System.Drawing.Graphics").FromImage bmp
    local size = dotNetObject "System.Drawing.Size" (geom[3] as integer) (geom[4] as integer)
    gfx.CopyFromScreen (geom[1] as integer) (geom[2] as integer) 0 0 size ((dotNetClass "System.Drawing.CopyPixelOperation").SourceCopy)
    gfx.Dispose()
    bmp.Save pC ((dotNetClass "System.Drawing.Imaging.ImageFormat").Png)
    bmp.Dispose()
    append results ("C size=" + ((getFileSize pC) as string) + " path=" + pC)
  ) catch (append results ("CERR " + (getCurrentException() as string)))
  try (
    local pD = dir + "dm_D.png"
    local bmp = dotNetObject "System.Drawing.Bitmap" 200 80
    local gfx = (dotNetClass "System.Drawing.Graphics").FromImage bmp
    local size = dotNetObject "System.Drawing.Size" 200 80
    gfx.CopyFromScreen 0 0 0 0 size
    gfx.Dispose()
    bmp.Save pD ((dotNetClass "System.Drawing.Imaging.ImageFormat").Png)
    bmp.Dispose()
    append results ("D size=" + ((getFileSize pD) as string) + " path=" + pD)
  ) catch (append results ("DERR " + (getCurrentException() as string)))
  local s = ""
  for r in results do s += r + " || "
  s + " geom=" + (geom as string)
)
)
'''
raw = _exec_ms(c, code, timeout=45)
print(raw)

# If any path succeeded, fetch and inspect uniqueness
import re
from PIL import Image
import io

for label in ("A", "C", "D"):
    m = re.search(rf"{label}.*?path=([^\s|]+)", raw)
    if not m:
        continue
    path = m.group(1).strip()
    try:
        data = fetch_file_bytes_via_http_upload(c, path, timeout=20)
    except Exception as exc:
        print(label, "fetch fail", exc)
        continue
    im = Image.open(io.BytesIO(data))
    sample = list(im.getdata())[:: max(1, im.size[0] * im.size[1] // 200)]
    print(label, "bytes", len(data), "size", im.size, "unique", len(set(sample)))
    Path(__file__).with_name(f"_probe_{label}.png").write_bytes(data)
