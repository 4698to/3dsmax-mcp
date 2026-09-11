"""Orchestrate dialog capture → OCR → screen click via MCP_DialogMonitor."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

from .ocr_client import DEFAULT_OCR_BASE, OcrError, health, recognize

_MODULE_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _MODULE_DIR.parent
_MS_PATH = _REPO_ROOT / "maxscript" / "mcp" / "mcp_dialog_monitor.ms"
if not _MS_PATH.is_file():
    _MS_PATH = _MODULE_DIR / "mcp_dialog_monitor.ms"


def _ms_path_literal(path: Path) -> str:
    return str(path).replace("\\", "/")


def _ensure_max_client(client: Any | None = None):
    if client is not None:
        return client
    # Allow running as `python -m dialog_monitor.click_button` from repo root
    repo_root = _MODULE_DIR.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from maxmcp.max_client import MaxClient

    return MaxClient()


def _exec_ms(client: Any, code: str, timeout: float | None = None) -> str:
    response = client.send_command(code, cmd_type="maxscript", timeout=timeout)
    result = response.get("result", "")
    if isinstance(result, str) and result.startswith("__MCP_MS_ERR__:"):
        raise RuntimeError(result[len("__MCP_MS_ERR__:") :].strip())
    return result if isinstance(result, str) else str(result)


def ensure_dialog_monitor_loaded(client: Any | None = None, *, force: bool = False) -> None:
    """Ensure MCP_DialogMonitor is loaded inside 3ds Max.

    Prefer the copy under Max ``scripts\\mcp\\`` (works for remote Max). Fall back
    to this repo's dialog_monitor.ms only when that file exists on the Max host.
    """
    client = _ensure_max_client(client)
    if not force:
        check = _exec_ms(
            client,
            '(if MCP_DialogMonitor == undefined then "missing" else "ok")',
        )
        if check.strip().strip('"') == "ok":
            return

    # Load from Max's scripts/mcp first (installed / autostart layout).
    load_code = r'''(
local loaded = false
local scriptDir = (getDir #scripts) + "\\mcp\\"
local candidates = #(
  scriptDir + "mcp_dialog_monitor.ms",
  scriptDir + "mcp_dialog_monitor.mse"
)
for f in candidates where (not loaded) and (doesFileExist f) do (
  fileIn f
  loaded = true
  format "MCP: fileIn dialog monitor from %\n" f
)
if (not loaded) and (MCP_DialogMonitor != undefined) then "ok"
else if loaded and (MCP_DialogMonitor != undefined) then "loaded"
else if MCP_DialogMonitor != undefined then "ok"
else "missing"
)'''
    result = _exec_ms(client, load_code).strip().strip('"')
    if result in {"ok", "loaded"}:
        return

    # Last resort: repo path (same-machine checkout only)
    path = _ms_path_literal(_MS_PATH)
    try:
        _exec_ms(client, f'if doesFileExist @"{path}" then (fileIn @"{path}"; "loaded") else "missing"')
    except Exception:
        pass
    check = _exec_ms(
        client,
        '(if MCP_DialogMonitor == undefined then "missing" else "ok")',
    )
    if check.strip().strip('"') != "ok":
        raise RuntimeError(
            "MCP_DialogMonitor not loaded in 3ds Max. "
            "Copy dialog_monitor/mcp_dialog_monitor.ms to scripts\\mcp\\ on the Max host "
            "(or enable mcp_autostart)."
        )


def _escape_ms_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def max_temp_capture_outfile(client: Any, *, prefix: str = "dialog_monitor") -> str:
    """ASCII path under Max getDir #temp — avoids CJK workspace paths that break save bmp."""
    import time

    raw = _exec_ms(
        client,
        r'''(
local dir = (getDir #temp)
if dir == undefined or dir == "" do dir = sysInfo.tempdir
dir = substituteString dir "\\" "/"
if dir[dir.count] != "/" do dir += "/"
dir
)''',
        timeout=15.0,
    ).strip().strip('"').replace("\\", "/")
    if not raw or raw.startswith("ERROR"):
        raise RuntimeError(f"Could not resolve Max temp dir: {raw!r}")
    if not raw.endswith("/"):
        raw += "/"
    stamp = int(time.time() * 1000) % 2_000_000_000
    return f"{raw}{prefix}_{stamp}.png"


def _shared_capture_outfile(prefix: str = "dialog_monitor") -> str | None:
    """Path under shared workspace for Max to write, or None if not configured."""
    try:
        from maxmcp.workspace_config import next_shared_capture_path
    except ImportError:
        repo_root = _MODULE_DIR.parent
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from maxmcp.workspace_config import next_shared_capture_path
    return next_shared_capture_path(prefix)


def _callback_host_for_max() -> str:
    """LAN address Max should use to reach this Python host."""
    import os
    import socket

    env = (os.environ.get("MAXMCP_CALLBACK_HOST") or "").strip()
    if env:
        return env
    # Route-based guess toward the configured Max host.
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.168.139.45", 8765))
        return sock.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())
    finally:
        sock.close()


def _python_side_path_candidates(client: Any, file_path: str) -> list[Path]:
    """Paths Python can try reading without involving Max (UNC admin share etc.)."""
    host = getattr(client, "host", None) or ""
    raw = str(file_path).replace("/", "\\")
    out: list[Path] = [Path(file_path), Path(raw)]
    if host and len(raw) >= 3 and raw[1] == ":" and raw[2] == "\\":
        drive = raw[0].upper()
        rest = raw[3:]
        out.append(Path(rf"\\{host}\{drive}$\{rest}"))
    # De-dupe while preserving order
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in out:
        key = str(p).lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return uniq


