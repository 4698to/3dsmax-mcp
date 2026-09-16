# -*- coding: utf-8 -*-
"""Fast GoSkin prepare flow over HTTP MCP (pauses before 开始蒙皮 by default)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

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


def _as_dict(raw: Any) -> dict[str, Any]:
    data = coerce_payload(raw)
    if isinstance(data, dict) and "ok" in data:
        if not data.get("ok"):
            raise McpHttpError(json.dumps(data, ensure_ascii=False)[:1200])
        inner = data.get("result", data)
        if isinstance(inner, str):
            inner = coerce_payload(inner)
        if isinstance(inner, dict):
            return inner
        return {"result": inner}
    if isinstance(data, dict):
        return data
    raise McpHttpError(f"unexpected tool payload: {type(data).__name__}")


def _idle_online(instances: list) -> list[dict]:
    out = []
    for inst in instances:
        if isinstance(inst, dict) and inst.get("online") and not inst.get("busy"):
            out.append(inst)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    add_common_args(ap)
    ap.add_argument("--instance", default=None, help="Required when >1 idle online Max")
    ap.add_argument("--mesh-names", default=None, help="Comma-separated mesh names")
    ap.add_argument("--bone-names", default=None, help="Comma-separated bone names")
    ap.add_argument(
        "--confirm-start",
        action="store_true",
        help="After prepare, call goskin_confirm_start(user_confirmed=true)",
    )
    ap.add_argument("--keep-lease", action="store_true")
    ap.add_argument("--menu", default="自动蒙皮")
    ap.add_argument("--item", default="GoSkinning")
    args = ap.parse_args()

    mesh_names = [s.strip() for s in (args.mesh_names or "").split(",") if s.strip()] or None
    bone_names = [s.strip() for s in (args.bone_names or "").split(",") if s.strip()] or None

    session = McpHttpSession(args.url, client_name="goskin-dev-flow", timeout=args.timeout)
    acquired = False
    summary: dict[str, Any] = {"ok": False}
    try:
        health = _as_dict(session.call_tool("check_dialog_ocr_health"))
        summary["ocr_health"] = {
            k: health.get(k) for k in ("ok", "status", "endpoints", "workspace") if k in health
        } or health

        listed = coerce_payload(session.call_tool("list_instances"))
        if isinstance(listed, dict) and "ok" in listed:
            listed = envelope_ok(listed)
            listed = coerce_payload(listed)
        if not isinstance(listed, dict):
            die("list_instances failed")
        instances = listed.get("instances") or []
        idle = _idle_online(instances)
        summary["idle_count"] = len(idle)
        summary["idle_names"] = [i.get("name") for i in idle]

        if not idle:
            die("no idle online Max instances")
        if len(idle) > 1 and not args.instance:
            die(
                "multiple idle instances; pass --instance NAME. candidates: "
                + ", ".join(str(n) for n in summary["idle_names"])
            )

        acq_args: dict[str, Any] = {}
        if args.instance:
            acq_args["name"] = args.instance
        acq = _as_dict(session.call_tool("acquire_instance", acq_args))
        if acq.get("code") and not acq.get("acquired"):
            die(json.dumps(acq, ensure_ascii=False))
        acquired = True
        summary["acquired"] = acq

        unhidden = _as_dict(session.call_tool("get_unhidden_meshes_bones"))
        summary["unhidden"] = {
            "meshes": unhidden.get("meshes") or unhidden.get("mesh_names"),
            "bones": unhidden.get("bones") or unhidden.get("bone_names"),
            "meshes_handle": unhidden.get("meshes_handle"),
            "bones_handle": unhidden.get("bones_handle"),
        }

        ready = _as_dict(
            session.call_tool(
                "goskin_ensure_ready",
                {"menu": args.menu, "item": args.item},
            )
        )
        summary["ensure_ready"] = {
            k: ready.get(k) for k in ("ok", "status", "error", "tab") if k in ready
        } or {"keys": list(ready.keys())[:12]}

        run_args: dict[str, Any] = {"click_start": False}
        if mesh_names:
            run_args["mesh_names"] = mesh_names
        if bone_names:
            run_args["bone_names"] = bone_names
        mh = unhidden.get("meshes_handle")
        bh = unhidden.get("bones_handle")
        if not mesh_names and mh:
            run_args["mesh_handles"] = mh
        if not bone_names and bh:
            run_args["bone_handles"] = bh

        prep = _as_dict(session.call_tool("goskin_run_skin", run_args))
        summary["run_skin"] = {
            "awaiting_start_confirm": prep.get("awaiting_start_confirm"),
            "user_prompt": prep.get("user_prompt"),
            "mesh_count": prep.get("mesh_count"),
            "bone_count": prep.get("joint_count") or prep.get("bone_count"),
            "error": prep.get("error") or prep.get("status"),
        }

        if args.confirm_start:
            conf = _as_dict(
                session.call_tool(
                    "goskin_confirm_start",
                    {"user_confirmed": True},
                )
            )
            summary["confirm_start"] = {
                k: conf.get(k) for k in ("ok", "status", "error", "completed") if k in conf
            } or conf

        summary["ok"] = True
        print_json(summary)
        return 0
    except McpHttpError as exc:
        summary["error"] = str(exc)
        print_json(summary)
        return 1
    finally:
        if acquired and not args.keep_lease:
            try:
                session.call_tool("release_instance")
            except Exception as exc:  # noqa: BLE001
                print(f"warn: release_instance failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
