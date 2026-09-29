# -*- coding: utf-8 -*-
"""One-shot GoSkin job submission to the shared queue (submit_goskin_job).

Uploads a local .max (HTTP multipart), then calls ``submit_goskin_job``.
``user_id`` is a best-effort audit field (the agent may not be able to obtain
the user identifier; empty is accepted — the server normalizes it to null and
falls back to owner/session auditing). Prints ``{job_id, status,
queue_position, user_id, confirm_mode, status_url}``. Optional ``--poll``
waits until a terminal status (succeeded/failed/cancelled).

Auto skinning must go through this queue path only; do not use
``goskin_dev_flow.py`` for production jobs (it is a low-level OCR debug flow).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from lib.cli_util import add_common_args, build_session, die, print_json
from lib.mcp_http import McpHttpError, coerce_payload

TERMINAL = {"succeeded", "failed", "cancelled"}


def _call_dict(session: Any, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    data = coerce_payload(session.call_tool(name, arguments))
    if isinstance(data, dict) and data.get("ok") is False:
        payload = dict(data)
        payload.setdefault("failed_tool", name)
        raise McpHttpError(json_dumps(payload)[:1600])
    if not isinstance(data, dict):
        raise McpHttpError(f"unexpected payload from {name}: {data!r}")
    return data


def json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _resolve_user_id(args: argparse.Namespace) -> str:
    """Best-effort audit user id. The agent may not be able to obtain the user
    identifier — empty is acceptable: never block submission over it. The
    server normalizes an empty user_id to null and falls back to owner/session
    auditing."""
    return (args.user_id or os.environ.get("MAXMCP_USER_ID") or "").strip()


def _split_names(raw: str | None) -> list[str] | None:
    out = [s.strip() for s in (raw or "").split(",") if s.strip()]
    return out or None


def _poll(
    session: Any, job_id: str, timeout: float, interval: float
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        data = _call_dict(session, "get_goskin_job", {"job_id": job_id})
        status = data.get("status")
        if status in TERMINAL or time.monotonic() >= deadline:
            return data
        time.sleep(interval)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    add_common_args(ap)
    ap.add_argument("scene", nargs="?", default=None, help="Local .max to upload")
    ap.add_argument(
        "--local-path",
        default=None,
        help="Server-side local_path from a prior upload; skip upload (mutually "
        "exclusive with the scene argument)",
    )
    ap.add_argument(
        "--user-id",
        default=None,
        help="Best-effort audit user id (fallback: env MAXMCP_USER_ID; empty is "
        "fine — server falls back to owner/session audit). When set, also sent "
        "as X-Maxmcp-User-Id so the server records it even if the argument is empty",
    )
    ap.add_argument(
        "--confirm-mode",
        default="auto",
        choices=["auto", "manual"],
        help="auto (default) = server starts skinning automatically; "
        "manual = stop at awaiting_confirm until confirm_goskin_job",
    )
    ap.add_argument(
        "--instance",
        default=None,
        help="Pin to a specific shared/jobs-pool instance (name from list_instances)",
    )
    ap.add_argument("--mesh-names", default=None, help="Comma-separated mesh names")
    ap.add_argument("--bone-names", default=None, help="Comma-separated bone names")
    ap.add_argument(
        "--priority", default=None, choices=["high", "normal", "low"]
    )
    ap.add_argument(
        "--preserve-scene",
        action="store_true",
        help="Do not reset the assigned instance's scene when scene_local_path is omitted",
    )
    ap.add_argument(
        "--debug",
        action="store_true",
        help="Queue-only simulated job (never dispatched to 3ds Max)",
    )
    ap.add_argument(
        "--poll",
        action="store_true",
        help="Poll get_goskin_job until a terminal status (or --poll-timeout)",
    )
    ap.add_argument("--poll-timeout", type=float, default=1800.0, help="Seconds (default 1800)")
    ap.add_argument("--poll-interval", type=float, default=5.0, help="Seconds (default 5)")
    args = ap.parse_args()

    if args.scene and args.local_path:
        die("give either the scene file or --local-path, not both")
    if not args.scene and not args.local_path:
        die("need a scene file (uploaded to the queue) or --local-path from a prior upload")

    uid = _resolve_user_id(args)
    if uid and not getattr(args, "audit_user_id", None):
        args.audit_user_id = uid
    session = build_session(args, client_name="goskin-submit")

    summary: dict[str, Any] = {"ok": False}
    try:
        if args.local_path:
            local_path = args.local_path
        else:
            src = Path(args.scene).expanduser()
            if not src.is_file():
                raise McpHttpError(f"scene file not found: {src}")
            uploaded = session.upload_file_multipart(src)
            local_path = str(uploaded.get("local_path") or "").strip()
            if not local_path:
                raise McpHttpError(
                    "upload returned no local_path: " + json_dumps(uploaded)[:500]
                )
            summary["upload"] = {
                "name": uploaded.get("name"),
                "local_path": local_path,
                "size": uploaded.get("size"),
            }

        submit_args: dict[str, Any] = {"scene_local_path": local_path}
        if uid:
            submit_args["user_id"] = uid
        if args.instance:
            submit_args["instance"] = args.instance
        if args.confirm_mode:
            submit_args["confirm_mode"] = args.confirm_mode
        mesh_names = _split_names(args.mesh_names)
        bone_names = _split_names(args.bone_names)
        if mesh_names:
            submit_args["mesh_names"] = mesh_names
        if bone_names:
            submit_args["bone_names"] = bone_names
        if args.priority:
            submit_args["priority"] = args.priority
        if args.preserve_scene:
            submit_args["preserve_scene"] = True
        if args.debug:
            submit_args["debug"] = True

        submitted = _call_dict(session, "submit_goskin_job", submit_args)
        summary["ok"] = bool(submitted.get("status"))
        summary["submitted"] = submitted

        if args.poll:
            job_id = submitted.get("job_id") or ""
            if not job_id:
                raise McpHttpError("submit returned no job_id")
            summary["final"] = _poll(
                session, job_id, args.poll_timeout, args.poll_interval
            )

        print_json(summary)
        return 0
    except McpHttpError as exc:
        summary["error"] = str(exc)
        print_json(summary)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
