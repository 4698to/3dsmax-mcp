"""Probe remote Max after writeResponseLine fix (handles UTF-8 BOM)."""
from __future__ import annotations

import json
import socket
from uuid import uuid4

HOST = "192.168.139.45"
PORT = 8765


def raw(payload: dict, wait: float = 6.0) -> bytes:
    data = (json.dumps(payload, ensure_ascii=True) + "\n").encode("utf-8")
    sock = socket.create_connection((HOST, PORT), timeout=5)
    sock.sendall(data)
    sock.settimeout(wait)
    buf = b""
    while True:
        try:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
            if b"\n" in buf:
                break
        except OSError:
            break
    sock.close()
    return buf


def parse(buf: bytes) -> dict:
    if buf.startswith(b"\xef\xbb\xbf"):
        buf = buf[3:]
    return json.loads(buf.decode("utf-8").strip())


def call(command: str, cmd_type: str = "maxscript") -> dict:
    return parse(
        raw(
            {
                "command": command,
                "type": cmd_type,
                "requestId": uuid4().hex,
                "protocolVersion": 2,
            }
        )
    )


def main() -> None:
    print("=== ping ===")
    print(json.dumps(call("", "ping"), ensure_ascii=False, indent=2))

    print("=== objects.count ===")
    print(json.dumps(call("objects.count as string"), ensure_ascii=False, indent=2))

    print("=== overview ===")
    overview = (
        '"obj=" + (objects.count as string) + '
        '" sel=" + (selection.count as string) + '
        '" file=" + maxFileName'
    )
    print(json.dumps(call(overview), ensure_ascii=False, indent=2))

    print("=== root names ===")
    roots = (
        "(local a=\"[\"; local n=0; "
        "for o in objects where o.parent == undefined do ("
        "n += 1; if n > 1 do a += \",\"; "
        "a += \"\\\"\" + o.name + \"\\\"\"); "
        "a + \"]\")"
    )
    print(json.dumps(call(roots), ensure_ascii=False, indent=2))

    print("=== class counts ===")
    classes = (
        "(local names=#(); local counts=#(); "
        "for o in objects do ("
        "local cn=(classOf o) as string; local idx=0; "
        "for i=1 to names.count where names[i]==cn do idx=i; "
        "if idx==0 then (append names cn; append counts 1) else counts[idx]+=1); "
        "local s=\"{\"; "
        "for i=1 to names.count do ("
        "if i>1 do s+=\",\"; "
        "s+=\"\\\"\"+names[i]+\"\\\":\"+(counts[i] as string)); "
        "s+\"}\")"
    )
    print(json.dumps(call(classes), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
