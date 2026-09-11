"""Open a local .max file on the remote Max machine and fetch a viewport capture.

Pipeline (CJK-safe): workspace_upload (keeps the original filename) -> load_scene
with the returned local_path -> capture_viewport -> copyfile capture into the
workspace -> workspace_download -> save the PNG locally.

Usage: python open_remote_max.py "<source.max>" "<out.png>"
"""
import argparse
import base64
import json
import sys
import time
from pathlib import Path

import requests

ENDPOINT = "http://192.168.139.76:8000/mcp"
session_id = None


def mcp_call(payload, timeout=600):
    """POST one JSON-RPC message over the MCP streamable-http transport."""
    global session_id
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json; charset=utf-8",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    r = requests.post(ENDPOINT, data=body, headers=headers, timeout=timeout)
    if not session_id and "Mcp-Session-Id" in r.headers:
        session_id = r.headers["Mcp-Session-Id"]
    # SSE: the reply arrives as a `data:` line; decode the raw bytes as UTF-8.
    for raw in r.content.splitlines():
        line = raw.strip()
        if line.startswith(b"data:"):
            return json.loads(line[5:].strip().decode("utf-8"))
    if not r.content.strip():
        return None  # notifications get a 202 with an empty body
    return json.loads(r.content.decode("utf-8"))


def tool_result(obj):
    """Unwrap a tools/call response into the inner {ok, result, ...} dict."""
    res = obj["result"]
    if res.get("structuredContent") is not None:
        return res["structuredContent"]
    return json.loads(res["content"][0]["text"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="path of the local .max file")
    ap.add_argument("out_png", help="local path to save the capture PNG")
    args = ap.parse_args()

    src = Path(args.source)
    if not src.exists():
        sys.exit(f"source not found: {src}")
    file_name = src.name
    print(f"source: {src}  ({src.stat().st_size} bytes)")

    mcp_call({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                         "clientInfo": {"name": "open-remote-max", "version": "1.0"}}})
    mcp_call({"jsonrpc": "2.0", "method": "notifications/initialized"})

    # 1. upload (original filename, incl. CJK/space)
    data_b64 = base64.b64encode(src.read_bytes()).decode("ascii")
    up = tool_result(mcp_call({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                               "params": {"name": "workspace_upload",
                                          "arguments": {"file_name": file_name,
                                                        "data_b64": data_b64}}}))
    print("upload ok:", up.get("ok"))
    r_up = up["result"]
    local_path = r_up["local_path"]
    print("local_path:", local_path)

    # 2. load scene
    ls = tool_result(mcp_call({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                               "params": {"name": "load_scene",
                                          "arguments": {"file_path": local_path}}}))
    print("load_scene ok:", ls.get("ok"), "->", ls.get("result"))
    time.sleep(3)  # let the viewport redraw the freshly loaded scene

    # 3-5. capture -> move into workspace -> download, retrying while the
    #      capture is still a tiny placeholder (viewport not yet rendered).
    png = b""
    for attempt in range(1, 4):
        cv = tool_result(mcp_call({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                   "params": {"name": "capture_viewport",
                                              "arguments": {"source": "auto",
                                                            "max_width": 1600}}}))
        cap_file = cv["result"]["file"]
        print(f"capture #{attempt} file:", cap_file)

        ws_dir = r_up["local_path"].rsplit("\\", 1)[0]
        base = cap_file.rsplit("\\", 1)[-1]
        dst = ws_dir + "\\" + base
        code = f'copyfile @"{cap_file}" @"{dst}"'
        cp = tool_result(mcp_call({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                                   "params": {"name": "execute_maxscript",
                                              "arguments": {"command": f"( {code} )"}}}))
        print("copyfile ok:", cp.get("ok"), "->", cp.get("result"))

        dl = tool_result(mcp_call({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                                   "params": {"name": "workspace_download",
                                              "arguments": {"file_name": base}}}))
        r_dl = dl["result"]
        png = base64.b64decode(r_dl["data_b64"])
        print(f"download #{attempt}: {len(png)} bytes")
        if len(png) >= 10_000:
            break
        print("  capture too small, waiting and retrying...")
        time.sleep(4)

    out = Path(args.out_png)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(png)
    print(f"saved: {out}  ({len(png)} bytes)")


if __name__ == "__main__":
    main()
