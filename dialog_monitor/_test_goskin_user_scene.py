# -*- coding: utf-8 -*-
"""Load user scene on remote Max and run GoSkin up to confirm gate."""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from maxmcp.max_client import MaxClient
from maxmcp.workspace_config import (
    clear_workspace_cache,
    ensure_workspace_dir,
    get_shared_workspace,
)
from dialog_monitor.click_button import (
    _escape_ms_string,
    _exec_ms,
    ensure_dialog_monitor_loaded,
    recognize_dialog,
)
from dialog_monitor.goskin_flow import ensure_goskin_ready, run_goskin_skin

SRC = Path(r"d:\Documents\3dsMax\测试模拟鼠标点击.max")
HOST = "192.168.139.45"
PORT = 8765


def _list_scene(client: MaxClient) -> dict:
    code = r"""(
local lines = #()
append lines ("FILE|" + maxFilePath + maxFileName)
for o in objects do (
  local n = o.name as string
  local c = (classOf o) as string
  local kind = "other"
  if (isKindOf o BoneGeometry) or (matchPattern c pattern:"*Bone*") then kind = "bone"
  else if (isKindOf o GeometryClass) and (c != "Dummy") then kind = "geo"
  append lines (kind + "|" + n + "|" + c)
)
local s = ""
for i = 1 to lines.count do (
  if i > 1 do s += "\n"
  s += lines[i]
)
s
)"""
    raw = _exec_ms(client, code, timeout=60.0).strip().strip('"')
    geo: list[str] = []
    bones: list[str] = []
    other: list[str] = []
    file_path = ""
    for line in raw.replace("\\n", "\n").splitlines():
        parts = line.split("|", 2)
        if not parts:
            continue
        if parts[0] == "FILE":
            file_path = parts[1] if len(parts) > 1 else ""
        elif parts[0] == "geo" and len(parts) >= 2:
            geo.append(parts[1])
        elif parts[0] == "bone" and len(parts) >= 2:
            bones.append(parts[1])
        elif parts[0] == "other" and len(parts) >= 2:
            other.append("|".join(parts[1:]))
    return {"file": file_path, "geo": geo, "bones": bones, "other": other, "raw": raw}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    if not SRC.exists():
        print("missing", SRC)
        return 2

    clear_workspace_cache()
    ws = get_shared_workspace()
    if ws is None:
        print("no shared workspace")
        return 2
    ensure_workspace_dir(ws)
    dest = Path(ws) / SRC.name
    print("copy", SRC, "->", dest)
    shutil.copy2(SRC, dest)
    print("copied", dest.stat().st_size, "bytes")

    client = MaxClient(host=HOST, port=PORT, transport="tcp", timeout=60.0)
    ensure_dialog_monitor_loaded(client)
    print("ping", client.send_command('"pong"', cmd_type="maxscript", timeout=10).get("result"))

    esc = _escape_ms_string(str(dest))
    load_code = f'MCP_SceneManage.loadScene @"{esc}"'
    load_raw = _exec_ms(client, load_code, timeout=120.0)
    print("load", load_raw)
    time.sleep(2.0)

    scene = _list_scene(client)
    print("scene", json.dumps(scene, ensure_ascii=False, indent=2))

    geo = list(scene.get("geo") or [])
    bones = list(scene.get("bones") or [])
    # Prefer Box*/mesh-like first; else first geo
    mesh_names = [n for n in geo if n.lower().startswith("box")] or (geo[:1] if geo else [])
    bone_names = bones[:]
    if not bone_names:
        # fallback: anything with Bone in other?
        for item in scene.get("other") or []:
            name = str(item).split("|", 1)[0]
            if "bone" in name.lower() or "骨" in name:
                bone_names.append(name)

    print("pick mesh", mesh_names, "bones", bone_names)

    ensure = ensure_goskin_ready(client, open_wait_s=10.0)
    print(
        "ensure",
        json.dumps(
            {"ok": ensure.get("ok"), "error": ensure.get("error"), "steps": list((ensure.get("steps") or {}).keys())},
            ensure_ascii=False,
        ),
    )
    if not ensure.get("ok"):
        out = Path(__file__).with_name("_last_goskin_user_scene.json")
        out.write_text(json.dumps({"scene": scene, "ensure": ensure}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print("wrote", out)
        return 1

    if not mesh_names:
        print("ERROR: no geometry to use as mesh")
        return 1
    if not bone_names:
        print("ERROR: no bones found; will still try with empty bone_names to surface error")

    skin = run_goskin_skin(
        client,
        mesh_names=mesh_names,
        bone_names=bone_names or None,
        click_start=False,
    )
    brief = {
        "ok": skin.get("ok"),
        "error": skin.get("error"),
        "awaiting_start_confirm": skin.get("awaiting_start_confirm"),
        "confirmation": skin.get("confirmation"),
        "user_prompt": skin.get("user_prompt"),
        "select_mesh_scene": skin.get("steps", {}).get("select_mesh_scene"),
        "select_bones_scene": skin.get("steps", {}).get("select_bones_scene"),
        "verify_mesh": (skin.get("steps") or {}).get("verify_mesh"),
        "verify_bones": (skin.get("steps") or {}).get("verify_bones"),
    }
    print(json.dumps(brief, ensure_ascii=False, indent=2))

    texts: list = []
    ocr_err = None
    try:
        time.sleep(0.4)
        rec = recognize_dialog(client=client)
        texts = [ln.get("text") for ln in (rec.get("ocr_lines") or [])][:50]
        print("ocr_after", json.dumps(texts, ensure_ascii=False))
    except Exception as exc:
        ocr_err = f"{type(exc).__name__}: {exc}"
        print("ocr_after_failed", ocr_err)

    out = Path(__file__).with_name("_last_goskin_user_scene.json")
    out.write_text(
        json.dumps(
            {
                "src": str(SRC),
                "dest": str(dest),
                "load": load_raw,
                "scene": scene,
                "mesh_names": mesh_names,
                "bone_names": bone_names,
                "ensure": ensure,
                "skin": skin,
                "ocr_after": texts,
                "ocr_after_error": ocr_err,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print("wrote", out)
    print("STOPPED before 开始蒙皮 — awaiting user confirm")
    return 0 if skin.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