def try_read_capture_locally(client: Any, file_path: str) -> bytes | None:
    """Python-only read of a Max capture path (local disk or UNC)."""
    for candidate in _python_side_path_candidates(client, file_path):
        try:
            if candidate.is_file():
                return candidate.read_bytes()
        except OSError:
            continue
    return None


def fetch_file_bytes_via_http_upload(
    client: Any,
    path: str,
    *,
    timeout: float = 30.0,
) -> bytes:
    """Ask Max to HTTP POST the file to a one-shot server on this host.

    Avoids returning image bytes through the MaxScript TCP bridge (which has
    stalled the remote listener when base64-encoding captures).
    """
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    box: dict[str, Any] = {"data": None}

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            box["data"] = self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, format, *args):  # noqa: A003
            return

    server = HTTPServer(("0.0.0.0", 0), _Handler)
    port = int(server.server_address[1])
    host = _callback_host_for_max()
    url = f"http://{host}:{port}/capture"
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    esc = _escape_ms_string(str(path).replace("/", "\\"))
    url_esc = _escape_ms_string(url)
    code = f'''(
local p = @"{esc}"
if not (doesFileExist p) then ("ERROR: missing:" + p)
else (
  try (
    local bytes = (dotNetClass "System.IO.File").ReadAllBytes p
    local wc = dotNetObject "System.Net.WebClient"
    wc.UploadData @"{url_esc}" bytes
    wc.Dispose()
    "ok"
  ) catch ("ERROR: " + (getCurrentException() as string))
)
)'''
    raw = "unset"
    try:
        raw = _exec_ms(client, code, timeout=timeout).strip().strip('"')
        thread.join(timeout=min(10.0, timeout))
    finally:
        try:
            server.server_close()
        except Exception:
            pass

    data = box.get("data")
    if isinstance(data, (bytes, bytearray)) and len(data) > 0:
        return bytes(data)
    raise RuntimeError(
        f"Max HTTP upload of capture failed (ms={raw!r}, received={0 if not data else len(data)} bytes, url={url})"
    )


def fetch_file_bytes_via_max(
    client: Any,
    path: str,
    *,
    timeout: float = 20.0,
    chunk_size: int = 8_000,
) -> bytes:
    """Fallback: read Max-host file via small base64 chunks on the TCP bridge."""
    import base64

    esc = _escape_ms_string(str(path).replace("\\", "/"))
    size_raw = _exec_ms(
        client,
        f'(if doesFileExist @"{esc}" then ((getFileSize @"{esc}") as string) else "ERROR: missing")',
        timeout=timeout,
    ).strip().strip('"')
    if size_raw.startswith("ERROR:"):
        raise RuntimeError(f"Could not stat capture on Max host: {size_raw}")
    digits = "".join(ch for ch in size_raw if ch.isdigit())
    if not digits:
        raise RuntimeError(f"Could not stat capture on Max host: {size_raw}")
    total = int(digits)
    if total <= 0:
        raise RuntimeError(f"Capture on Max host is empty: {path}")

    out = bytearray()
    offset = 0
    while offset < total:
        n = min(chunk_size, total - offset)
        code = f'''(
local p = @"{esc}"
try (
  local fs = dotNetObject "System.IO.FileStream" p ((dotNetClass "System.IO.FileMode").Open) ((dotNetClass "System.IO.FileAccess").Read) ((dotNetClass "System.IO.FileShare").ReadWrite)
  fs.Seek {offset} ((dotNetClass "System.IO.SeekOrigin").Begin)
  local buf = (dotNetClass "System.Array").CreateInstance (dotNetClass "System.Byte") {n}
  local got = fs.Read buf 0 {n}
  fs.Close()
  if got <= 0 then "ERROR: eof"
  else (
    if got < {n} do (
      local trim = (dotNetClass "System.Array").CreateInstance (dotNetClass "System.Byte") got
      (dotNetClass "System.Array").Copy buf 0 trim 0 got
      buf = trim
    )
    (dotNetClass "System.Convert").ToBase64String buf
  )
) catch ("ERROR: " + (getCurrentException() as string))
)'''
        raw = _exec_ms(client, code, timeout=timeout).strip().strip('"')
        if raw.startswith("ERROR:") or raw == "":
            raise RuntimeError(f"Could not read capture chunk @{offset}: {raw}")
        try:
            out.extend(base64.b64decode(raw, validate=False))
        except Exception as exc:
            raise RuntimeError(
                f"Invalid base64 chunk @{offset} ({len(raw)} chars): {exc}"
            ) from exc
        offset += n
    return bytes(out)


def resolve_capture_image(client: Any, capture: dict[str, Any]) -> bytes:
    """Load capture bytes with as little Max work as possible.

    Order:
    1. Python reads the path directly (local or ``\\\\host\\C$\\...``)
    2. One Max HTTP upload to this host (bridge returns only ``ok``)
    Never pull image bytes through the MaxScript TCP bridge.
    """
    file_path = capture.get("file") or ""
    if not file_path:
        raise RuntimeError("capture missing file")

    data = try_read_capture_locally(client, file_path)
    if data is not None:
        capture["fetched_via"] = "python_local_or_unc"
        return data

    data = fetch_file_bytes_via_http_upload(client, file_path)
    capture["fetched_via"] = "max_http_upload"
    return data


