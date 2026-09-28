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
| GoSkin 自动蒙皮（单次/批量/多人） | `submit_goskin_job` + `get/list/cancel/confirm_goskin_job`；**不要**自己调 `goskin_*` 工具、不要跑 `goskin_dev_flow.py` | — (MCP tools only，见下) |

### Viewport capture vs leases (avoid WAIT_TIMEOUT)

- Screenshot is a **short Max call**, not a long exclusive job. **Do not** `acquire_instance` and wait in the FIFO queue (~60s) only to capture.
- If this session already holds the instance → `capture_viewport` only.
- If `list_instances` shows the target **busy** → use `--no-acquire` (script) or skip; never block on acquire for a shot.
- Full rules: [instance-locks.md](instance-locks.md).

Details: [scripts/README.md](scripts/README.md). Scripts are stdlib HTTP only (no `maxmcp`). If unsure of the URL, use MCP tools directly and skip the scripts.

### Session resume（恢复既有 MCP 会话）

脚本默认新建 MCP 会话。需要**复用服务器端既有会话**（保留其对 Max 实例的租约/路由）时，加以下参数：

```bash
python goskin_dev_flow.py --session-id <Mcp-Session-Id> ...        # 直接指定上次的 id
python goskin_dev_flow.py --session-id-file .mcp_session ...       # 从文件恢复，新 id 也自动写回
```

- 会话 id 由**服务器**下发（`Mcp-Session-Id` 响应头），客户端只回传、不自造；未知/过期 id → 服务器返回 HTTP 404，脚本自动重新握手拿到新会话并重试一次，无感知。
- 带恢复 id 时脚本**跳过 `initialize` 握手**（重复 initialize 会被服务器拒绝），直接以真实请求探测。
- 典型用途：本对话已占住某 Max 实例时，恢复本会话的 id 跑脚本，脚本与对话共享同一租约，不触发「实例已被占用」冲突。
- 详细约定见 [scripts/README.md](scripts/README.md) § 会话恢复（`Mcp-Session-Id`）。

## Scene file ops (MCP tools — do not invent Max-side scripts)

Drive save/load/reset **only** through these tools on B (never raw `saveMaxFile` / Max script paths on A):

| Need | Tool call |
|------|-----------|
| Scene summary (path, counts) | `manage_scene(action="info")` |
| Save current file (or Temp if unsaved) | `manage_scene(action="save")` |
| Save As to a path | `manage_scene(action="save_as", file_path=...)` **or** `save_as` / `save_scene_as` |
| Load a `.max` | `load_scene(file_path=<upload local_path>)` — absolute path on the Max host; not `url`, not a bare filename |
| Hold / fetch / reset | `manage_scene(action="hold"|"fetch"|"reset")` — reset is user-opt-in; saves first |
| Agent 视口提示 | 租约自动：`acquire_instance` 显示 / `release_instance` 隐藏；或 `manage_scene(action="show_agent_banner"|"hide_agent_banner")` |

Path tips:
- For Save As, only the **file name** is used; directories agents invent are ignored.
  Example: `/workspace/output/foo.max` → `{WORKSPACE_DIR}/foo.max`.
- Pass `file_path` **or** `path` (alias). `manage_scene(action="save", path="foo.max")` is treated as Save As.
- Do **not** use `action="save_as:/path"` — pass `file_path`/`path` as a separate argument.
- Full catalog: [tool-reference.md](tool-reference.md) § Scene management. Leases: [instance-locks.md](instance-locks.md).

## GoSkin：自动蒙皮统一走 `submit_goskin_job`

自动蒙皮**只能**用 `submit_goskin_job` 提交队列。不要自己选工具，不要先 `load_scene` 再写 `call('goskin_ensure_ready')`，也不要再用 `list_online_instances.py` 看 busy，更不要跑 `goskin_dev_flow.py`。一个工具入队：服务端自动排队 → 挑空闲实例（jobs/shared 池）→ 独占租约跑完整个 GoSkin 流程，Agent 只负责轮询终态。

```text
workspace_upload <本地.max>  →  submit_goskin_job(scene_local_path=..., user_id=<你的用户标识>)  →  轮询 get_goskin_job(job_id) 直到终态
```

- **提交必须带 user_id（必做）**：每次 `submit_goskin_job` 都要传 `user_id=<你的用户标识>`（HTTP 提交则带 `X-Maxmcp-User-Id` 请求头），用于审计追踪提交者；不影响鉴权，不传则任务 `user_id` 为 null。
- `confirm_mode="auto"`（默认）提交后全程自动；`manual` 停在 `awaiting_confirm` 需 `confirm_goskin_job` 放行。
- `scene_local_path` 只传上传返回的 `local_path`，不要把 `url` 或文件名传进去。
- **向用户展示 status_url（必做）**：提交成功返回的 `status_url` 已含访问令牌，**必须以可点击链接放进你的回复**并说明——打开可实时查看进度/排队位置、可在页面上取消任务；不要只回 job_id。
- 提交前不要手动 `acquire_instance`，会和任务抢实例；查询/取消/确认仅本人 + 管理员。
- 完整用法见下节「批量任务队列」及 [references/goskin-job-queue.md](references/goskin-job-queue.md)；OCR 细节见 [references/goskin-ocr-click.md](references/goskin-ocr-click.md)。

