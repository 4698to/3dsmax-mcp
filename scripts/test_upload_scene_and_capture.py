"""Smoke test for the server-side composite tool upload_scene_and_capture.

Verifies: tool advertised -> one-call pipeline works with a real CJK filename ->
download_url fast path returns bytes identical to png_b64 -> return_png_b64=False
omits the inline blob.
"""
import base64
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from open_remote_max import mcp_call, tool_result  # noqa: E402

SRC = r"C:\Users\Administrator\AppData\Roaming\im\199505@nd\RecvFile\肖家鑫_920401\200-召唤_ 计算_20230210_谢锦02_14塌陷_08.max"
OUT = Path(__file__).resolve().parent.parent / "outputs"


def call(data_b64: str, name: str, return_png_b64: bool) -> dict:
    payload = {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
               "params": {"name": "upload_scene_and_capture",
                          "arguments": {"data_b64": data_b64, "file_name": name,
                                        "max_width": 1600,
                                        "return_png_b64": return_png_b64}}}
    res = tool_result(mcp_call(payload))
    assert res.get("ok"), f"tool failed: {json.dumps(res, ensure_ascii=False)[:2000]}"
    return res["result"]


def main() -> None:
    src = Path(SRC)
    if not src.exists():
        sys.exit(f"source not found: {src}")

    mcp_call({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                         "clientInfo": {"name": "upload-scene-capture-test", "version": "1.0"}}})
    mcp_call({"jsonrpc": "2.0", "method": "notifications/initialized"})

    listed = mcp_call({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = [t["name"] for t in listed["result"]["tools"]]
    assert "upload_scene_and_capture" in names, "tool not registered"
    print("tool advertised:", True)

    data_b64 = base64.b64encode(src.read_bytes()).decode("ascii")
    name = src.name

    # run 1: default (inline b64) + validate download_url fast path
    r = call(data_b64, name, True)
    print("scene:", r["scene"])
    print("file_name:", r["file_name"])
    print("local_path:", r["local_path"])
    print("upload_size:", r["upload_size"])
    print("png_size:", r["png_size"], "attempts:", r["attempts"])

    png_b64 = base64.b64decode(r["png_b64"])
    (OUT / "tool_upload_scene_capture.png").write_bytes(png_b64)

    dl = requests.get(r["download_url"], timeout=30)
    assert dl.status_code == 200, f"download_url -> HTTP {dl.status_code}"
    assert dl.content == png_b64, "download_url bytes differ from png_b64"
    print("download_url:", r["download_url"], "-> bytes match:", len(dl.content))

    # run 2: no inline b64, fast path only
    r2 = call(data_b64, name, False)
    assert r2["png_b64"] == "", "expected png_b64 omitted"
    dl2 = requests.get(r2["download_url"], timeout=30)
    assert dl2.status_code == 200 and len(dl2.content) == r2["png_size"]
    print("run2: png_b64 omitted, GET size:", len(dl2.content))

    print("ALL PASS")


if __name__ == "__main__":
    main()
