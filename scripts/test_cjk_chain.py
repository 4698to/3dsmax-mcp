"""Test whether CJK survives the Python-client -> HTTP MCP -> Max chain."""
import json
import requests
import base64

ENDPOINT = "http://192.168.139.76:8000/mcp"
session_id = None

def mcp_call(payload, timeout=300):
    global session_id
    headers = {"Accept": "application/json, text/event-stream"}
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    r = requests.post(ENDPOINT, data=body,
                      headers={**headers, "Content-Type": "application/json; charset=utf-8"},
                      timeout=timeout)
    if not session_id and "Mcp-Session-Id" in r.headers:
        session_id = r.headers["Mcp-Session-Id"]
    text = r.text
    # extract first SSE data: line
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            return line[5:].strip()
    return text

# handshake
init = mcp_call({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                            "clientInfo": {"name": "py-cjk-test", "version": "1.0"}}})
mcp_call({"jsonrpc": "2.0", "method": "notifications/initialized"})

# Test 1: CJK in an execute_maxscript command must round-trip
code = '( "雷世坤_RoundTrip" )'
r1 = mcp_call({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
               "params": {"name": "execute_maxscript", "arguments": {"command": code}}})
print("T1 execute_maxscript CJK roundtrip:")
print(r1)
ok1 = "雷世坤_RoundTrip" in r1
print("T1 PASS" if ok1 else "T1 FAIL")

# Test 2: upload a small file with a CJK file_name, then list the workspace
small = b"hello cjk test"
b64 = base64.b64encode(small).decode("ascii")
r2 = mcp_call({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
               "params": {"name": "workspace_upload",
                          "arguments": {"file_name": "cjk_测试_upload.txt", "data_b64": b64}}})
print("\nT2 workspace_upload CJK file_name:")
print(r2)

code2 = '''(
    ws = @"C:\\Users\\199505\\AppData\\Local\\Temp\\3dsmax-mcp\\workspace\\"
    fs = getFiles (ws + "cjk_*.txt")
    if fs.count == 0 then "NO_MATCH" else ((for f in fs collect (getFilenameFile f)) as string)
)'''
r3 = mcp_call({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
               "params": {"name": "execute_maxscript", "arguments": {"command": code2}}})
print("\nT3 workspace list for cjk_*.txt:")
print(r3)
ok3 = "cjk_测试_upload.txt" in r3
print("T3 PASS" if ok3 else "T3 FAIL")
