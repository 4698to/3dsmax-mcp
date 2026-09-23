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
python goskin_dev_flow.py --instance max1 --scene C:\path\to\file.max
```

If `MAXMCP_URL` / `--url` is missing, scripts **exit with an error** instead of guessing.

## Scripts

| Script | MCP / HTTP |
|--------|------------|
| `list_online_instances.py` | `list_instances` (online filter) |
| `capture_viewport_shot.py` | Optional `acquire_instance` → `capture_viewport` → download → `release_instance`. **If the target is busy, pass `--no-acquire`** — do not wait in the lease queue just for a screenshot (see [instance-locks.md](../instance-locks.md)). |
| `upload_to_mcp.py` | `POST /files/upload`（或 `--via-mcp` → `workspace_upload`）。打印的 JSON 里 **`local_path` 才是 `load_scene(file_path=...)` 的参数**；`url` 只给 Agent 下载，`name` 不是路径。下载用 `McpHttpSession.download_file_name`（中文文件名会做百分号编码）。不要自己把中文名拼进 `GET /files/...`，`urllib` 会按 ASCII 编码请求行并抛 `UnicodeEncodeError`。 |
| `goskin_dev_flow.py` | **一条命令做完**：`--scene` 上传本机 `.max` → `load_scene(local_path)` → `goskin_ensure_ready` → `propose_skin_bones` → `goskin_run_skin`（默认不点开始蒙皮）。不要再自己调这些工具，也不要先 load 再查 busy。场景已在 Max 里时省略 `--scene`。 |

```bash
# Screenshot while another agent holds Max (no queue):
python capture_viewport_shot.py --out shot.png --instance max-8765 --no-acquire
```

Prefer these for common tasks to avoid multi-round tool guessing — or just call the MCP tools above through the IDE.

Maintainer probes that `import maxmcp` live in the **product repo** `dialog_monitor/`, not here.