def capture_dialog_meta(
    *,
    title_pattern: str = "自动蒙皮4.*",
    vendor_pattern: str = "*天晴数码",
    require_vendor: bool = True,
    client: Any | None = None,
    outfile: str | None = None,
) -> dict[str, Any]:
    """Find dialog + plain windows.snapshot to ASCII temp PNG (one Max round-trip).

    Orchestration/JSON stay on Python. Avoids ``windows.snapshot`` kwargs that
    produce solid-black images on Max 2016, and avoids CopyFromScreen (invalid
    handle on this remote host).
    """
    client = _ensure_max_client(client)
    ensure_dialog_monitor_loaded(client)
    title = _escape_ms_string(title_pattern)
    vendor = _escape_ms_string(vendor_pattern)
    req = "true" if require_vendor else "false"
    if outfile:
        out_setup = f'local _dmOut = @"{_escape_ms_string(outfile)}"\n'
    else:
        out_setup = r'''
local _dmDir = (getDir #temp)
if _dmDir == undefined or _dmDir == "" do _dmDir = sysInfo.tempdir
_dmDir = substituteString _dmDir "\\" "/"
if _dmDir[_dmDir.count] != "/" do _dmDir += "/"
local _dmOut = _dmDir + "dialog_monitor_" + (timeStamp() as string) + ".png"
'''
    code = f"""(
        MCP_DialogMonitor.titlePattern = "{title}"
        MCP_DialogMonitor.vendorPattern = "{vendor}"
        MCP_DialogMonitor.requireVendor = {req}
        {out_setup}
        local matches = MCP_DialogMonitor.findDialog visibleOnly:true parentType:"desktop"
        if matches.count == 0 then "ERROR: dialog not found"
        else (
          local hwnd = MCP_DialogMonitor.asHwndInt matches[1][1]
          local dlgTitle = MCP_DialogMonitor.normalizeTitle matches[1][5]
          local geom = MCP_DialogMonitor.getWindowGeom hwnd
          if geom == undefined then "ERROR: could not read window geometry"
          else (
            local bmp = undefined
            local snapErr = undefined
            try (bmp = windows.snapshot hwnd) catch (snapErr = (getCurrentException() as string))
            if snapErr != undefined then ("ERROR: windows.snapshot failed: " + snapErr)
            else if bmp == undefined then "ERROR: snapshot returned undefined"
            else (
              local imageW = bmp.width
              local imageH = bmp.height
              local saved = false
              local saveErr = undefined
              try (
                bmp.filename = _dmOut
                saved = save bmp
              ) catch (saveErr = (getCurrentException() as string))
              try (close bmp) catch ()
              if saveErr != undefined then ("ERROR: save failed: " + saveErr)
              else if not saved then ("ERROR: save returned false: " + _dmOut)
              else (
                MCP_DialogMonitor.lastHwnd = hwnd
                MCP_DialogMonitor.lastTitle = dlgTitle
                MCP_DialogMonitor.lastCapturePath = _dmOut
                MCP_DialogMonitor.lastImageWidth = imageW
                MCP_DialogMonitor.lastImageHeight = imageH
                "OK|" + (hwnd as string) + "|" + dlgTitle + "|" + _dmOut + "|" + \
                  (geom[1] as string) + "|" + (geom[2] as string) + "|" + (geom[3] as string) + "|" + (geom[4] as string) + "|" + \
                  (imageW as string) + "|" + (imageH as string) + "|" + (matches.count as string)
              )
            )
          )
        )
    )"""
    raw = _exec_ms(client, code).strip().strip('"')
    if raw.startswith("ERROR"):
        return {
            "ok": False,
            "error": raw,
            "title_pattern": title_pattern,
            "vendor_pattern": vendor_pattern,
            "shared_workspace": False,
        }
    if not raw.startswith("OK|"):
        try:
            data = json.loads(raw)
            data["shared_workspace"] = False
            return data
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"capture_dialog_meta unexpected payload: {raw!r}") from exc

    parts = raw.split("|")
    if len(parts) < 11:
        raise RuntimeError(f"capture_dialog_meta bad OK payload: {raw!r}")
    _, hwnd_s, dlg_title, path, gx, gy, gw, gh, iw, ih, match_count = parts[:11]
    hwnd_i = int(hwnd_s)
    meta = {
        "ok": True,
        "hwnd": hwnd_i,
        "title": dlg_title,
        "file": path.replace("\\", "/"),
        "screen_rect": {"x": int(gx), "y": int(gy), "w": int(gw), "h": int(gh)},
        "image_width": int(iw),
        "image_height": int(ih),
        "match_count": int(match_count),
        "shared_workspace": False,
        "capture_method": "hwnd_snapshot_plain",
    }
    crect = fetch_client_rect(hwnd_i, client=client)
    if crect:
        meta["client_rect"] = crect
    return meta


def box_center(box: list) -> tuple[float, float] | None:
    """OCR box is [[x,y], ...] — return average center, or None if empty."""
    if not box:
        return None
    xs: list[float] = []
    ys: list[float] = []
    for pt in box:
        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
            xs.append(float(pt[0]))
            ys.append(float(pt[1]))
        elif isinstance(pt, dict):
            xs.append(float(pt.get("x", 0)))
            ys.append(float(pt.get("y", 0)))
    if not xs:
        return None
    return sum(xs) / len(xs), sum(ys) / len(ys)


