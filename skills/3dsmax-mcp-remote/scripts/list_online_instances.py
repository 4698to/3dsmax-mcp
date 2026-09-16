# -*- coding: utf-8 -*-
"""List online 3ds Max instances via remote MCP (HTTP)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from lib.cli_util import add_common_args, die, print_json
from lib.mcp_http import McpHttpError, McpHttpSession, coerce_payload, envelope_ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    add_common_args(ap)
    ap.add_argument("--json", action="store_true", help="Print full list_instances JSON")
    ap.add_argument(
        "--all",
        action="store_true",
        help="Include offline instances (default: online only)",
    )
    args = ap.parse_args()

    try:
        session = McpHttpSession(args.url, client_name="list-online-instances", timeout=args.timeout)
        raw = session.call_tool("list_instances")
        data = coerce_payload(envelope_ok(raw) if isinstance(raw, dict) and "ok" in raw else raw)
        if isinstance(data, dict) and "ok" in data and "result" in data:
            data = coerce_payload(envelope_ok(data))
        if not isinstance(data, dict):
            die(f"unexpected list_instances payload: {data!r}")
        if args.json:
            print_json(data)
            return 0

        instances = data.get("instances") or []
        rows = []
        for inst in instances:
            if not isinstance(inst, dict):
                continue
            if not args.all and not inst.get("online"):
                continue
            rows.append(inst)

        print(
            f"online_filter={'off' if args.all else 'on'}  "
            f"queue_depth={data.get('queue_depth')}  "
            f"lease_idle_seconds={data.get('lease_idle_seconds')}"
        )
        if not rows:
            print("(no matching instances)")
            return 0
        for inst in rows:
            name = inst.get("name", "?")
            host = inst.get("host", "")
            port = inst.get("port", "")
            busy = inst.get("busy")
            online = inst.get("online")
            transports = inst.get("transports") or []
            print(
                f"- {name}\t{host}:{port}\tonline={online}\tbusy={busy}\t"
                f"transports={','.join(map(str, transports))}"
            )
        return 0
    except McpHttpError as exc:
        die(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
