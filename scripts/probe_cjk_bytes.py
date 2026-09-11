"""Byte-level CJK probe: determine whether the server/Max chain is really lossless.

All previous 'corruption' evidence came from client scripts decoding SSE responses
as ISO-8859-1 (requests falls back to it for text/event-stream with no charset).
This probe reads raw bytes (r.content) and reports hex + escaped text, so console
encoding can never fake the result.
"""
import json
import sys
from base64 import b64encode
from pathlib import Path

import requests

ENDPOINT = "http://192.168.139.76:8000/mcp"
OUT = Path(__file__).resolve().parent / "probe_cjk_bytes_result.txt"
session_id = None

lines: list[str] = []


def log(text: str) -> None:
    lines.append(text)


def first_data(content: bytes) -> bytes:
    for raw_line in content.splitlines():
        if raw_line.strip().startswith(b"data:"):
            return raw_line[5:].strip()
    return content


def call(payload: dict, ensure_ascii: bool, label: str) -> tuple[bytes, str]:
    """POST one JSON-RPC message; return (raw response bytes, data-line bytes)."""
    global session_id
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json; charset=utf-8",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    body = json.dumps(payload, ensure_ascii=ensure_ascii).encode("utf-8")
    r = requests.post(ENDPOINT, data=body, headers=headers, timeout=120)
    if not session_id and "Mcp-Session-Id" in r.headers:
        session_id = r.headers["Mcp-Session-Id"]
    data_line = first_data(r.content)
    log(f"--- {label} ---")
    log(f"HTTP content-type: {r.headers.get('content-type')!r}  requests.encoding={r.encoding!r}")
    log(f"raw data bytes hex: {data_line.hex()}")
    return r.content, data_line


def show_utf8(data_line: bytes, label: str) -> None:
    """Try to locate CJK and report whether it matches the expected UTF-8 bytes."""
    log(f"{label} -> utf8-decode: {data_line.decode('utf-8', errors='replace')!r}")
    if b"u6d4b" in data_line or b"\xe6\xb5\x8b" in data_line:
        log(f"{label} -> contains literal '\\\\u6d4b' or UTF-8 of 测: OK(lossless)")
    else:
        log(f"{label} -> NO matching CJK bytes found for 测 (u6d4b)")


# ── handshake ────────────────────────────────────────────────────────────────
call({"jsonrpc": "2.0", "id": 1, "method": "initialize",
      "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                 "clientInfo": {"name": "probe", "version": "1.0"}}}, True, "initialize")
call({"jsonrpc": "2.0", "method": "notifications/initialized"}, True, "initialized")

# ── 1. execute_maxscript echo, raw UTF-8 body ────────────────────────────────
_, dl_a = call({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "execute_maxscript",
                           "arguments": {"command": '( "测试A_RawUTF8" )'}}}, False, "echo A raw-utf8")
show_utf8(dl_a, "A")

# ── 2. execute_maxscript echo, \\uXXXX escaped body ──────────────────────────
_, dl_b = call({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "execute_maxscript",
                           "arguments": {"command": '( "测试B_Escaped" )'}}}, True, "echo B escaped")
show_utf8(dl_b, "B")

# ── 3. workspace_upload with Chinese filename ────────────────────────────────
_, dl_c = call({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                "params": {"name": "workspace_upload",
                           "arguments": {"file_name": "测试_CJK_upload.txt",
                                         "data_b64": b64encode(b"hello-cjk").decode("ascii")}}}, True, "upload CJK")
show_utf8(dl_c, "C(server response)")

# ── 4. workspace_list_files: the on-disk name as the server sees it ──────────
_, dl_d = call({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                "params": {"name": "workspace_list_files", "arguments": {}}}, True, "list files")
log(f"D(disk listing) hex: {dl_d.hex()}")
log(f"D(disk listing) utf8: {dl_d.decode('utf-8', errors='replace')!r}")

OUT.write_text("\n".join(lines), encoding="utf-8")
print(f"written: {OUT}")
print("\n".join(lines))
