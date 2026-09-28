# -*- coding: utf-8 -*-
"""Shared CLI helpers for remote skill scripts."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--url",
        default=None,
        help="MCP endpoint (required unless env MAXMCP_URL is set; must match IDE mcp.json)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=600.0,
        help="HTTP timeout seconds (default 600)",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="Resume an existing MCP HTTP session (the Mcp-Session-Id from a prior run)",
    )
    parser.add_argument(
        "--session-id-file",
        default=None,
        help="File holding the Mcp-Session-Id to resume; a fresh session id is also saved here",
    )


def build_session(
    args: argparse.Namespace, *, client_name: str = "3dsmax-mcp-remote-skill"
) -> Any:
    from .mcp_http import McpHttpSession

    return McpHttpSession(
        args.url,
        client_name=client_name,
        timeout=args.timeout,
        session_id=args.session_id,
        session_id_file=args.session_id_file,
    )


def print_json(obj: Any) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def die(msg: str, code: int = 1) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(code)
