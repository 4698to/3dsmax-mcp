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
python goskin_dev_flow.py --instance max1
```

If `MAXMCP_URL` / `--url` is missing, scripts **exit with an error** instead of guessing.

## Scripts

| Script | MCP / HTTP |
|--------|------------|
| `list_online_instances.py` | `list_instances` (online filter) |
| `capture_viewport_shot.py` | Optional `acquire_instance` → `capture_viewport` → download → `release_instance`. **If the target is busy, pass `--no-acquire`** — do not wait in the lease queue just for a screenshot (see [instance-locks.md](../instance-locks.md)). |
| `upload_to_mcp.py` | `POST /files/upload` (or `--via-mcp` → `workspace_upload`) |
| `goskin_dev_flow.py` | OCR health → acquire → `restore_max_window` → unhidden → `propose_skin_bones` (if registered) → `goskin_ensure_ready` → `goskin_run_skin` (no start unless `--confirm-start`). **`--keep-lease` only on success**; OCR/tool failure always releases. A new python process is a new MCP session and cannot reclaim a prior keep-lease (see [instance-locks.md](../instance-locks.md)). |

```bash
# Screenshot while another agent holds Max (no queue):
python capture_viewport_shot.py --out shot.png --instance max-8765 --no-acquire
```

Prefer these for common tasks to avoid multi-round tool guessing — or just call the MCP tools above through the IDE.

Maintainer probes that `import maxmcp` live in the **product repo** `dialog_monitor/`, not here.
