# -*- coding: utf-8 -*-
"""Probe PostMessage / SendInput clicks against GoSkin dialog."""
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
    find_matching_line,
    image_to_screen,
    recognize_dialog,
)
from dialog_monitor.goskin_flow import _ocr_texts, _parse_list_counts, _select_objects_via_max

# One-shot C# helper: PostMessage + SendInput click at screen xy on hwnd
CLICK_MS = r'''(
local sx = %d
local sy = %d
local hwnd = %d
local mode = "%s"
local source = ""
source += "using System;\n"
source += "using System.Runtime.InteropServices;\n"
source += "public class McpClickProbe2 {\n"
source += "  [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X; public int Y; }\n"
source += "  [StructLayout(LayoutKind.Sequential)] public struct INPUT { public uint type; public MOUSEINPUT mi; }\n"
source += "  [StructLayout(LayoutKind.Sequential)] public struct MOUSEINPUT { public int dx; public int dy; public uint mouseData; public uint dwFlags; public uint time; public IntPtr dwExtraInfo; }\n"
source += "  [DllImport(\"user32.dll\")] static extern bool ScreenToClient(IntPtr hWnd, ref POINT lpPoint);\n"
source += "  [DllImport(\"user32.dll\")] static extern bool PostMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);\n"
source += "  [DllImport(\"user32.dll\")] static extern bool SendMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);\n"
source += "  [DllImport(\"user32.dll\")] static extern uint SendInput(uint n, INPUT[] p, int cb);\n"
source += "  [DllImport(\"user32.dll\")] static extern bool SetCursorPos(int X, int Y);\n"
source += "  [DllImport(\"user32.dll\")] static extern bool SetForegroundWindow(IntPtr hWnd);\n"
source += "  [DllImport(\"user32.dll\")] static extern IntPtr WindowFromPoint(POINT p);\n"
source += "  const uint WM_MOUSEMOVE=0x0200, WM_LBUTTONDOWN=0x0201, WM_LBUTTONUP=0x0202;\n"
source += "  const uint MK_LBUTTON=0x0001;\n"
source += "  const uint INPUT_MOUSE=0, MOUSEEVENTF_MOVE=0x0001, MOUSEEVENTF_LEFTDOWN=0x0002, MOUSEEVENTF_LEFTUP=0x0004, MOUSEEVENTF_ABSOLUTE=0x8000, MOUSEEVENTF_VIRTUALDESK=0x4000;\n"
source += "  [DllImport(\"user32.dll\")] static extern int GetSystemMetrics(int n);\n"
source += "  static IntPtr Pack(int x, int y) { return (IntPtr)((y << 16) | (x & 0xFFFF)); }\n"
source += "  public static string Click(long h, int sx, int sy, string mode) {\n"
source += "    IntPtr hwnd = (IntPtr)h;\n"
source += "    SetForegroundWindow(hwnd);\n"
source += "    POINT pt; pt.X=sx; pt.Y=sy;\n"
source += "    IntPtr hit = WindowFromPoint(pt);\n"
source += "    POINT cpt; cpt.X=sx; cpt.Y=sy; ScreenToClient(hwnd, ref cpt);\n"
source += "    if (mode == \"post\" || mode == \"both\") {\n"
source += "      PostMessage(hwnd, WM_MOUSEMOVE, IntPtr.Zero, Pack(cpt.X, cpt.Y));\n"
source += "      PostMessage(hwnd, WM_LBUTTONDOWN, (IntPtr)MK_LBUTTON, Pack(cpt.X, cpt.Y));\n"
source += "      PostMessage(hwnd, WM_LBUTTONUP, IntPtr.Zero, Pack(cpt.X, cpt.Y));\n"
source += "      if (hit != IntPtr.Zero && hit != hwnd) {\n"
source += "        POINT hpt; hpt.X=sx; hpt.Y=sy; ScreenToClient(hit, ref hpt);\n"
source += "        PostMessage(hit, WM_MOUSEMOVE, IntPtr.Zero, Pack(hpt.X, hpt.Y));\n"
source += "        PostMessage(hit, WM_LBUTTONDOWN, (IntPtr)MK_LBUTTON, Pack(hpt.X, hpt.Y));\n"
source += "        PostMessage(hit, WM_LBUTTONUP, IntPtr.Zero, Pack(hpt.X, hpt.Y));\n"
source += "      }\n"
source += "    }\n"
source += "    if (mode == \"sendmsg\" || mode == \"both\") {\n"
source += "      SendMessage(hwnd, WM_MOUSEMOVE, IntPtr.Zero, Pack(cpt.X, cpt.Y));\n"
source += "      SendMessage(hwnd, WM_LBUTTONDOWN, (IntPtr)MK_LBUTTON, Pack(cpt.X, cpt.Y));\n"
source += "      SendMessage(hwnd, WM_LBUTTONUP, IntPtr.Zero, Pack(cpt.X, cpt.Y));\n"
source += "    }\n"
source += "    if (mode == \"sendinput\" || mode == \"both\") {\n"
source += "      int vx=GetSystemMetrics(76), vy=GetSystemMetrics(77), vw=GetSystemMetrics(78), vh=GetSystemMetrics(79);\n"
source += "      if (vw<=1) { vw=GetSystemMetrics(0); vh=GetSystemMetrics(1); vx=0; vy=0; }\n"
source += "      int ax=(int)Math.Round((sx-vx)*65535.0/Math.Max(1,vw-1));\n"
source += "      int ay=(int)Math.Round((sy-vy)*65535.0/Math.Max(1,vh-1));\n"
source += "      SetCursorPos(sx, sy);\n"
source += "      INPUT[] arr = new INPUT[3];\n"
source += "      arr[0].type=INPUT_MOUSE; arr[0].mi.dx=ax; arr[0].mi.dy=ay; arr[0].mi.dwFlags=MOUSEEVENTF_MOVE|MOUSEEVENTF_ABSOLUTE|MOUSEEVENTF_VIRTUALDESK;\n"
source += "      arr[1].type=INPUT_MOUSE; arr[1].mi.dx=ax; arr[1].mi.dy=ay; arr[1].mi.dwFlags=MOUSEEVENTF_LEFTDOWN|MOUSEEVENTF_ABSOLUTE|MOUSEEVENTF_VIRTUALDESK;\n"
source += "      arr[2].type=INPUT_MOUSE; arr[2].mi.dx=ax; arr[2].mi.dy=ay; arr[2].mi.dwFlags=MOUSEEVENTF_LEFTUP|MOUSEEVENTF_ABSOLUTE|MOUSEEVENTF_VIRTUALDESK;\n"
source += "      SendInput(3, arr, Marshal.SizeOf(typeof(INPUT)));\n"
source += "    }\n"
source += "    return \"ok mode=\"+mode+\" client=\"+cpt.X+\",\"+cpt.Y+\" hit=\"+hit.ToInt64();\n"
source += "  }\n"
source += "}\n"

fn ensureProbe = (
  try (if (dotNetClass "McpClickProbe2") != undefined do return true) catch ()
  local provider = dotNetObject "Microsoft.CSharp.CSharpCodeProvider"
  local parms = dotNetObject "System.CodeDom.Compiler.CompilerParameters"
  parms.GenerateInMemory = true
  parms.ReferencedAssemblies.Add "System.dll"
  local results = provider.CompileAssemblyFromSource parms #(source)
  if results.Errors.HasErrors then (
    local msg = ""
    for i = 0 to (results.Errors.Count - 1) do msg += (results.Errors.Item[i].ErrorText + "; ")
    format "compile fail: %%\n" msg
    return false
  )
  true
)
if not (ensureProbe()) then "ERROR compile"
else ((dotNetClass "McpClickProbe2").Click (hwnd as integer64) sx sy mode)
)'''


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

    for mode in ("post", "sendmsg", "sendinput", "both"):
        before = recognize_dialog(client=c)
        texts = _ocr_texts(before)
        if not has_global(texts):
            print("skip mode", mode, "not on global", texts[:12])
            continue
        ln = find_matching_line(before["ocr_lines"], "局部蒙皮")
        center = box_center(ln["box"])
        cap = before["capture"]
        sx, sy = image_to_screen(
            center[0], center[1],
            screen_rect=cap["screen_rect"],
            image_width=int(cap["image_width"]),
            image_height=int(cap["image_height"]),
        )
        raw = _exec_ms(c, CLICK_MS % (sx, sy, int(cap["hwnd"]), mode), timeout=40)
        print("mode", mode, "xy", sx, sy, "->", raw)
        time.sleep(1.0)
        after = _ocr_texts(recognize_dialog(client=c))
        changed = has_global(texts) and not has_global(after)
        print("changed", changed, "after_edit", "编辑区" in after, after[:16])
        if changed:
            # restore
            rec2 = recognize_dialog(client=c)
            ln2 = find_matching_line(rec2["ocr_lines"], "全局蒙皮")
            if ln2:
                c2 = box_center(ln2["box"])
                sx2, sy2 = image_to_screen(
                    c2[0], c2[1],
                    screen_rect=rec2["capture"]["screen_rect"],
                    image_width=int(rec2["capture"]["image_width"]),
                    image_height=int(rec2["capture"]["image_height"]),
                )
                _exec_ms(c, CLICK_MS % (sx2, sy2, int(rec2["capture"]["hwnd"]), mode), timeout=40)
                time.sleep(0.8)
            print("WORKING_MODE", mode)
            # also try mesh 选定
            _select_objects_via_max(c, ["Box001"])
            r = recognize_dialog(client=c)
            row = find_matching_line(r["ocr_lines"], "在场景中选择模型")
            from dialog_monitor.click_button import _line_y_center
            row_y = _line_y_center(row)
            best = None
            best_dy = 1e9
            for ln3 in r["ocr_lines"]:
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
            sx3, sy3 = image_to_screen(
                ctr[0], ctr[1],
                screen_rect=r["capture"]["screen_rect"],
                image_width=int(r["capture"]["image_width"]),
                image_height=int(r["capture"]["image_height"]),
            )
            print("xuan", _exec_ms(c, CLICK_MS % (sx3, sy3, int(r["capture"]["hwnd"]), mode), timeout=40))
            time.sleep(1.2)
            texts2 = _ocr_texts(recognize_dialog(client=c))
            print("counts", _parse_list_counts(texts2))
            print("texts", texts2)
            return 0

    print("no mode changed UI")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
