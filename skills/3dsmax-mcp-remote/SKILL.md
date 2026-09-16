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
| Viewport screenshot | `capture_viewport` (+ download) | `python capture_viewport_shot.py --out shot.png` |
| Upload file to workspace | `workspace_upload` / HTTP `/files/upload` | `python upload_to_mcp.py <path>` |
| GoSkin prepare (no 开始蒙皮) | `goskin_*` | `python goskin_dev_flow.py --instance <name>` |

Details: [scripts/README.md](scripts/README.md). Scripts are stdlib HTTP only (no `maxmcp`). If unsure of the URL, use MCP tools directly and skip the scripts.

## Tool Profile Routing

- **Full/core:** Operational tools such as `query_scene` and `create_object` are advertised directly; call the matching tool by name.
- **Progressive:** If the advertised surface contains only `list_toolsets`, `describe_toolset`, and `call_tool`, never call an operational name as a top-level MCP tool. Choose the relevant capability with `list_toolsets`, load only that group with `describe_toolset`, then invoke the selected operation through `call_tool(name=..., arguments=...)`.
- Do not describe every toolset up front. Load only the group needed for the current request; if the exact operational tool and arguments are already known, `call_tool` can dispatch it directly.

Principles:
- Match the user's request. Do not run setup, discovery, or scene analysis by habit.
- Do not call `get_bridge_status` or `get_session_context` as a session preamble.
- Prefer a dedicated MCP tool over raw MAXScript when a tool clearly matches the task.
- Do not render unless the user explicitly asks. Viewport capture is fine when visual proof is useful.
- Multiple Max instances: `list_max_instances`, `select_max_instance(pid)`, `get_selected_max_instance`, and `release_max_instance` are available in every profile. The first successful native connection stays bound to that Max. Starting or claiming another Max only changes the default for unbound clients. If the selected Max closes, explicitly select another or release it; clients never silently switch. `MCP_MAX_PID` or `MCP_MAX_PIPE` pins the startup target (`MCP_MAX_PIPE` takes precedence). Release also clears startup pinning.

## Read on demand (modules)

Open only the file needed for the current task (one level from this index):

| When | Read |
|------|------|
| Multi-agent leases / acquire / release | [instance-locks.md](instance-locks.md) |
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
