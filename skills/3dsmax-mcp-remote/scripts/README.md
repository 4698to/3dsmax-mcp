# Remote skill scripts (Agent host A)

These helpers talk to the **same** 3dsmax-mcp HTTP server (B) that the IDE MCP client already uses.
Stdlib only — **no `maxmcp` import**.

## Endpoint (do not invent localhost)

1. **Prefer MCP tools** via the client’s configured server (see project/user `mcp.json`). No URL needed.
2. If you run these scripts, set `MAXMCP_URL` (or pass `--url`) to **that exact** streamable-http URL from the MCP config.
3. **Never** assume `http://127.0.0.1:8000/mcp` unless the user’s MCP config literally uses that URL.

```bash
rem Example only — replace with YOUR mcp.json URL for 3dsmax-mcp:
set MAXMCP_URL=http://your-mcp-host:8000/mcp
```

Run from this `scripts/` directory (or any cwd; scripts add themselves to `sys.path`):

```bash
python list_online_instances.py
python capture_viewport_shot.py --out shot.png
python upload_to_mcp.py C:\path\to\file.max
python submit_goskin_job.py C:\path\to\file.max --user-id <你的用户标识>
python submit_goskin_job.py --local-path C:\server\path\from\prior\upload.max --user-id <你的用户标识> --poll
python goskin_dev_flow.py --instance max1 --scene C:\path\to\file.max
```

If `MAXMCP_URL` / `--url` is missing, scripts **exit with an error** instead of guessing.

## 会话恢复（`Mcp-Session-Id`）

MCP streamable-http 的会话 id 由**服务器**在首次响应时下发（`Mcp-Session-Id` 响应头），客户端只能回传、不能自造。脚本默认每次开新会话；想**复用服务器端既有会话**（保留其到 Max 实例的租约/路由）时，用下面任一参数恢复：

```bash
# 方式一：直接给 id（来自上一次脚本运行，或 IDE MCP 客户端持有的会话）
python list_online_instances.py --session-id 3f2a1b9c...

# 方式二：用文件记忆（首次运行自动把新 id 写回文件，之后自动恢复）
python list_online_instances.py --session-id-file .mcp_session
python capture_viewport_shot.py  --session-id-file .mcp_session
python goskin_dev_flow.py        --session-id-file .mcp_session --instance max-8765
```

行为约定：

- **跳过握手**：带恢复的 id 时脚本不再发 `initialize`（对已有会话重复 initialize 会被服务器拒绝），直接以 `Mcp-Session-Id` 头发出真实请求探测会话是否仍有效。
- **404 自动重建**：id 未知/已过期时服务器返回 HTTP 404 `Session not found`；脚本会丢弃旧 id、重新握手拿到新会话，再重试原请求一次，并把新 id 写回 `--session-id-file`（若指定）。
- **不要自造 id**：id 由服务器生成（`uuid4().hex`），未知 id 一律走上面的 404 重建流程，脚本无感知。
- **共享租约**：恢复的会话复用该会话在服务器端已持有的实例租约与路由。典型场景——本对话已 `acquire_instance` 占住某 Max，再恢复本对话的会话 id 跑脚本，脚本在同一会话内执行，不会因「实例已被占用」而冲突（对应 SKILL.md 里「先 release 再跑脚本」的替代方案）。

## Scripts

| Script | MCP / HTTP |
|--------|------------|
| `list_online_instances.py` | `list_instances` (online filter) |
| `capture_viewport_shot.py` | Optional `acquire_instance` → `capture_viewport` → download → `release_instance`. **If the target is busy, pass `--no-acquire`** — do not wait in the lease queue just for a screenshot (see [instance-locks.md](../instance-locks.md)). |
| `upload_to_mcp.py` | `POST /files/upload`（或 `--via-mcp` → `workspace_upload`）。打印的 JSON 里 **`local_path` 才是 `load_scene(file_path=...)` 的参数**；`url` 只给 Agent 下载，`name` 不是路径。下载用 `McpHttpSession.download_file_name`（中文文件名会做百分号编码）。不要自己把中文名拼进 `GET /files/...`，`urllib` 会按 ASCII 编码请求行并抛 `UnicodeEncodeError`。 |
| `submit_goskin_job.py` | **自动蒙皮入队快捷脚本**（唯一正确的生产路径）：上传本机 `.max` → `submit_goskin_job(scene_local_path=..., user_id=...)` → 打印 `job_id/status/status_url`。**`--user-id` 尽力而为**（或 env `MAXMCP_USER_ID`）——拿到标识就带、拿不到（agent 无用户标识）空着照常提交，服务端归一为 null 靠 owner/session 兜底审计；传了则在构造 `McpHttpSession` 时同值作为 `audit_user_id` 自动带 `X-Maxmcp-User-Id` 头兜底。可选 `--poll` 轮询到终态。**不要用 `goskin_dev_flow.py` 跑生产蒙皮任务**。 |
| `goskin_dev_flow.py` | **仅供低层 OCR 流程调试**（一条命令：`--scene` 上传本机 `.max` → `load_scene(local_path)` → `goskin_ensure_ready` → `propose_skin_bones` → `goskin_run_skin`，默认不点开始蒙皮）。**自动蒙皮任务不要用它，统一走 `submit_goskin_job`**（见 [goskin-job-queue.md](../references/goskin-job-queue.md)）。 |

```bash
# Screenshot while another agent holds Max (no queue):
python capture_viewport_shot.py --out shot.png --instance max-8765 --no-acquire
```

Prefer these for common tasks to avoid multi-round tool guessing — or just call the MCP tools above through the IDE.

Maintainer probes that `import maxmcp` live in the **product repo** `dialog_monitor/`, not here.
