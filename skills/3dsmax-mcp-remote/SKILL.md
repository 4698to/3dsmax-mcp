---
name: 3dsmax-mcp-remote
description: >-
  Remote agent guide for 3ds Max via HTTP MCP: tool choice, GoSkin OCR flows,
  and stdlib helper scripts (list instances, viewport capture, upload, goskin_dev).
  Use on agent host A talking to MCP server B — no local maxmcp install.
---

# 3ds Max MCP — Remote Agent Guide

## Deployment: remote skill (A) vs MCP server (B)

| Host | Role | Install |
|------|------|---------|
| **A** | Agent | **This skill** (`3dsmax-mcp-remote`: docs + HTTP `scripts/`) + MCP client → **B** |
| **B** | 3dsmax-mcp server | Full product (tools + `dialog_monitor` implementation) |
| **C** | 3ds Max | Max + bridge; TCP reachable from **B** |

- Drive Max **only** through B (MCP tools or the HTTP helper scripts below). Never `import maxmcp` on A.
- Maintainer / local-checkout skill is **`3dsmax-mcp-dev`** (separate package). Repo `dialog_monitor/_probe_*.py` stays in the full product tree.

## Agent helper scripts (prefer for common tasks)

**Default path:** call MCP tools through the client’s **already-configured** 3dsmax-mcp server (Cursor/Claude `mcp.json` / IDE MCP settings). Do **not** invent `http://127.0.0.1:8000/mcp` or any other URL.

**Optional CLI scripts** (same skill `scripts/` dir) talk to that **same** HTTP endpoint. Only set `MAXMCP_URL` (or `--url`) to the URL already used by the MCP client — never a guessed localhost.

| Task | Prefer MCP tool | Or script (needs `MAXMCP_URL` = client’s B URL) |
|------|-----------------|--------------------------------------------------|
| List online Max instances | `list_instances` | `python list_online_instances.py` |
| Viewport screenshot | `capture_viewport` (+ download) — **no lease queue** if already holding or target busy; see below | `python capture_viewport_shot.py --out shot.png --no-acquire` when busy |
| Upload file to workspace | `workspace_upload` / HTTP `/files/upload` | `python upload_to_mcp.py <path>` |
| Load / save scene | `load_scene`, `manage_scene`, `save_as` | — (MCP tools only) |
| GoSkin prepare (no 开始蒙皮) | `goskin_*` | `python goskin_dev_flow.py --instance <name>` |

### Viewport capture vs leases (avoid WAIT_TIMEOUT)

- Screenshot is a **short Max call**, not a long exclusive job. **Do not** `acquire_instance` and wait in the FIFO queue (~60s) only to capture.
- If this session already holds the instance → `capture_viewport` only.
- If `list_instances` shows the target **busy** → use `--no-acquire` (script) or skip; never block on acquire for a shot.
- Full rules: [instance-locks.md](instance-locks.md).

Details: [scripts/README.md](scripts/README.md). Scripts are stdlib HTTP only (no `maxmcp`). If unsure of the URL, use MCP tools directly and skip the scripts.

## Scene file ops (MCP tools — do not invent Max-side scripts)

Drive save/load/reset **only** through these tools on B (never raw `saveMaxFile` / Max script paths on A):

| Need | Tool call |
|------|-----------|
| Scene summary (path, counts) | `manage_scene(action="info")` |
| Save current file (or Temp if unsaved) | `manage_scene(action="save")` |
| Save As to a path | `manage_scene(action="save_as", file_path=...)` **or** `save_as` / `save_scene_as` |
| Load a `.max` | `load_scene(file_path=...)` |
| Hold / fetch / reset | `manage_scene(action="hold"|"fetch"|"reset")` — reset is user-opt-in; saves first |
| Agent 视口提示 | 租约自动：`acquire_instance` 显示 / `release_instance` 隐藏；或 `manage_scene(action="show_agent_banner"|"hide_agent_banner")` |

Path tips:
- For Save As, only the **file name** is used; directories agents invent are ignored.
  Example: `/workspace/output/foo.max` → `{WORKSPACE_DIR}/foo.max`.
- Pass `file_path` **or** `path` (alias). `manage_scene(action="save", path="foo.max")` is treated as Save As.
- Do **not** use `action="save_as:/path"` — pass `file_path`/`path` as a separate argument.
- Full catalog: [tool-reference.md](tool-reference.md) § Scene management. Leases: [instance-locks.md](instance-locks.md).

