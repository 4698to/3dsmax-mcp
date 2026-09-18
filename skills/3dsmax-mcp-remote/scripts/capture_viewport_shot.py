# -*- coding: utf-8 -*-
"""Acquire a Max lease, capture viewport, download PNG via HTTP.

Do not queue for a screenshot when the target is busy: pass --no-acquire
(see remote skill instance-locks.md). Capture talks to Max briefly but does
not require an exclusive lease the way GoSkin does.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from lib.cli_util import add_common_args, die, print_json
from lib.mcp_http import (
    McpHttpError,
    McpHttpSession,
    coerce_payload,
    envelope_ok,
)


def _tool_dict(raw) -> dict:
    data = coerce_payload(raw)
    if isinstance(data, dict) and "ok" in data:
        data = envelope_ok(data)
        data = coerce_payload(data)
    if not isinstance(data, dict):
        raise McpHttpError(f"expected dict tool result, got {type(data).__name__}")
    return data


def _instance_busy(listed: dict, name: str | None) -> bool:
    if not name:
        return False
    for inst in listed.get("instances") or []:
        if isinstance(inst, dict) and inst.get("name") == name:
            return bool(inst.get("busy"))
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    add_common_args(ap)
    ap.add_argument("--instance", default=None, help="Instance name from list_instances")
    ap.add_argument("--out", default=None, help="Output PNG path")
    ap.add_argument("--max-width", type=int, default=1600)
    ap.add_argument(
        "--source",
        default="auto",
        choices=["auto", "agent", "active"],
        help="capture_viewport source",
    )
    ap.add_argument(
        "--keep-lease",
        action="store_true",
        help="Do not call release_instance after capture",
    )
    ap.add_argument(
        "--no-acquire",
        action="store_true",
        help=(
            "Skip acquire_instance (use when target is busy or this session "
            "already holds Max — never wait in the lease queue just to screenshot)"
        ),
    )
    ap.add_argument("--json", action="store_true", help="Print capture metadata JSON")
    args = ap.parse_args()

    out = Path(args.out) if args.out else Path(
        f"viewport_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    )

    session = McpHttpSession(args.url, client_name="capture-viewport-shot", timeout=args.timeout)
    acquired = False
    try:
        skip_acquire = bool(args.no_acquire)
        if not skip_acquire and args.instance:
            # Avoid 60s WAIT_TIMEOUT when another agent already holds the Max.
            listed_raw = coerce_payload(session.call_tool("list_instances"))
            if isinstance(listed_raw, dict) and "ok" in listed_raw:
                listed_raw = envelope_ok(listed_raw)
                listed_raw = coerce_payload(listed_raw)
            if isinstance(listed_raw, dict) and _instance_busy(listed_raw, args.instance):
                print(
                    f"warn: {args.instance} is busy — capturing with --no-acquire "
                    f"(do not queue for screenshots)",
                    file=sys.stderr,
                )
                skip_acquire = True

        if not skip_acquire:
            acq_args = {}
            if args.instance:
                acq_args["name"] = args.instance
            acq = _tool_dict(session.call_tool("acquire_instance", acq_args))
            if acq.get("acquired") is False or acq.get("code"):
                code = acq.get("code") or ""
                if code in {"WAIT_TIMEOUT", "INSTANCE_BUSY", "QUEUE_FULL", "NO_FREE_INSTANCE"}:
                    die(
                        json.dumps(acq, ensure_ascii=False)
                        + "\nHint: for screenshots use --no-acquire instead of waiting in the lease queue."
                    )
                die(json.dumps(acq, ensure_ascii=False))
            acquired = True

        cap = _tool_dict(
            session.call_tool(
                "capture_viewport",
                {"max_width": int(args.max_width), "source": args.source},
            )
        )
        # Envelope may already be unwrapped to result dict with file/download_url
        meta = cap.get("result", cap) if "file" not in cap and "result" in cap else cap
        if isinstance(meta, str):
            meta = coerce_payload(meta)
        if not isinstance(meta, dict):
            die(f"unexpected capture payload: {cap!r}")

        download_url = meta.get("download_url")
        file_path = meta.get("file") or ""
        if download_url:
            session.download_url(str(download_url), out)
        elif file_path:
            base = Path(str(file_path).replace("\\", "/")).name
            session.download_file_name(base, out)
        else:
            die(f"no download_url/file in capture result: {json.dumps(meta, ensure_ascii=False)[:800]}")

        summary = {
            "ok": True,
            "out": str(out.resolve()),
            "bytes": out.stat().st_size,
            "capture": {k: meta.get(k) for k in ("file", "download_url", "width", "height", "source") if k in meta},
            "acquired": acquired,
            "no_acquire": skip_acquire,
        }
        if args.json:
            print_json(summary)
        else:
            print(f"saved {out} ({summary['bytes']} bytes)")
            if download_url:
                print(f"download_url {download_url}")
        return 0
    except McpHttpError as exc:
        die(str(exc))
        return 1
    finally:
        if acquired and not args.keep_lease:
            try:
                session.call_tool("release_instance")
            except Exception as exc:  # noqa: BLE001
                print(f"warn: release_instance failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