## 批量任务队列（GoSkin Job Queue）

多人/批量提交自动蒙皮时**走队列，不要自己抢实例**：`submit_goskin_job` 提交后，服务端自动排队、挑空闲实例（jobs/shared 池）、独占租约跑完整个 GoSkin 流程，Agent 只负责轮询终态。

```text
workspace_upload <本地.max>  →  submit_goskin_job(scene_local_path=..., user_id=<你的用户标识>)  →  轮询 get_goskin_job(job_id)
```

- `confirm_mode="auto"`（默认）：提交后全程自动，轮询到 `succeeded`/`failed` 即可。
- `confirm_mode="manual"`：停在 `awaiting_confirm` 持租约等确认，需 `confirm_goskin_job` 放行（有 30min 持有上限）。
- **提交必须带 user_id（必做）**：`submit_goskin_job` 传 `user_id`，HTTP 提交带 `X-Maxmcp-User-Id` 头。
- 审计 user_id（**每次提交必带**）：`submit_goskin_job` 参数传 `user_id`（MCP），HTTP 提交带 `X-Maxmcp-User-Id` 请求头；只记录到任务日志与 Job 的 `user_id` 字段，用于追踪谁提交的，不影响 owner/鉴权。都不传则 `user_id` 为 null。
- **向用户展示 status_url（必做）**：提交成功返回的 `status_url` 已含访问令牌，**必须以可点击链接放进你的回复**并说明——打开可实时查看进度/排队位置、可在页面上取消任务；不要只回 job_id。
- 查询/取消/确认仅本人 + 管理员（`MAXMCP_JOB_ADMIN_IDS`）；别在提交前手动 `acquire_instance`，会和任务抢实例。
- 5 个工具：`submit_goskin_job` / `get_goskin_job` / `list_goskin_jobs` / `cancel_goskin_job` / `confirm_goskin_job`；HTTP 等价 `POST /jobs` 等。完整用法见 [references/goskin-job-queue.md](references/goskin-job-queue.md)。

## Tool Profile Routing

- **Verify before you route.** Check the actual `tools/list` surface first; never assume a profile from docs alone.
- **Full/core (current):** Operational tools are advertised directly, and the discovery meta-tools `list_toolsets` / `describe_toolset` / `call_tool` are also registered. Prefer calling operational tools by name; use `list_toolsets` only to browse capability groups or verify a tool exists. Never call `list_toolsets` if it is not in `tools/list`.
- **Progressive:** If the advertised surface contains only `list_toolsets`, `describe_toolset`, and `call_tool`, never call an operational name as a top-level MCP tool. Choose the relevant capability with `list_toolsets`, load only that group with `describe_toolset`, then invoke the selected operation through `call_tool(name=..., arguments=...)`.
- **On "Unknown tool":** Re-pull `tools/list` and pick from the real surface. Do not keep guessing tool names.
- Do not describe every toolset up front. Load only the group needed for the current request; if the exact operational tool and arguments are already known, `call_tool` can dispatch it directly.

Principles:
- Match the user's request. Do not run setup, discovery, or scene analysis by habit.
- Do not call `get_bridge_status` or `get_session_context` as a session preamble.
- Prefer a dedicated MCP tool over raw MAXScript when a tool clearly matches the task.
- Do not render unless the user explicitly asks. Viewport capture is fine when visual proof is useful — **never wait in the acquire queue only to screenshot** ([instance-locks.md](instance-locks.md)).
- Multiple Max instances: `list_max_instances`, `select_max_instance(pid|name)`, `get_selected_max_instance`, and `release_max_instance` are available in every profile. Prefer `name` from `list_instances` (e.g. `max-8765`) for remote/ini targets — it acquires a session lease without resetting the scene. `pid` binds local native pipes. The first successful native connection stays bound to that Max. Starting or claiming another Max only changes the default for unbound clients. If the selected Max closes, explicitly select another or release it; clients never silently switch. `MCP_MAX_PID` or `MCP_MAX_PIPE` pins the startup target (`MCP_MAX_PIPE` takes precedence). Release also clears startup pinning.

## Subskills (nested skills)

本包附带可独立调用的子技能（保留各自 frontmatter，随包分发安装到 `subskills/`）：

| 子技能 | 用途 |
|--------|------|
| `3dsmax-spring-bone-audit` | 弹簧/柔体骨骼审计：找出应提交给解算工具的辅助骨骼链首 |

调用方式取决于宿主 skill 系统是否递归发现嵌套 `SKILL.md`；若宿主仅扫描顶层 skills 目录，请把 `subskills/3dsmax-spring-bone-audit/` 单独安装为技能后按名字调用。

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
| GoSkin 批量任务队列（多人/排队） | [goskin-job-queue.md](goskin-job-queue.md) |
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
