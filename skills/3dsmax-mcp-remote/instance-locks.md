# Instance leases (remote agents)

Public multi-agent sharing uses **short idle leases** and a **bounded FIFO acquire queue**.

## Do not queue for viewport screenshots

`capture_viewport` only grabs a viewport image. It talks to Max briefly but **does not need an exclusive lease** the way GoSkin / scene edits do.

**Hard rule for agents:** never sit in `acquire_instance` wait (up to ~60s `WAIT_TIMEOUT`) just to take a screenshot. That blocks you and does not help the holder.

| Situation | What to do |
|-----------|------------|
| This session **already** holds the Max (`get_my_instance` / mid–GoSkin) | Call `capture_viewport` directly — **no** second `acquire_instance` |
| Target is **busy** (`list_instances` → `busy: true`) | Do **not** acquire. Use script `--no-acquire`, or skip / ask the holder to capture |
| Target is **idle** and you need a one-shot shot | Short `acquire` → `capture_viewport` → **immediate** `release_instance`, or `capture_viewport_shot.py` (releases by default) |
| `WAIT_TIMEOUT` / `INSTANCE_BUSY` | Stop waiting. Retry later, use `--no-acquire`, or pick another idle instance — do **not** tight-loop acquire |

Script:

```bash
# Preferred when Max may be busy / another agent holds it:
python capture_viewport_shot.py --out shot.png --instance max-8765 --no-acquire

# Only when list_instances shows the target idle (or you accept queuing):
python capture_viewport_shot.py --out shot.png --instance max-8765
```

## When you DO need a lease

Call `list_instances`, then `acquire_instance` before **scene-changing** work (GoSkin, load/save, mesh edits, etc.).

- `acquire_instance` waits up to `MAXMCP_ACQUIRE_WAIT_SECONDS` (default 60s). On failure read `code`: `QUEUE_FULL` / `WAIT_TIMEOUT` / `NO_FREE_INSTANCE` / `INSTANCE_BUSY` — do not busy-poll.
- Finish Max work → `release_instance` promptly. Idle TTL (`MAXMCP_LOCK_TTL`, default 180s) auto-releases abandoned leases; do not rely on that while only “thinking”.
- Scene reset on acquire is opt-in (`reset_scene=true`). Prefer explicit `manage_scene(action="reset")` when the user asks.
- Prefer `name` from `list_instances` (e.g. `max-8765`) for remote/ini targets.

## `--keep-lease` pitfalls (goskin / capture scripts)

Each `python …/goskin_dev_flow.py` starts a **new** MCP HTTP session. Leases are per session.

| Flag / outcome | Lease |
|----------------|--------|
| Success + `--keep-lease` | Held until that **same** HTTP session calls `release_instance`, or idle TTL (~180s) |
| Failure (OCR, tool error, …) | **Always released** (even if `--keep-lease` was passed) |
| New python process after a stuck keep-lease | Sees `busy` / `no idle online` — cannot steal the old session’s lock |

Do **not** use `--keep-lease` unless a follow-up call in the **same** long-lived MCP session will continue (IDE tool loop). For one-shot CLI scripts, omit it.

## GoSkin is a queue job

自动蒙皮任务统一走 `submit_goskin_job`（服务端排队、挑空闲实例、独占租约跑完整流程），**不要**自己 `acquire_instance` 再跑 `goskin_dev_flow.py`（该脚本仅供低层 OCR 调试）：

```text
workspace_upload <本机.max>  →  submit_goskin_job(scene_local_path=<上传的 local_path>)  →  轮询 get_goskin_job(job_id)
```

`scene_local_path` 传上传返回的 `local_path`；省略则用被分配实例的当前场景。提交前不要手动 acquire——实例由服务端 worker 独占租给任务，手动 acquire 会互相卡死（`INSTANCE_BUSY`）。完整约定见 [goskin-job-queue.md](references/goskin-job-queue.md)。
