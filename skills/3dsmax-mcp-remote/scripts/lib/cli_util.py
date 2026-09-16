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
