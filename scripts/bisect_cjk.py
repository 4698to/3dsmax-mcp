"""Bisect where CJK is corrupted in the HTTP MCP server."""
import json
import requests

ENDPOINT = "http://192.168.139.76:8000/mcp"
session_id = None

def call(payload_raw: bytes, headers_extra=None, timeout=120):
    global session_id
    headers = {"Accept": "application/json, text/event-stream",
               "Content-Type": "application/json; charset=utf-8"}
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    if headers_extra:
        headers.update(headers_extra)
    r = requests.post(ENDPOINT, data=payload_raw, headers=headers, timeout=timeout)
    if not session_id and "Mcp-Session-Id" in r.headers:
        session_id = r.headers["Mcp-Session-Id"]
    for line in r.text.splitlines():
        if line.strip().startswith("data:"):
            return line[5:].strip()
    return r.text

def jsondumps(payload, ensure_ascii):
    return json.dumps(payload, ensure_ascii=ensure_ascii).encode("utf-8")

# handshake
call(jsondumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                           "clientInfo": {"name": "bisect", "version": "1.0"}}}, True))
call(jsondumps({"jsonrpc": "2.0", "method": "notifications/initialized"}, True))

# A: raw UTF-8 body (ensure_ascii=False) - chars sent literally as UTF-8 bytes
body_a = jsondumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "execute_maxscript",
                               "arguments": {"command": '( "测试A_RawUTF8" )'}}}, False)
ra = call(body_a)
print("A raw-UTF8:", ra)

# B: escaped body (ensure_ascii=True) - \uXXXX ASCII
body_b = jsondumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "execute_maxscript",
                               "arguments": {"command": '( "测试B_Escaped" )'}}}, True)
rb = call(body_b)
print("B escaped :", rb)

# C: same but content-type WITHOUT charset
r = requests.post(ENDPOINT,
                  data=jsondumps({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                  "params": {"name": "execute_maxscript",
                                             "arguments": {"command": '( "测试C_NoCharset" )'}}}, False),
                  headers={"Accept": "application/json, text/event-stream",
                           "Content-Type": "application/json",
                           "Mcp-Session-Id": session_id}, timeout=120)
rc = "\n".join(l[5:].strip() for l in r.text.splitlines() if l.strip().startswith("data:"))
print("C no-charset:", rc)