## Tool Profile Routing

- **Full/core:** Operational tools such as `query_scene` and `create_object` are advertised directly; call the matching tool by name.
- **Progressive:** If the advertised surface contains only `list_toolsets`, `describe_toolset`, and `call_tool`, never call an operational name as a top-level MCP tool. Choose the relevant capability with `list_toolsets`, load only that group with `describe_toolset`, then invoke the selected operation through `call_tool(name=..., arguments=...)`.
- Do not describe every toolset up front. Load only the group needed for the current request; if the exact operational tool and arguments are already known, `call_tool` can dispatch it directly.

Principles:
- Match the user's request. Do not run setup, discovery, or scene analysis by habit.
- Do not call `get_bridge_status` or `get_session_context` as a session preamble.
- Prefer a dedicated MCP tool over raw MAXScript when a tool clearly matches the task.
- Do not render unless the user explicitly asks. Viewport capture is fine when visual proof is useful — **never wait in the acquire queue only to screenshot** ([instance-locks.md](instance-locks.md)).
- Multiple Max instances: `list_max_instances`, `select_max_instance(pid|name)`, `get_selected_max_instance`, and `release_max_instance` are available in every profile. Prefer `name` from `list_instances` (e.g. `max-8765`) for remote/ini targets — it acquires a session lease without resetting the scene. `pid` binds local native pipes. The first successful native connection stays bound to that Max. Starting or claiming another Max only changes the default for unbound clients. If the selected Max closes, explicitly select another or release it; clients never silently switch. `MCP_MAX_PID` or `MCP_MAX_PIPE` pins the startup target (`MCP_MAX_PIPE` takes precedence). Release also clears startup pinning.

## Read on demand (modules)

Open only the file needed for the current task (one level from this index):

| When | Read |
|------|------|
| Multi-agent leases / acquire / release | [instance-locks.md](instance-locks.md) |
| Load / save / reset scene (`manage_scene`, `save_as`, `load_scene`) | [tool-reference.md](tool-reference.md) (§ Scene management) |
| Which tool family to use | [tool-choice.md](tool-choice.md) |
| Full tool catalog (objects, mesh, materials, GoSkin, tyFlow, …) | [tool-reference.md](tool-reference.md) |
| `execute_maxscript` + MCP gotchas | [mcp-pitfalls.md](mcp-pitfalls.md) |
| MAXScript / OSL gotchas | [maxscript-pitfalls.md](maxscript-pitfalls.md) |
| HTTP MCP handshake, upload/download, CJK | [remote-http-mcp.md](remote-http-mcp.md) |
| Auto GoSkin OCR click flow | [goskin-ocr-click.md](goskin-ocr-click.md) |
| Curves / loft recipes | [curve-construction.md](curve-construction.md) |
| tyFlow graphs | [tyflow-graphs.md](tyflow-graphs.md) |
| Data Channel / MCG | [procedural-graphs.md](procedural-graphs.md) |

## MAXScript Reference (bundled)

Read the relevant file before writing unfamiliar MAXScript:

| File | Covers |
|------|--------|
| [maxscript-core-syntax.md](maxscript-core-syntax.md) | Variables, scope, types, operators, control flow |
| [maxscript-common-patterns.md](maxscript-common-patterns.md) | Undo/animate blocks, callbacks, file I/O |
| [maxscript-3dsmax-objects.md](maxscript-3dsmax-objects.md) | Nodes, transforms, hierarchy, properties |
| [maxscript-mesh-poly-ops.md](maxscript-mesh-poly-ops.md) | Sub-object mesh/poly ops |
| [maxscript-materials-textures.md](maxscript-materials-textures.md) | Materials, texmaps, PBR |
| [maxscript-animation-controllers.md](maxscript-animation-controllers.md) | Controllers, constraints, wire params |
| [maxscript-rendering-cameras.md](maxscript-rendering-cameras.md) | Render settings, cameras, environment |
| [maxscript-splines-shapes.md](maxscript-splines-shapes.md) | Splines and shapes |
| [maxscript-scripted-plugins.md](maxscript-scripted-plugins.md) | Scripted geometry, modifiers, utilities |
| [maxscript-ui-rollouts.md](maxscript-ui-rollouts.md) | Rollout UIs and dialogs |

### Unwrap UVW
- Open the editor: `$Box001.modifiers[#Unwrap_UVW].edit()` — not the `OpenUnwrapUI` macro alone
