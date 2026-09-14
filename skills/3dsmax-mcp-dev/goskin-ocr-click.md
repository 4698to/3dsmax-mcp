# GoSkin OCR Simulated Click (Case Study)

Read this completely before automating **自动蒙皮 / GoSkinning** (Qt dialog `自动蒙皮4.*` / `*天晴数码`) via OCR mouse clicks.

Companion module: `dialog_monitor/` · MCP tools: `goskin_*`, `recognize_plugin_dialog`, `click_plugin_dialog_button`.

## When to use

- User asks to run Auto GoSkin / 自动蒙皮 / 全局蒙皮 without a MaxScript API.
- Plugin UI is Qt: no reliable child HWNDs → screenshot + external OCR + simulated click.
- Progressive profile: load toolset `dialog_ui`, then `call_tool`.

## Hard rules (do not reorder)

1. Prefer dedicated GoSkin tools over raw `click_plugin_dialog_button` loops.
2. **Never** click 「开始蒙皮」 until the user explicitly confirms the summary from `goskin_run_skin`.
3. **Never** click 「选定」 without first focusing the list slot 「(选中后在编辑区添加)」 (or an existing `模型：N` row). Skipping this pops a warning that covers the dialog and breaks later OCR.
4. Do not trust click `ok:true` alone — verify with OCR (`模型：N` / `关节：N`) or returned counts.
5. Empty scene selection → refuse 「选定」 (GoSkin warning).
6. Remote host must stay **unlocked**. Lock screen / disconnected RDP → clicks report success but UI unchanged. Turning monitors off is usually fine.

## Click mechanism

Inside Max (`MCP_DialogMonitor.clickAtScreen`):

1. `SetForegroundWindow` on the dialog
2. `SetCursorPos(x, y)` — physical screen pixels
3. `mouse_event(LEFTDOWN)` → sleep → `mouse_event(LEFTUP)`

Coordinates: dialog snapshot → OCR boxes → `image_to_screen` (prefer **client rect**; when image height > client height and widths match, subtract top pad — outer-window mapping misses the list row).

## Config (`max_instances.ini`)

```ini
[instances]
max1 = 192.168.139.45:8765

[workspace]
path = \\share\3dsmax-mcp\workspace

[ocr]
base = http://192.168.139.130:8000
```

| Key | Role |
|-----|------|
| `[ocr] base` | HTTP OCR root. Priority: tool `ocr_base` > `MAXMCP_OCR_BASE` > ini > builtin default |
| Endpoints | `GET {base}/v1/ocr/health` · `POST {base}/v1/ocr` |
| `[workspace]` | Shared capture dir for cross-machine Max ↔ Python (optional if same host / HTTP upload path works) |

Probe: `check_dialog_ocr_health` → `ok`, `endpoints`, `workspace.shared_configured`.

## MCP agent workflow (canonical)

```
Task Progress:
- [ ] check_dialog_ocr_health
- [ ] goskin_ensure_ready
- [ ] goskin_run_skin(mesh_names=..., bone_names=..., click_start=false)
- [ ] Show user_prompt / confirmation; WAIT for user OK
- [ ] goskin_confirm_start(user_confirmed=true)   # only after OK
```

### Step details

| Step | Tool | Success signal |
|------|------|----------------|
| Health | `check_dialog_ocr_health` | OCR reachable |
| Open UI | `goskin_ensure_ready` | 「编辑区」/「开始蒙皮」 visible on 全局蒙皮 |
| Prepare | `goskin_run_skin` | `awaiting_start_confirm=true`, counts mesh≥1 joints≥1 |
| Gate | (chat) | User agrees with names/counts |
| Start | `goskin_confirm_start(user_confirmed=true)` | OCR sees 「完成」 (or tool wait succeeds) |

`goskin_run_auto` = ensure + run_skin; still defaults to **pause** before 开始蒙皮.

Pass explicit `mesh_names` / `bone_names` when known (e.g. `Box001`, `Bone001..003`). Otherwise ensure a valid Max selection exists before 选定.

Internal order inside `run_goskin_skin` (already implemented — do not reinvent):

1. dismiss leftover warnings  
2. cleanup dirty lists if needed  
3. click 「(选中后在编辑区添加)」  
4. Max-select mesh → 选定 on 「在场景中选择模型」 → verify `模型：N≥1`  
5. focus `模型：N` → Max-select bones → 选定 on 「在场景中选择关节」 → verify `关节：N≥1`  
6. return confirmation; **stop**

## Failure modes

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Click ok but OCR unchanged | Lock screen / RDP disconnect | Unlock interactive desktop |
| Warning modal / stuck UI | 选定 without list-slot focus | `dismiss` via 确定, then focus 「(选中后在编辑区添加)」 first |
| `mesh_not_added` | Slot miss, wrong Y map, or empty selection | Re-run after unlock; pass mesh names; check client-rect mapping |
| OCR unreachable | Bad `[ocr] base` / firewall | Fix ini / `MAXMCP_OCR_BASE`; recheck health |
| Black / useless capture | Snapshot kwargs / session | Use plain `windows.snapshot`; keep session interactive |

## Minimal Python smoke (same host as Max)

```python
from maxmcp.max_client import MaxClient
from dialog_monitor.goskin_flow import ensure_goskin_ready, run_goskin_skin, confirm_goskin_start

c = MaxClient(host="127.0.0.1", port=8765, transport="tcp")
ensure_goskin_ready(c)
prep = run_goskin_skin(
    c,
    mesh_names=["Box001"],
    bone_names=["Bone001", "Bone002", "Bone003"],
)  # click_start defaults False
print(prep["user_prompt"])
# after user OK:
# confirm_goskin_start(c, user_confirmed=True)
```

## Related files

- `dialog_monitor/goskin_flow.py` — orchestration  
- `dialog_monitor/click_button.py` — capture / OCR / click primitives  
- `maxscript/mcp/mcp_dialog_monitor.ms` — Max-side snapshot + mouse helper  
- `maxmcp/tools/goskin.py` — MCP wrappers  
- `dialog_monitor/README.md` — module docs  
- `README.zh-CN.md` — user-facing GoSkin section  
