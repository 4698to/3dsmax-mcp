# -*- coding: utf-8 -*-
"""Fix: map OCR image coords via client rect, not outer window rect."""
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
    box_center,
    click_at_screen,
    find_matching_line,
    recognize_dialog,
)
from dialog_monitor.goskin_flow import _ocr_texts, _parse_list_counts, _select_objects_via_max

# Compile helper once; returns "ox,oy,cw,ch" for client origin+size
CLIENT_MS_TMPL = r'''(
local hwnd = __HWND__
fn ensure = (
  try (if (dotNetClass "McpClientRect3") != undefined do return true) catch()
  local source = ""
  source += "using System; using System.Runtime.InteropServices;\n"
  source += "public class McpClientRect3 {\n"
  source += "  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L; public int T; public int R; public int B; }\n"
  source += "  [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X; public int Y; }\n"
  source += "  [DllImport(\"user32.dll\")] static extern bool GetClientRect(IntPtr h, out RECT r);\n"
  source += "  [DllImport(\"user32.dll\")] static extern bool ClientToScreen(IntPtr h, ref POINT p);\n"
  source += "  public static string Info(long h) {\n"
  source += "    RECT r; GetClientRect((IntPtr)h, out r);\n"
  source += "    POINT p; p.X=0; p.Y=0; ClientToScreen((IntPtr)h, ref p);\n"
  source += "    return p.X+\",\"+p.Y+\",\"+(r.R-r.L)+\",\"+(r.B-r.T);\n"
  source += "  }\n"
  source += "}\n"
  local provider = dotNetObject "Microsoft.CSharp.CSharpCodeProvider"
  local parms = dotNetObject "System.CodeDom.Compiler.CompilerParameters"
  parms.GenerateInMemory = true
  parms.ReferencedAssemblies.Add "System.dll"
  local results = provider.CompileAssemblyFromSource parms #(source)
  if results.Errors.HasErrors then (
    local msg = ""
    for i = 0 to (results.Errors.Count - 1) do msg += (results.Errors.Item[i].ErrorText + "; ")
    format "McpClientRect3 compile fail: %\n" msg
    false
  ) else true
)
if not (ensure()) then "ERROR"
else ((dotNetClass "McpClientRect3").Info (hwnd as integer64))
)'''


def client_rect_code(hwnd: int) -> str:
    return CLIENT_MS_TMPL.replace("__HWND__", str(int(hwnd)))


def client_map(ix: float, iy: float, ox: int, oy: int, cw: int, ch: int, iw: int, ih: int) -> tuple[int, int]:
    sx = int(round(ox + ix * cw / max(1, iw)))
    sy = int(round(oy + iy * ch / max(1, ih)))
    return sx, sy


def has_global(texts: list[str]) -> bool:
    return "编辑区" in texts and any("在场景中选择模型" in t for t in texts)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    c = MaxClient(host="192.168.139.45", port=8765, transport="tcp", timeout=60.0)
    ensure_dialog_monitor_loaded(c)

    before = recognize_dialog(client=c)
    cap = before["capture"]
    hwnd = int(cap["hwnd"])
    info = _exec_ms(c, client_rect_code(hwnd), timeout=40).strip().strip('"')
    print("client_info", info)
    print("outer", cap.get("screen_rect"), "image", cap.get("image_width"), cap.get("image_height"))
    ox, oy, cw, ch = map(int, info.split(","))
    iw, ih = int(cap["image_width"]), int(cap["image_height"])

    ln = find_matching_line(before["ocr_lines"], "局部蒙皮")
    center = box_center(ln["box"])
    sx, sy = client_map(center[0], center[1], ox, oy, cw, ch, iw, ih)
    print("tab_target_client_map", center, "->", sx, sy)

    texts0 = _ocr_texts(before)
    print("before_global", has_global(texts0))
    clk = click_at_screen(sx, sy, hwnd=hwnd, client=c)
    print("click", clk)
    time.sleep(1.0)
    after = _ocr_texts(recognize_dialog(client=c))
    print("after_global", has_global(after), after[:18])

    if has_global(texts0) and not has_global(after):
        print("TAB_CLICK_OK with client mapping")
        # restore global
        rec2 = recognize_dialog(client=c)
        ln2 = find_matching_line(rec2["ocr_lines"], "全局蒙皮")
        c2 = box_center(ln2["box"])
        info2 = _exec_ms(c, client_rect_code(int(rec2["capture"]["hwnd"])), timeout=40).strip().strip('"')
        ox2, oy2, cw2, ch2 = map(int, info2.split(","))
        sx2, sy2 = client_map(
            c2[0], c2[1], ox2, oy2, cw2, ch2,
            int(rec2["capture"]["image_width"]), int(rec2["capture"]["image_height"]),
        )
        click_at_screen(sx2, sy2, hwnd=int(rec2["capture"]["hwnd"]), client=c)
        time.sleep(0.8)

    # mesh 选定 with client mapping
    _select_objects_via_max(c, ["Box001"])
    rec = recognize_dialog(client=c)
    cap = rec["capture"]
    hwnd = int(cap["hwnd"])
    info = _exec_ms(c, client_rect_code(hwnd), timeout=40).strip().strip('"')
    ox, oy, cw, ch = map(int, info.split(","))
    iw, ih = int(cap["image_width"]), int(cap["image_height"])
    from dialog_monitor.click_button import _line_y_center

    row = find_matching_line(rec["ocr_lines"], "在场景中选择模型")
    row_y = _line_y_center(row)
    best = None
    best_dy = 1e9
    for ln3 in rec["ocr_lines"]:
        if str(ln3.get("text") or "").strip() != "选定" and "选定" not in str(ln3.get("text") or ""):
            continue
        if "选定" not in str(ln3.get("text") or ""):
            continue
        cy = _line_y_center(ln3)
        if cy is None or row_y is None:
            continue
        dy = abs(cy - row_y)
        if dy < best_dy:
            best_dy = dy
            best = ln3
    ctr = box_center(best["box"])
    sx, sy = client_map(ctr[0], ctr[1], ox, oy, cw, ch, iw, ih)
    print("xuan_target", ctr, "->", sx, sy, "dy", best_dy)
    print("click_xuan", click_at_screen(sx, sy, hwnd=hwnd, client=c))
    time.sleep(1.2)
    texts = _ocr_texts(recognize_dialog(client=c))
    print("counts", _parse_list_counts(texts))
    print("texts", json.dumps(texts, ensure_ascii=False))
    return 0 if (_parse_list_counts(texts).get("mesh") or 0) >= 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
