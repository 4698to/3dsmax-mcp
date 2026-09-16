# Instance Lock Discipline (short lease + wait queue)

Public multi-agent sharing uses **short idle leases** and a **bounded FIFO acquire queue**, not long session monopolies.

- Multi-instance mode: call `list_instances` first, then `acquire_instance` before any scene tool, so commands route to your own instance.
- `list_instances` probes **one Max at a time** under a process-wide lock shared with `list_max_instances` (TCP ping + local named-pipe checks never overlap). Each row reports `tcp_online` and `native_online` (local named pipe only; remote hosts are TCP-only). `online` is true if either transport is up. It also reports `queue_depth` / `lease_idle_seconds`. Never call these discovery tools in parallel with each other or with scene tools — Max is single-threaded.
- `acquire_instance` is lock-only (does **not** ping Max). If no Max is idle it **waits** up to `MAXMCP_ACQUIRE_WAIT_SECONDS` (default 60s) on a FIFO queue. Do **not** tight-loop acquire. On failure read `code`: `QUEUE_FULL` / `WAIT_TIMEOUT` / `NO_FREE_INSTANCE` / `INSTANCE_BUSY` — all retryable except `INSTANCE_ERROR`. Honor `retry_after_seconds`.
- Default lease idle TTL is **`MAXMCP_LOCK_TTL` (default 180s)**; scene tool activity renews it. Finish the task and call `release_instance` promptly so waiters can proceed — **do not hold a Max while only thinking**.
- New leases **reset the scene by default** (`MAXMCP_RESET_ON_ACQUIRE`, default true) so tenants do not share leftover scene data. Load any needed `.max` inside your own lease.
- **Release when the task is done:** Prefer immediate `release_instance` after the user confirms the Max work for this request is finished. Every 2 rounds of max-mcp usage, ask whether to release if still holding. Session disconnect also auto-releases and cancels any acquire wait.
- `release_instance` can only be called by the current holder.
- Single-instance fallback: explicit `acquire_instance` is not required, but the same prompt-release discipline is recommended.
- Long jobs (heavy renders, multi-hour work): do not starve the shared short-lease pool; use a dedicated Max instance when available.
