# -*- coding: utf-8 -*-
"""Compare window outer vs client rect vs snapshot size; try offset click."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from maxmcp.max_client import MaxClient
from dialog_monitor.click_button import (
    ensure_dialog_monitor_loaded,
    _exec_ms,
    capture_dialog_meta,
    click_at_screen,
    click_button_on_same_row,
    find_matching_line,
    recognize_dialog,
    resolve_capture_image,
)
from dialog_monitor.goskin_flow import _ocr_texts, _parse_list_counts, _select_objects_via_max


CLIENT_RECT_MS = r"""(
local hwnd = %d
local src = "using System; using System.Runtime.InteropServices; public class McpRectProbe { [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left; public int Top; public int Right; public int Bottom; } [DllImport(\"user32.dll\")] public static extern bool GetClientRect(IntPtr hWnd, out RECT lpRect); [DllImport(\"user32.dll\")] public static extern bool ClientToScreen(IntPtr hWnd, ref POINT lpPoint); [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X; public int Y; } public static string Info(long h) { RECT r; GetClientRect((IntPtr)h, out r); POINT p; p.X=0; p.Y=0; ClientToScreen((IntPtr)h, ref p); return p.X.ToString()+\",\"+p.Y.ToString()+\",\"+(r.Right-r.Left).ToString()+\",\"+(r.Bottom-r.Top).ToString(); } }"
local ok = true
try (dotNetClass "McpRectProbe") catch (
  local provider = dotNetObject "Microsoft.CSharp.CSharpCodeProvider"
  local parms = dotNetObject "System.CodeDom.Compiler.CompilerParameters"
  parms.GenerateInMemory = true
  parms.ReferencedAssemblies.Add "System.dll"
  local results = provider.CompileAssemblyFromSource parms #(src)
  if results.Errors.HasErrors then ok = false
)
if not ok then "ERR compile"
else (
  local cls = dotNetClass "McpRectProbe"
  cls.Info (hwnd as integer64)
)
)"""


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    c = MaxClient(host="192.168.139.45", port=8765, transport="tcp", timeout=60.0)
    ensure_dialog_monitor_loaded(c)

    cap = capture_dialog_meta(client=c)
    print("capture", json.dumps({k: cap.get(k) for k in ("ok", "hwnd", "screen_rect", "image_width", "image_height", "file")}, ensure_ascii=False))
    hwnd = int(cap["hwnd"])
    client_info = _exec_ms(c, CLIENT_RECT_MS % hwnd, timeout=30).strip().strip('"')
    print("client_rect", client_info)

    # Try mapping via client rect if available
    parts = client_info.split(",")
    if len(parts) == 4 and all(p.lstrip("-").isdigit() for p in parts):
        cx0, cy0, cw, ch = map(int, parts)
        print("client", cx0, cy0, cw, ch)

    _select_objects_via_max(c, ["Box001"])
    rec = recognize_dialog(client=c)
    lines = rec.get("ocr_lines") or []
    btn = find_matching_line(lines, "选定")
    # Prefer the one near 在场景中选择模型
    row = find_matching_line(lines, "在场景中选择模型")
    print("row", row)
    # gather all 选定
    selected = [ln for ln in lines if "选定" in str(ln.get("text") or "")]
    print("xuan_buttons", json.dumps(selected, ensure_ascii=False))

    if len(parts) == 4 and selected:
        cx0, cy0, cw, ch = map(int, parts)
        iw = int(rec["capture"]["image_width"])
        ih = int(rec["capture"]["image_height"])
        # Use first 选定 that is nearest row y — same as flow
        from dialog_monitor.click_button import _line_y_center, box_center

        row_y = _line_y_center(row) if row else None
        best = None
        best_dy = 1e9
        for ln in selected:
            cy = _line_y_center(ln)
            if cy is None or row_y is None:
                continue
            dy = abs(cy - row_y)
            if dy < best_dy:
                best_dy = dy
                best = ln
        center = box_center(best.get("box") or []) if best else None
        print("best", best, "center", center, "dy", best_dy)
        if center:
            # Map image -> client screen using client size (not outer geom)
            sx = int(round(cx0 + center[0] * cw / max(1, iw)))
            sy = int(round(cy0 + center[1] * ch / max(1, ih)))
            print("client_mapped_xy", sx, sy)
            clk = click_at_screen(sx, sy, hwnd=hwnd, client=c)
            print("click_client_map", clk)
            time.sleep(1.2)
            rec2 = recognize_dialog(client=c)
            texts = _ocr_texts(rec2)
            print("counts", _parse_list_counts(texts))
            print("texts", json.dumps(texts, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
