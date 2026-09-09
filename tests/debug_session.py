"""Debug: check Mcp-Session-Id handling on the real HTTP path (SSE-aware)."""
import asyncio
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["MAXMCP_INSTANCES"] = "127.0.0.1:19001:maxA,127.0.0.1:19002:maxB"
os.environ["MAXMCP_LOCK_TTL"] = "60"

ACCEPT = "application/json, text/event-stream"


class FakeMaxServer:
    def __init__(self, port: int, name: str):
        self.port = port
        self.name = name
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", port))
        self.sock.listen(5)
        self.sock.settimeout(0.2)
        self._stop = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                conn.settimeout(2.0)
                data = b""
                while b"\n" not in data:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                req = json.loads(data.decode("utf-8").strip())
                resp = {
                    "success": True,
                    "requestId": req.get("requestId"),
                    "result": json.dumps({"name": self.name, "port": self.port}),
                    "error": "",
                    "meta": {"protocolVersion": 2, "durationMs": 1},
                }
                conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))

    def stop(self):
        self._stop.set()
        try:
            self.sock.close()
        except OSError:
            pass


def wait_for_port(port: int, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"port {port} did not open in time")


def parse_sse(raw: str) -> list[dict]:
    """Extract JSON payloads from an SSE text body."""
    out = []
    for block in raw.split("\n\n"):
        data_lines = [
            ln[len("data:"):].strip()
            for ln in block.splitlines()
            if ln.startswith("data:")
        ]
        if data_lines:
            out.append(json.loads("".join(data_lines)))
    return out


async def main() -> None:
    import httpx

    fake_a = FakeMaxServer(19001, "maxA")
    fake_b = FakeMaxServer(19002, "maxB")

    from src import server

    import uvicorn

    uvicorn_server = uvicorn.Server(
        uvicorn.Config(server.mcp.streamable_http_app(), host="127.0.0.1", port=18000, log_level="warning")
    )
    threading.Thread(target=uvicorn_server.run, daemon=True).start()
    wait_for_port(18000)

    async with httpx.AsyncClient(timeout=15.0) as client:
        init = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "debug", "version": "0"},
            },
        }
        resp = await client.post("http://127.0.0.1:18000/mcp", json=init, headers={"Accept": ACCEPT})
        print("initialize status:", resp.status_code)
        print("initialize headers:", dict(resp.headers))
        sid = resp.headers.get("mcp-session-id")
        print("extracted session id:", sid)
        events = parse_sse(resp.text)
        print("initialize events:", [e.get("method") or e.get("result", {}).get("serverInfo") for e in events])

        # notifications/initialized must be sent before tools/call
        await client.post(
            "http://127.0.0.1:18000/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers={"Accept": ACCEPT, "mcp-session-id": sid},
        )

        call = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "acquire_instance", "arguments": {}},
        }
        resp2 = await client.post(
            "http://127.0.0.1:18000/mcp",
            json=call,
            headers={"Accept": ACCEPT, "mcp-session-id": sid},
        )
        print("call status:", resp2.status_code)
        ev2 = parse_sse(resp2.text)
        for e in ev2:
            if "result" in e:
                content = e["result"].get("content", [])
                print("call result content:", [c.get("text") for c in content if c.get("type") == "text"])
            else:
                print("call event:", e)

    fake_a.stop()
    fake_b.stop()


if __name__ == "__main__":
    asyncio.run(main())
