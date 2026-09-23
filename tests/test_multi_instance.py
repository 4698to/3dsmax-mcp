"""End-to-end test: multiple 3ds Max instances, per-user exclusivity, explicit release.

Run standalone (no 3ds Max required):

    .venv\\Scripts\\python tests\\test_multi_instance.py

It starts two fake "3ds Max" TCP responders and one MCP server configured with
MAXMCP_INSTANCES pointing at both, then exercises two MCP client sessions to
verify: instance listing, exclusive acquire, command routing to the held
instance, busy conflict, explicit release, and instance switching.
"""

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

# Must be set before src.server is imported (registry reads env at import time).
os.environ["MAXMCP_INSTANCES"] = "127.0.0.1:19001:maxA,127.0.0.1:19002:maxB"
os.environ["MAXMCP_LOCK_TTL"] = "60"

MCP_URL = "http://127.0.0.1:18000/mcp"


class FakeMaxServer:
    """Minimal TCP responder mimicking the 3ds Max MCP listener."""

    def __init__(self, port: int, name: str):
        self.port = port
        self.name = name
        self.received: list[dict] = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", port))
        self.sock.listen(5)
        self.sock.settimeout(0.2)
        self._stop = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                with conn:
                    conn.settimeout(2.0)
                    data = b""
                    while b"\n" not in data:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        data += chunk
                    req = json.loads(data.decode("utf-8").strip())
                    self.received.append(
                        {
                            "port": self.port,
                            "name": self.name,
                            "type": req.get("type"),
                            "command": (req.get("command") or "")[:60],
                        }
                    )
                    resp = {
                        "success": True,
                        "requestId": req.get("requestId"),
                        "result": json.dumps(
                            {"port": self.port, "name": self.name, "echo": req.get("command", "")[:40]}
                        ),
                        "error": "",
                        "meta": {"protocolVersion": 2, "durationMs": 1},
                    }
                    conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
            except Exception as exc:  # noqa: BLE001 - test harness
                print(f"[fake {self.name}] error: {exc}")

    def stop(self) -> None:
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


async def main() -> None:
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    fake_a = FakeMaxServer(19001, "maxA")
    fake_b = FakeMaxServer(19002, "maxB")

    # Build and start the MCP server against the fake instances.
    from src import server  # noqa: E402

    import uvicorn

    uvicorn_server = uvicorn.Server(
        uvicorn.Config(server.mcp.streamable_http_app(), host="127.0.0.1", port=18000, log_level="warning")
    )
    threading.Thread(target=uvicorn_server.run, daemon=True).start()
    wait_for_port(18000)

    def text_of(result) -> str:
        return "".join(c.text for c in result.content if c.type == "text")

    async def tool(session: ClientSession, name: str, args: dict | None = None):
        result = await session.call_tool(name, args or {})
        raw = text_of(result)
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return raw

    checks = []

    def check(label: str, condition: bool, detail: str = "") -> None:
        checks.append((label, condition))
        status = "PASS" if condition else "FAIL"
        print(f"  [{status}] {label}" + (f" -- {detail}" if detail else ""))
        if not condition:
            raise AssertionError(f"check failed: {label} {detail}")

    async with streamable_http_client(MCP_URL, http_client=httpx.AsyncClient(timeout=30.0)) as (r1, w1, _sid1):
        async with streamable_http_client(MCP_URL, http_client=httpx.AsyncClient(timeout=30.0)) as (r2, w2, _sid2):
            async with ClientSession(r1, w1) as sess_a, ClientSession(r2, w2) as sess_b:
                await sess_a.initialize()
                await sess_b.initialize()

                # 1. Both instances are idle.
                listing = await tool(sess_a, "list_instances")
                check("list_instances reports 2 instances", len(listing["instances"]) == 2, str(listing))
                check("both instances idle initially", all(not i["busy"] for i in listing["instances"]))

                # 2. Session A acquires the first idle instance (maxA).
                acquired_a = await tool(sess_a, "acquire_instance")
                check("session A acquired an instance", acquired_a.get("acquired") is True, str(acquired_a))
                mine_a = await tool(sess_a, "get_my_instance")
                check("session A holds maxA", mine_a["acquired"] and mine_a["instance"]["name"] == "maxA", str(mine_a))

                # 3. A's command routes to maxA only.
                await tool(sess_a, "execute_maxscript", {"code": "1+1"})
                time.sleep(0.3)
                check("maxA received A's command", any(r["name"] == "maxA" for r in fake_a.received))
                check("maxB did not receive A's command", len(fake_b.received) == 0)

                # 4. Session B cannot take maxA while A holds it.
                busy = await tool(sess_b, "acquire_instance", {"name": "maxA"})
                check("B's acquire of maxA is rejected (busy)", busy.get("acquired") is False and "busy" in str(busy).lower(), str(busy))

                # 5. B falls back to maxB and routes there.
                acquired_b = await tool(sess_b, "acquire_instance")
                check("session B acquired an instance", acquired_b.get("acquired") is True, str(acquired_b))
                await tool(sess_b, "execute_maxscript", {"code": "2+2"})
                time.sleep(0.3)
                check("maxB received B's command", any(r["name"] == "maxB" for r in fake_b.received))

                # 6. list_instances reflects both busy.
                listing2 = await tool(sess_a, "list_instances")
                by_name = {i["name"]: i for i in listing2["instances"]}
                check("maxA busy", by_name["maxA"]["busy"])
                check("maxB busy", by_name["maxB"]["busy"])

                # 7. A releases; maxA becomes idle.
                released = await tool(sess_a, "release_instance")
                check("A released maxA", released.get("released") == "maxA" and released.get("idle"), str(released))
                listing3 = await tool(sess_a, "list_instances")
                by_name3 = {i["name"]: i for i in listing3["instances"]}
                check("maxA idle after release", not by_name3["maxA"]["busy"])

                # 8. B switches from maxB to the now-free maxA.
                switched = await tool(sess_b, "acquire_instance", {"name": "maxA"})
                check("B switched to maxA", switched.get("acquired") is True and switched["instance"]["name"] == "maxA", str(switched))
                mine_b = await tool(sess_b, "get_my_instance")
                check("B now holds maxA", mine_b["acquired"] and mine_b["instance"]["name"] == "maxA", str(mine_b))
                listing4 = await tool(sess_a, "list_instances")
                by_name4 = {i["name"]: i for i in listing4["instances"]}
                check("maxB idle after B switched away", not by_name4["maxB"]["busy"])

                # 9. A releases again while holding nothing -> clear message.
                release_none = await tool(sess_a, "release_instance")
                check("releasing with nothing held is reported", release_none.get("released") is False, str(release_none))

    fake_a.stop()
    fake_b.stop()
    print(f"\nALL {sum(1 for _, ok in checks if ok)} CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError:
        sys.exit(1)
