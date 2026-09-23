# -*- coding: utf-8 -*-
"""Upload a local file to the MCP server workspace (HTTP multipart preferred)."""

from __future__ import annotations

import argparse
import base64
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
    ap.add_argument("path", help="Local file to upload")
    ap.add_argument(
        "--via-mcp",
        action="store_true",
        help="Use workspace_upload (base64) instead of POST /files/upload",
    )
    ap.add_argument(
        "--name",
        default=None,
        help="Remote file_name for --via-mcp (default: local basename)",
    )
    args = ap.parse_args()

    src = Path(args.path)
    if not src.is_file():
        die(f"not a file: {src}")

    session = McpHttpSession(args.url, client_name="upload-to-mcp", timeout=args.timeout)
    try:
        if args.via_mcp:
            data_b64 = base64.b64encode(src.read_bytes()).decode("ascii")
            file_name = args.name or src.name
            raw = session.call_tool(
                "workspace_upload",
                {"file_name": file_name, "data_b64": data_b64},
            )
            data = coerce_payload(raw)
            if isinstance(data, dict) and "ok" in data:
                data = envelope_ok(data)
            print_json(data if isinstance(data, dict) else {"result": data})
            return 0

        info = session.upload_file_multipart(src)
        print_json(info)
        return 0
    except McpHttpError as exc:
        die(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