def image_to_screen(
    cx: float,
    cy: float,
    *,
    screen_rect: dict[str, Any],
    image_width: int,
    image_height: int,
    client_rect: dict[str, Any] | None = None,
) -> tuple[int, int]:
    """Map image-pixel center to physical screen coordinates.

    ``windows.snapshot`` is usually client-area content, but Max often returns a
    bitmap a few pixels taller than GetClientRect (non-client chrome in the
    capture). Prefer ``client_rect`` origin; when width matches, use 1:1 X and
    subtract top padding instead of uniformly scaling Y (which misses list rows).
    """
    iw = max(1, int(image_width))
    ih = max(1, int(image_height))
    if isinstance(client_rect, dict) and client_rect.get("w"):
        ox = float(client_rect["x"])
        oy = float(client_rect["y"])
        cw = float(client_rect["w"])
        ch = float(client_rect["h"])
        # Width match (±2px): treat as client bitmap with optional top pad.
        if abs(cw - iw) <= 2.0:
            top_pad = max(0.0, float(ih) - ch)
            sx = int(round(ox + cx * cw / iw))
            sy = int(round(oy + cy - top_pad))
            return sx, sy
        sx = int(round(ox + cx * cw / iw))
        sy = int(round(oy + cy * ch / ih))
        return sx, sy
    sx = int(round(float(screen_rect["x"]) + cx * float(screen_rect["w"]) / iw))
    sy = int(round(float(screen_rect["y"]) + cy * float(screen_rect["h"]) / ih))
    return sx, sy


def fetch_client_rect(hwnd: int, *, client: Any | None = None) -> dict[str, int] | None:
    """Return {x,y,w,h} client area in screen pixels, or None on failure."""
    client = _ensure_max_client(client)
    ensure_dialog_monitor_loaded(client)
    tmpl = r'''(
local hwnd = __HWND__
fn mcpEnsureClientRect = (
  try (if (dotNetClass "McpDialogClientRect") != undefined do return true) catch ()
  local source = ""
  source += "using System; using System.Runtime.InteropServices;\n"
  source += "public class McpDialogClientRect {\n"
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
  if results.Errors.HasErrors then false else true
)
if not (mcpEnsureClientRect()) then "ERROR"
else ((dotNetClass "McpDialogClientRect").Info (hwnd as integer64))
)'''
    raw = _exec_ms(client, tmpl.replace("__HWND__", str(int(hwnd))), timeout=30.0).strip().strip('"')
    parts = raw.split(",")
    if len(parts) != 4:
        return None
    try:
        x, y, w, h = (int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]))
    except ValueError:
        return None
    if w <= 0 or h <= 0:
        return None
    return {"x": x, "y": y, "w": w, "h": h}


def _normalize_text(value: str) -> str:
    return "".join(value.split())


def find_matching_line(
    lines: list[dict[str, Any]],
    text: str,
    *,
    score_min: float = 0.5,
) -> dict[str, Any] | None:
    needle = _normalize_text(text)
    if not needle:
        return None

    candidates: list[tuple[int, float, int, dict[str, Any]]] = []
    for line in lines:
        hay = _normalize_text(str(line.get("text") or ""))
        if needle not in hay:
            continue
        score = float(line.get("score") or 0.0)
        if score < score_min:
            continue
        # Rank: exact match first, then shortest containing text, then higher score
        exact = 0 if hay == needle else 1
        candidates.append((exact, len(hay), -score, line))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    return candidates[0][3]


def click_at_screen(
    x: int,
    y: int,
    *,
    hwnd: int = 0,
    client: Any | None = None,
) -> dict[str, Any]:
    """Click screen coords inside Max (keeps Max-side work to one short call)."""
    client = _ensure_max_client(client)
    ensure_dialog_monitor_loaded(client)
    code = f"MCP_DialogMonitor.clickAtScreen {int(x)} {int(y)} foregroundHwnd:{int(hwnd)}"
    raw = _exec_ms(client, code, timeout=15.0).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"ok": False, "error": raw}


def _parse_meta(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"non-JSON capture meta: {raw!r}") from exc


def capture_max_menu_bar(
    *,
    height: int = 120,
    client: Any | None = None,
    outfile: str | None = None,
) -> dict[str, Any]:
    """Capture the top strip of the 3ds Max main window (menubar area)."""
    client = _ensure_max_client(client)
    ensure_dialog_monitor_loaded(client)
    if outfile:
        code = (
            f"MCP_DialogMonitor.captureMaxMenuBarMeta height:{int(height)} "
            f'outfile:@"{_escape_ms_string(outfile)}"'
        )
    else:
        code = f"MCP_DialogMonitor.captureMaxMenuBarMeta height:{int(height)}"
    data = _parse_meta(_exec_ms(client, code))
    data["shared_workspace"] = False
    return data


def capture_popup_menu(*, client: Any | None = None, outfile: str | None = None) -> dict[str, Any]:
    """Capture the largest visible Win32 popup menu (#32768)."""
    client = _ensure_max_client(client)
    ensure_dialog_monitor_loaded(client)
    if outfile:
        code = f'MCP_DialogMonitor.capturePopupMenuMeta outfile:@"{_escape_ms_string(outfile)}"'
    else:
        code = "MCP_DialogMonitor.capturePopupMenuMeta()"
    data = _parse_meta(_exec_ms(client, code))
    data["shared_workspace"] = False
    return data


def list_popup_menus(*, client: Any | None = None) -> dict[str, Any]:
    client = _ensure_max_client(client)
    ensure_dialog_monitor_loaded(client)
    return _parse_meta(_exec_ms(client, "MCP_DialogMonitor.listPopupMenus()"))


