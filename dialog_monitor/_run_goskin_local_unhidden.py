# -*- coding: utf-8 -*-
"""Fetch unhidden meshes/bones from local Max via MCP_SceneManage.

Default: only query + parse JSON (no DialogMonitor / GoSkin).
Pass --skin to continue into ensure_goskin_ready / run_goskin_skin.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from maxmcp.max_client import MaxClient

HOST = "127.0.0.1"
PORT = 8765

GET_UNHIDDEN_MS = "MCP_SceneManage.getunhidden_meshes_bones()"


def _parse_unhidden_json(raw: Any) -> dict[str, Any]:
    """Parse JSON returned by MCP_SceneManage.getunhidden_meshes_bones()."""
    text = str(raw).strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1]
        text = (
            text.replace('\\"', '"')
            .replace("\\\\", "\\")
            .replace("\\n", "\n")
            .replace("\\r", "\r")
            .replace("\\t", "\t")
        )
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object, got {type(data).__name__}")
    return {
        "mesh_count": int(data.get("mesh_count") or 0),
        "bone_count": int(data.get("bone_count") or 0),
        "meshes": list(data.get("meshes") or []),
        "bones": list(data.get("bones") or []),
        "meshes_handle": list(data.get("meshes_handle") or []),
        "bones_handle": list(data.get("bones_handle") or []),
    }


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skin",
        action="store_true",
        help="Also run GoSkin ensure + prepare (uses MCP_DialogMonitor)",
    )
    args = parser.parse_args(argv)

    client = MaxClient(host=HOST, port=PORT, transport="tcp", timeout=60.0)
    raw = client.send_command(GET_UNHIDDEN_MS, cmd_type="maxscript", timeout=60).get(
        "result"
    )
    scene = _parse_unhidden_json(raw)
    meshes = scene["meshes"]
    bones = scene["bones"]
    print("meshes", meshes)
    print("meshes_handle", scene["meshes_handle"])
    print("bones_count", len(bones), "bone_count_field", scene["bone_count"])
    print("bones", bones)
    print("bones_handle", scene["bones_handle"])

    out = Path(__file__).with_name("_last_goskin_local_unhidden.json")
    if not args.skin:
        out.write_text(
            json.dumps({"scene": scene}, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print("wrote", out)
        print("STOPPED after scene query (pass --skin to run GoSkin)")
        return 0 if meshes and bones else 1

    from dialog_monitor.goskin_flow import ensure_goskin_ready, run_goskin_skin

    print("--- ensure ---")
    ensure = ensure_goskin_ready(client, open_wait_s=10.0)
    print(
        json.dumps(
            {k: ensure.get(k) for k in ("ok", "error", "opened", "already_open")},
            ensure_ascii=False,
            default=str,
        )
    )
    if not ensure.get("ok"):
        out.write_text(
            json.dumps(
                {"scene": scene, "ensure": ensure},
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        print("wrote", out)
        return 1

    if not scene["meshes_handle"] or not scene["bones_handle"]:
        print("ERROR: need unhidden mesh and bones")
        out.write_text(
            json.dumps(
                {"scene": scene, "ensure": ensure, "error": "empty"},
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        return 1

    print("--- run_skin ---")
    skin = run_goskin_skin(
        client,
        mesh_handles=scene["meshes_handle"],
        bone_handles=scene["bones_handle"],
        click_start=False,
    )
    brief = {
        "ok": skin.get("ok"),
        "error": skin.get("error"),
        "awaiting_start_confirm": skin.get("awaiting_start_confirm"),
        "confirmation": skin.get("confirmation"),
        "user_prompt": skin.get("user_prompt"),
        "verify_mesh": (skin.get("steps") or {}).get("verify_mesh"),
        "verify_bones": (skin.get("steps") or {}).get("verify_bones"),
        "select_mesh_scene": (skin.get("steps") or {}).get("select_mesh_scene"),
        "select_bones_scene": (skin.get("steps") or {}).get("select_bones_scene"),
    }
    print(json.dumps(brief, ensure_ascii=False, indent=2, default=str))
    out.write_text(
        json.dumps(
            {"scene": scene, "ensure": ensure, "skin": brief},
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print("wrote", out)
    return 0 if skin.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
