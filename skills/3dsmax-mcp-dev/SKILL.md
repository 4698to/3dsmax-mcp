---
name: 3dsmax-mcp-dev
description: >-
  Local/maintainer guide for 3ds Max MCP: tool choice, MAXScript pitfalls, GoSkin.
  Use with a full repo checkout (maxmcp available). Remote agent hosts should use
  the 3dsmax-mcp-remote skill instead.
---

# 3ds Max MCP — Local / Maintainer Guide

## Deployment: local skill vs remote skill

| Package | Audience | Contents |
|---------|----------|----------|
| **`3dsmax-mcp-remote`** | Agent host **A** (HTTP to B) | Docs + stdlib HTTP helper scripts — **install this on A** |
| **`3dsmax-mcp-dev` (this skill)** | Developer with full repo | Docs; probes stay in repo `dialog_monitor/` (needs `maxmcp`) |

Typical split — **Agent (A) / MCP server (B) / Max (C)**:

| Host | Role | Install |
|------|------|---------|
| **A** | Agent | **`3dsmax-mcp-remote`** + MCP client → **B** |
| **B** | 3dsmax-mcp server | Full product |
| **C** | 3ds Max | Max + bridge |

- This local package does **not** ship runnable `dialog_monitor` Python. See [scripts/dialog_monitor/README.md](scripts/dialog_monitor/README.md).
- Maintainer runners: `uv run python dialog_monitor/_test_goskin_*.py` from the **repo root**.

## Tool Profile Routing

- **Full/core:** Operational tools such as `query_scene` and `create_object` are advertised directly; call the matching tool by name.
- **Progressive:** If the advertised surface contains only `list_toolsets`, `describe_toolset`, and `call_tool`, never call an operational name as a top-level MCP tool. Choose the relevant capability with `list_toolsets`, load only that group with `describe_toolset`, then invoke the selected operation through `call_tool(name=..., arguments=...)`.
- Do not describe every toolset up front. Load only the group needed for the current request; if the exact operational tool and arguments are already known, `call_tool` can dispatch it directly.

Principles:
- Match the user's request. Do not run setup, discovery, or scene analysis by habit.
- Do not call `get_bridge_status` or `get_session_context` as a session preamble.
- Prefer a dedicated MCP tool over raw MAXScript when a tool clearly matches the task.
- Scene load/save/reset: use `load_scene`, `manage_scene`, `save_as` / `save_scene_as` — not ad-hoc Max file scripts.
- Do not render unless the user explicitly asks. Viewport capture is fine when visual proof is useful.
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