def click_ocr_in_capture(
    text: str,
    capture: dict[str, Any],
    *,
    ocr_base: str = DEFAULT_OCR_BASE,
    score_min: float = 0.5,
    client: Any | None = None,
    ocr_lines: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """OCR a capture (or reuse lines) and click the best matching text."""
    if not capture.get("ok"):
        return {"ok": False, "capture": capture, "error": capture.get("error", "capture failed")}
    file_path = capture.get("file")
    if not file_path:
        return {"ok": False, "capture": capture, "error": "capture missing file"}

    client = _ensure_max_client(client)
    lines = ocr_lines
    if lines is None:
        try:
            image = resolve_capture_image(client, capture)
            path_l = str(file_path).lower()
            mime = "image/jpeg" if path_l.endswith((".jpg", ".jpeg")) else "image/png"
            lines = recognize(image, ocr_base=ocr_base, mime=mime)
        except (OcrError, RuntimeError, OSError) as exc:
            return {"ok": False, "capture": capture, "error": str(exc)}

    matched = find_matching_line(lines, text, score_min=score_min)
    if matched is None:
        return {
            "ok": False,
            "error": f"no OCR line matched text={text!r} with score>={score_min}",
            "capture": capture,
            "ocr_lines": lines,
        }

    center = box_center(matched.get("box") or [])
    if center is None:
        return {
            "ok": False,
            "error": "matched line has empty box",
            "matched": matched,
            "capture": capture,
            "ocr_lines": lines,
        }

    sx, sy = image_to_screen(
        center[0],
        center[1],
        screen_rect=capture["screen_rect"],
        image_width=int(capture["image_width"]),
        image_height=int(capture["image_height"]),
        client_rect=capture.get("client_rect"),
    )
    click = click_at_screen(sx, sy, hwnd=int(capture.get("hwnd") or 0), client=client)
    return {
        "ok": bool(click.get("ok")),
        "matched": matched,
        "screen_xy": [sx, sy],
        "image_xy": [center[0], center[1]],
        "capture": capture,
        "ocr_lines": lines,
        "click": click,
        "error": None if click.get("ok") else click.get("error"),
    }


def click_menu_path(
    menu: str,
    item: str,
    *,
    menu_bar_height: int = 140,
    open_wait_s: float = 0.45,
    ocr_base: str = DEFAULT_OCR_BASE,
    score_min: float = 0.5,
    client: Any | None = None,
) -> dict[str, Any]:
    """Open a Max menubar entry via OCR, then click a popup menu item.

    Example: click_menu_path("NDBox", "天晴盒子")
    """
    import time

    client = _ensure_max_client(client)
    bar = capture_max_menu_bar(height=menu_bar_height, client=client)
    step1 = click_ocr_in_capture(
        menu,
        bar,
        ocr_base=ocr_base,
        score_min=score_min,
        client=client,
    )
    if not step1.get("ok"):
        return {
            "ok": False,
            "error": f"failed to open menu {menu!r}: {step1.get('error')}",
            "open": step1,
        }

    time.sleep(max(0.05, float(open_wait_s)))

    popup = capture_popup_menu(client=client)
    if not popup.get("ok"):
        # Fallback: capture a region under the menubar click
        sx, sy = step1["screen_xy"]
        fallback_out = _shared_capture_outfile("menu_fallback")
        if fallback_out:
            out_expr = f'outfile:@"{_escape_ms_string(fallback_out)}"'
        else:
            out_expr = ""
        code = f"""(
local cap = MCP_DialogMonitor.captureScreenRect {int(sx) - 40} {int(sy)} 420 480 {out_expr}
if classOf cap == String then ("{{\\"ok\\":false,\\"error\\":\\"" + (MCP_DialogMonitor.escapeJson cap) + "\\"}}") else (
  MCP_DialogMonitor.metaJson 0 "MenuFallback" cap[1] MCP_DialogMonitor.lastScreenRect cap[2] cap[3] extra:"\\"source\\":\\"menu_fallback\\""
)
)"""
        popup = _parse_meta(_exec_ms(client, code))
        popup["shared_workspace"] = bool(fallback_out)

    step2 = click_ocr_in_capture(
        item,
        popup,
        ocr_base=ocr_base,
        score_min=score_min,
        client=client,
    )
    return {
        "ok": bool(step2.get("ok")),
        "menu": menu,
        "item": item,
        "open": {
            "matched": step1.get("matched"),
            "screen_xy": step1.get("screen_xy"),
            "click": step1.get("click"),
            "capture": step1.get("capture"),
        },
        "select": {
            "matched": step2.get("matched"),
            "screen_xy": step2.get("screen_xy"),
            "click": step2.get("click"),
            "capture": step2.get("capture"),
            "ocr_texts": [ln.get("text") for ln in (step2.get("ocr_lines") or [])],
            "error": step2.get("error"),
        },
        "error": None if step2.get("ok") else step2.get("error"),
    }


def recognize_dialog(
    *,
    title_pattern: str = "自动蒙皮4.*",
    vendor_pattern: str = "*天晴数码",
    require_vendor: bool = True,
    ocr_base: str = DEFAULT_OCR_BASE,
    client: Any | None = None,
) -> dict[str, Any]:
    """Capture dialog and run OCR; does not click."""
    client = _ensure_max_client(client)
    capture = capture_dialog_meta(
        title_pattern=title_pattern,
        vendor_pattern=vendor_pattern,
        require_vendor=require_vendor,
        client=client,
    )
    if not capture.get("ok"):
        return {"ok": False, "capture": capture, "error": capture.get("error", "capture failed")}
    file_path = capture.get("file")
    if not file_path:
        return {"ok": False, "capture": capture, "error": "capture missing file"}
    try:
        image = resolve_capture_image(client, capture)
        path_l = str(file_path).lower()
        mime = "image/jpeg" if path_l.endswith((".jpg", ".jpeg")) else "image/png"
        lines = recognize(image, ocr_base=ocr_base, mime=mime)
    except (OcrError, RuntimeError, OSError) as exc:
        return {"ok": False, "capture": capture, "error": str(exc)}
    return {"ok": True, "capture": capture, "ocr_lines": lines}


def click_dialog_button(
    text: str,
    *,
    title_pattern: str = "自动蒙皮4.*",
    vendor_pattern: str = "*天晴数码",
    require_vendor: bool = True,
    ocr_base: str = DEFAULT_OCR_BASE,
    score_min: float = 0.5,
    client: Any | None = None,
) -> dict[str, Any]:
    """Capture → OCR → match button text → click screen center of box."""
    recognized = recognize_dialog(
        title_pattern=title_pattern,
        vendor_pattern=vendor_pattern,
        require_vendor=require_vendor,
        ocr_base=ocr_base,
        client=client,
    )
    if not recognized.get("ok"):
        return recognized

    capture = recognized["capture"]
    lines = recognized["ocr_lines"]
    matched = find_matching_line(lines, text, score_min=score_min)
    if matched is None:
        return {
            "ok": False,
            "error": f"no OCR line matched text={text!r} with score>={score_min}",
            "capture": capture,
            "ocr_lines": lines,
        }

    center = box_center(matched.get("box") or [])
    if center is None:
        return {
            "ok": False,
            "error": "matched line has empty box",
            "matched": matched,
            "capture": capture,
            "ocr_lines": lines,
        }

    sx, sy = image_to_screen(
        center[0],
        center[1],
        screen_rect=capture["screen_rect"],
        image_width=int(capture["image_width"]),
        image_height=int(capture["image_height"]),
        client_rect=capture.get("client_rect"),
    )
    click = click_at_screen(sx, sy, hwnd=int(capture.get("hwnd") or 0), client=client)
    return {
        "ok": bool(click.get("ok")),
        "matched": matched,
        "screen_xy": [sx, sy],
        "image_xy": [center[0], center[1]],
        "capture": capture,
        "ocr_lines": lines,
        "click": click,
        "error": None if click.get("ok") else click.get("error"),
    }


def dialog_exists(
    *,
    title_pattern: str = "自动蒙皮4.*",
    vendor_pattern: str = "*天晴数码",
    require_vendor: bool = True,
    client: Any | None = None,
) -> dict[str, Any]:
    """Lightweight findDialog probe — no screenshot / OCR."""
    client = _ensure_max_client(client)
    ensure_dialog_monitor_loaded(client)
    title = _escape_ms_string(title_pattern)
    vendor = _escape_ms_string(vendor_pattern)
    req = "true" if require_vendor else "false"
    code = f"""(
        MCP_DialogMonitor.titlePattern = "{title}"
        MCP_DialogMonitor.vendorPattern = "{vendor}"
        MCP_DialogMonitor.requireVendor = {req}
        local matches = MCP_DialogMonitor.findDialog visibleOnly:true parentType:"desktop"
        if matches.count == 0 then "NONE"
        else (
          local hwnd = MCP_DialogMonitor.asHwndInt matches[1][1]
          local dlgTitle = MCP_DialogMonitor.normalizeTitle matches[1][5]
          "OK|" + (hwnd as string) + "|" + dlgTitle + "|" + (matches.count as string)
        )
    )"""
    raw = _exec_ms(client, code, timeout=15.0).strip().strip('"')
    if raw == "NONE" or raw.startswith("ERROR"):
        return {
            "ok": True,
            "exists": False,
            "title_pattern": title_pattern,
            "vendor_pattern": vendor_pattern,
        }
    if not raw.startswith("OK|"):
        return {"ok": False, "exists": False, "error": raw}
    parts = raw.split("|")
    if len(parts) < 4:
        return {"ok": False, "exists": False, "error": f"bad payload: {raw!r}"}
    return {
        "ok": True,
        "exists": True,
        "hwnd": int(parts[1]),
        "title": parts[2],
        "match_count": int(parts[3]),
        "title_pattern": title_pattern,
        "vendor_pattern": vendor_pattern,
    }


def _line_y_center(line: dict[str, Any]) -> float | None:
    center = box_center(line.get("box") or [])
    return None if center is None else float(center[1])


def _prefer_dynamic_list_line(
    lines: list[dict[str, Any]],
    *,
    score_min: float = 0.5,
) -> dict[str, Any] | None:
    """Prefer filled list row (模型:...) or placeholder over other labels."""
    for needle in ("选中后在编辑区添加", "模型：", "模型:"):
        hit = find_matching_line(lines, needle, score_min=score_min)
        if hit is not None:
            return hit
    return None


def click_between_texts(
    above: str = "合并网格",
    below: str = "编辑区",
    *,
    title_pattern: str = "自动蒙皮4.*",
    vendor_pattern: str = "*天晴数码",
    require_vendor: bool = True,
    ocr_base: str = DEFAULT_OCR_BASE,
    score_min: float = 0.5,
    client: Any | None = None,
    prefer_dynamic_list: bool = True,
) -> dict[str, Any]:
    """Click midpoint between two OCR anchors (for dynamic list regions).

    When prefer_dynamic_list is True, first try clicking a ``模型:`` / placeholder
    row if OCR already sees it.
    """
    recognized = recognize_dialog(
        title_pattern=title_pattern,
        vendor_pattern=vendor_pattern,
        require_vendor=require_vendor,
        ocr_base=ocr_base,
        client=client,
    )
    if not recognized.get("ok"):
        return recognized

    capture = recognized["capture"]
    lines = recognized["ocr_lines"]
    client = _ensure_max_client(client)

    matched: dict[str, Any] | None = None
    method = "between_anchors"
    if prefer_dynamic_list:
        matched = _prefer_dynamic_list_line(lines, score_min=score_min)
        if matched is not None:
            method = "dynamic_list_text"

    image_xy: tuple[float, float] | None = None
    if matched is not None:
        image_xy = box_center(matched.get("box") or [])
    else:
        above_line = find_matching_line(lines, above, score_min=score_min)
        below_line = find_matching_line(lines, below, score_min=score_min)
        if above_line is None or below_line is None:
            return {
                "ok": False,
                "error": (
                    f"need anchors {above!r} and {below!r} "
                    f"(found above={above_line is not None}, below={below_line is not None})"
                ),
                "capture": capture,
                "ocr_lines": lines,
                "method": method,
            }
        ay = _line_y_center(above_line)
        by = _line_y_center(below_line)
        ax = box_center(above_line.get("box") or [])
        bx = box_center(below_line.get("box") or [])
        if ay is None or by is None or ax is None or bx is None:
            return {
                "ok": False,
                "error": "anchor boxes missing geometry",
                "capture": capture,
                "ocr_lines": lines,
            }
        # Midpoint between bottoms of above and top of below ≈ list body.
        image_xy = ((ax[0] + bx[0]) / 2.0, (ay + by) / 2.0)
        matched = {"text": f"{above}..{below}", "above": above_line, "below": below_line}

    if image_xy is None:
        return {
            "ok": False,
            "error": "could not compute click point",
            "capture": capture,
            "ocr_lines": lines,
        }

    sx, sy = image_to_screen(
        image_xy[0],
        image_xy[1],
        screen_rect=capture["screen_rect"],
        image_width=int(capture["image_width"]),
        image_height=int(capture["image_height"]),
        client_rect=capture.get("client_rect"),
    )
    click = click_at_screen(sx, sy, hwnd=int(capture.get("hwnd") or 0), client=client)
    return {
        "ok": bool(click.get("ok")),
        "matched": matched,
        "method": method,
        "screen_xy": [sx, sy],
        "image_xy": [image_xy[0], image_xy[1]],
        "capture": capture,
        "ocr_lines": lines,
        "click": click,
        "error": None if click.get("ok") else click.get("error"),
    }


def click_button_on_same_row(
    row_label: str,
    button_text: str = "选定",
    *,
    title_pattern: str = "自动蒙皮4.*",
    vendor_pattern: str = "*天晴数码",
    require_vendor: bool = True,
    ocr_base: str = DEFAULT_OCR_BASE,
    score_min: float = 0.5,
    y_tolerance_px: float = 28.0,
    client: Any | None = None,
) -> dict[str, Any]:
    """Click ``button_text`` whose OCR box shares roughly the same Y as ``row_label``."""
    recognized = recognize_dialog(
        title_pattern=title_pattern,
        vendor_pattern=vendor_pattern,
        require_vendor=require_vendor,
        ocr_base=ocr_base,
        client=client,
    )
    if not recognized.get("ok"):
        return recognized

    capture = recognized["capture"]
    lines = recognized["ocr_lines"]
    client = _ensure_max_client(client)

    row = find_matching_line(lines, row_label, score_min=score_min)
    if row is None:
        return {
            "ok": False,
            "error": f"row label not found: {row_label!r}",
            "capture": capture,
            "ocr_lines": lines,
        }
    row_y = _line_y_center(row)
    if row_y is None:
        return {
            "ok": False,
            "error": "row label has empty box",
            "matched_row": row,
            "capture": capture,
            "ocr_lines": lines,
        }

    needle = _normalize_text(button_text)
    ranked: list[tuple[float, float, float, dict[str, Any]]] = []
    for line in lines:
        hay = _normalize_text(str(line.get("text") or ""))
        if needle not in hay:
            continue
        score = float(line.get("score") or 0.0)
        if score < score_min:
            continue
        cy = _line_y_center(line)
        if cy is None:
            continue
        dy = abs(cy - row_y)
        if dy > y_tolerance_px:
            continue
        exact = 0.0 if hay == needle else 1.0
        ranked.append((exact, dy, -score, line))

    if not ranked:
        return {
            "ok": False,
            "error": (
                f"no {button_text!r} near row {row_label!r} "
                f"(y_tolerance={y_tolerance_px})"
            ),
            "matched_row": row,
            "capture": capture,
            "ocr_lines": lines,
        }
    ranked.sort(key=lambda item: (item[0], item[1], item[2]))
    matched = ranked[0][3]
    box = matched.get("box") or []
    center = box_center(box)
    if center is None:
        return {
            "ok": False,
            "error": "matched button has empty box",
            "matched": matched,
            "matched_row": row,
            "capture": capture,
            "ocr_lines": lines,
        }

    # When OCR merges adjacent buttons, bias click toward the intended label.
    # Row order is typically 选定 … 删除 清空 — 选定 is left, 清空 is right.
    hay = _normalize_text(str(matched.get("text") or ""))
    needle_n = _normalize_text(button_text)
    image_x, image_y = center[0], center[1]
    if hay != needle_n and len(hay) > len(needle_n) + 1:
        xs = []
        for pt in box:
            if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                xs.append(float(pt[0]))
            elif isinstance(pt, dict):
                xs.append(float(pt.get("x", 0)))
        if len(xs) >= 2:
            left, right = min(xs), max(xs)
            width = max(1.0, right - left)
            if needle_n in {"清空", "清除", "Close", "关闭"}:
                frac = max(0.65, min(0.92, 1.0 - len(needle_n) / max(1, len(hay))))
            elif needle_n in {"选定", "选择", "确定", "OK"}:
                frac = max(0.12, min(0.35, len(needle_n) / max(1, len(hay))))
            else:
                frac = 0.5
            image_x = left + width * frac

    sx, sy = image_to_screen(
        image_x,
        image_y,
        screen_rect=capture["screen_rect"],
        image_width=int(capture["image_width"]),
        image_height=int(capture["image_height"]),
        client_rect=capture.get("client_rect"),
    )
    click = click_at_screen(sx, sy, hwnd=int(capture.get("hwnd") or 0), client=client)
    return {
        "ok": bool(click.get("ok")),
        "matched": matched,
        "matched_row": row,
        "screen_xy": [sx, sy],
        "image_xy": [image_x, image_y],
        "capture": capture,
        "ocr_lines": lines,
        "click": click,
        "error": None if click.get("ok") else click.get("error"),
    }


def wait_for_dialog_ocr(
    text: str,
    *,
    title_pattern: str = "自动蒙皮4.*",
    vendor_pattern: str = "*天晴数码",
    require_vendor: bool = True,
    ocr_base: str = DEFAULT_OCR_BASE,
    score_min: float = 0.5,
    timeout_s: float = 120.0,
    poll_s: float = 2.0,
    max_match_len: int = 12,
    client: Any | None = None,
) -> dict[str, Any]:
    """Poll dialog OCR until ``text`` appears (prefers short lines)."""
    import time

    client = _ensure_max_client(client)
    needle = _normalize_text(text)
    deadline = time.time() + max(0.5, float(timeout_s))
    last: dict[str, Any] = {"ok": False, "error": "not started"}
    attempts = 0

    while time.time() < deadline:
        attempts += 1
        recognized = recognize_dialog(
            title_pattern=title_pattern,
            vendor_pattern=vendor_pattern,
            require_vendor=require_vendor,
            ocr_base=ocr_base,
            client=client,
        )
        last = recognized
        if recognized.get("ok"):
            lines = recognized.get("ocr_lines") or []
            hits: list[tuple[int, dict[str, Any]]] = []
            for line in lines:
                hay = _normalize_text(str(line.get("text") or ""))
                if needle not in hay:
                    continue
                if float(line.get("score") or 0.0) < score_min:
                    continue
                if max_match_len > 0 and len(hay) > max_match_len and hay != needle:
                    # Allow exact needle even if longer policy — skip long containers
                    continue
                hits.append((len(hay), line))
            if hits:
                hits.sort(key=lambda item: item[0])
                matched = hits[0][1]
                return {
                    "ok": True,
                    "matched": matched,
                    "attempts": attempts,
                    "capture": recognized.get("capture"),
                    "ocr_lines": lines,
                    "error": None,
                }
        time.sleep(max(0.2, float(poll_s)))

    return {
        "ok": False,
        "error": f"OCR text {text!r} not seen within {timeout_s}s ({attempts} attempts)",
        "attempts": attempts,
        "capture": last.get("capture"),
        "ocr_lines": last.get("ocr_lines"),
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Dialog / menu OCR click via MCP_DialogMonitor")
    p.add_argument("--text", help="Button text substring to click")
    p.add_argument("--list-only", action="store_true", help="OCR only; do not click")
    p.add_argument("--title", default="自动蒙皮4.*", help="Window title pattern")
    p.add_argument("--vendor", default="*天晴数码", help="Vendor title pattern")
    p.add_argument("--no-vendor", action="store_true", help="Do not require vendor pattern")
    p.add_argument("--menu", help="Menubar label to open (e.g. NDBox)")
    p.add_argument("--item", help="Popup menu item to click (e.g. 天晴盒子)")
    p.add_argument("--menu-bar-height", type=int, default=140)
    p.add_argument("--ocr-base", default=DEFAULT_OCR_BASE, help="OCR service base URL")
    p.add_argument("--score-min", type=float, default=0.5)
    p.add_argument("--health", action="store_true", help="Only check OCR health")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.health:
        try:
            print(json.dumps(health(args.ocr_base), ensure_ascii=False, indent=2))
            return 0
        except OcrError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
            return 1

    if args.menu and args.item:
        result = click_menu_path(
            args.menu,
            args.item,
            menu_bar_height=args.menu_bar_height,
            ocr_base=args.ocr_base,
            score_min=args.score_min,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1

    require_vendor = not args.no_vendor
    if args.list_only:
        result = recognize_dialog(
            title_pattern=args.title,
            vendor_pattern=args.vendor,
            require_vendor=require_vendor,
            ocr_base=args.ocr_base,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1

    if not args.text:
        print("error: provide --text, or --menu/--item, or --list-only / --health", file=sys.stderr)
        return 2

    result = click_dialog_button(
        args.text,
        title_pattern=args.title,
        vendor_pattern=args.vendor,
        require_vendor=require_vendor,
        ocr_base=args.ocr_base,
        score_min=args.score_min,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
