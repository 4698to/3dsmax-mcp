# 多用户 GoSkin 自动蒙皮任务队列（服务端）设计方案

**状态：** 草案（仅文档，未改动任何代码）
**日期：** 2026-09-28
**范围：** 3dsmax-mcp 服务端（B）新增"任务队列"能力，支撑多 Agent 并发提交完整 GoSkin 自动蒙皮任务（含点「开始蒙皮」的完整流程），与现有实例租约机制并存。
**涉及模块：** `maxmcp/instance_manager.py`、`maxmcp/server.py`、`maxmcp/tools/files.py`（路由模式参照）、`dialog_monitor/goskin_flow.py`、`maxmcp/tools/goskin.py`。

---

## 0. TL;DR（结论先行）

- **排队与仲裁全部放服务端（B）**：新增一个 `JobManager`（任务管理器），复用现有实例租约（lease）作为底层原语。Agent（A）只暴露"提交 / 查询 / 取消"三个薄接口。
- **排队对象升级：** 现有 `acquire_instance` 的 FIFO 排队的是"谁能用 Max"（交互式短租约）；任务队列排队的是"哪个蒙皮任务先执行"（长任务）。两者共存：交互租约机制不动，任务队列是它上层的一层。
- **执行者：** 服务端后台调度线程持租约干活（worker session），不是提交者持租约。提交者提交后即可断开，稍后查结果。
- **多实例分配（实例池）：** 配置文件可标注"队列专用"实例（`[pools] jobs=` / `MAXMCP_JOB_INSTANCES`），它们只服务队列任务、交互会话不可用；任务分配实例时优先队列专用池，再溢出到 shared 池（可关闭）。交互与批量任务在实例层面隔离。
- **关键风险点：** 长任务（OCR 蒙皮可能 >180s 空闲 TTL）必须解决"任务运行中租约被空闲回收"的问题；「开始蒙皮」的确认门禁（auto / manual）是首要待确认决策。

---

## 1. 背景与现状

### 1.1 现有能力（已实现）

| 机制 | 位置 | 说明 |
|------|------|------|
| 每 MCP 会话短租约 | `maxmcp/instance_manager.py` → `IMaxInstance.locked_by` / `_by_session` | 一个 Max 实例同时只归一个会话 |
| 有界 FIFO 等待队列 | `InstanceManager.acquire_with_meta()` | 无空闲时按先来后到等，默认 60s，最多 32 个等待者（`QUEUE_FULL`） |
| 空闲自动释放 | `MAXMCP_LOCK_TTL`（默认 180s）+ 后台清扫 | 挂死会话不锁死实例 |
| 会话结束自动释放 | `server.py::_install_session_release_hook` → `ServerSession.__aexit__` | MCP 会话断开即释放租约 |
| 单实例串行 | `PROBE_SERIAL` / 全程串行 | Max 单线程，探活与调用不可重叠 |
| GoSkin 一键流程 | A 侧 `goskin_dev_flow.py` → 上传 → load_scene → ensure_ready → 选网格/骨骼 → `goskin_run_skin`（默认不点开始蒙皮）→ 人工确认 → `goskin_confirm_start` | 全程需 Agent 会话在线持有租约 |
| HTTP 自定义路由模式 | `maxmcp/tools/files.py` → `@mcp.custom_route("/files/upload")` | FastMCP 上挂 REST 路由的既有范式 |

### 1.2 缺口分析

| 现状 | 多用户完整任务需要 |
|------|-------------------|
| 排队的是"谁能用 Max"，不是"谁的任务" | 排队对象 = 任务（含场景文件、网格/骨骼参数） |
| Agent 必须全程在线持有租约 | 提交后即走，服务端托管执行，稍后取结果 |
| 「开始蒙皮」前强制人工门禁 | 队列任务需可配置自动确认（策略待定） |
| 失败即释放、无记录 | 任务级状态、日志、失败原因、重试/取消 |
| 无跨进程协调 | 服务端唯一协调点：可看全局实例与全队任务 |
| 队列在内存，重启即失 | 任务需持久化，重启可恢复 |

---

## 2. 设计目标与非目标

**目标**

1. 多 Agent 并发提交完整 GoSkin 任务，服务端公平、有序执行。
2. 提交 / 查询 / 取消三接口即可，Agent 端薄化。
3. 与现有交互式租约完全兼容共存，互不破坏。
4. 长任务不被空闲 TTL 误回收，取消可在安全点生效，失败可见可查。
5. 队列、结果、日志可持久化，服务端重启不丢任务。

**非目标（v1 不做）**

- 抢占式优先级（v1 只做 FIFO + 简单优先级字段）。
- Webhook/SSE 主动推送（v1 轮询；v2 可选）。
- 任务 DAG / 依赖关系（每个任务独立）。
- Agent 侧任务调度（明确不做：Agent 无法做跨会话全局协调）。
- 性能优化 / 多实例横向扩容之外的并发编排。

---

## 3. 总体架构

### 3.1 拓扑

```
 A1 ─┐                 ┌───────────────────────────── B（3dsmax-mcp 服务端） ─────────────────────────┐
 A2 ─┼─ MCP/HTTP ─────▶│  MCP tools（submit/get/list/cancel_goskin_job）                                │
 A3 ─┘                  │  HTTP /jobs/*（custom_route，参照 files.py）                                    │
                       │                                                                                 │
                       │   JobManager（新增，进程内单例）                                                 │
                       │     ├─ JobStore   （JSONL 持久化 + 恢复）                                        │
                       │     ├─ Scheduler  （后台调度线程 + worker session 租约）                          │
                       │     └─ Executor   （GoSkin 步骤执行器，复用 dialog_monitor.goskin_flow）        │
                       │                                    │  acquire/release（复用 InstanceManager）    │
                       └────────────────────────────────────│─────────────────────────────────────────────┘
                                                             ▼
                                               C1（Max 实例 A） C2（Max 实例 B） ...
```

- **协调只在 B**。A 不做任何排队、重试循环、busy 探测自旋。
- **租约仍是底层原语**：任务运行时由 JobManager 的 worker session 持租约；交互式 Agent 照旧 acquire/release。

### 3.2 组件与建议落点（仅方案，不实现）

```
maxmcp/jobs/__init__.py        # JobManager 单例（server.py 导入并启动 scheduler）
maxmcp/jobs/job_model.py       # Job / JobLogEntry / 状态枚举
maxmcp/jobs/job_store.py       # JSONL 追加式持久化 + 启动恢复
maxmcp/jobs/scheduler.py       # 调度线程：出队、池内分配实例、acquire、并发上限、续租、释放
maxmcp/jobs/executor_goskin.py # 复用 goskin_flow 的步骤执行器 + 安全点取消
maxmcp/tools/jobs.py           # MCP 工具包装 + @mcp.custom_route("/jobs/*")
```

---

## 4. 任务模型

### 4.1 Job 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `job_id` | str | UUID |
| `owner` | str | 提交者标识：MCP session id，或 HTTP `X-Client-Id` |
| `user_id` | str \| null | **审计**字段：提交方显式声明的用户标识（MCP 工具参数 / HTTP `X-Maxmcp-User-Id` 头），仅记录到任务日志与 Job 字段，**不参与 owner 鉴权** |
| `instance` | str \| null | 指定实例名（如 `max-8765`）；null = 任取空闲 |
| `scene_local_path` | str \| null | 已上传到 WORKSPACE_DIR 的 .max 绝对路径；null = 当前场景 |
| `mesh_names` / `bone_names` | str[] \| null | 网格/骨骼名（可空，空则走 propose/unhidden 自动推导） |
| `confirm_mode` | `auto` \| `manual` | 是否自动点「开始蒙皮」（策略见 §7.3） |
| `priority` | `high`/`normal`/`low` | 排序权重（默认 normal） |
| `status` | JobStatus | 状态机（§4.2） |
| `created_at` / `started_at` / `finished_at` | ts | 时间戳 |
| `log` | JobLogEntry[] | 步骤日志（尾部保留最近 N 条，全量在 JSONL） |
| `result` | dict | 成功：mesh/joint 计数、产物路径；失败：error、failed_step |
| `cancel_requested` | bool | 取消标志 |
| `retries` | int | 已重试次数（默认不重试） |

### 4.2 状态机

```
            ┌────────── cancel（排队中）→ cancelled
            ▼
  queued ──▶ running ──▶ succeeded
               │
               ├─▶ failed（执行异常 / 超时 / 实例离线）
               └─▶ cancelled（running 时在下一个安全点中止）
  running ──▶ awaiting_confirm ──▶ running（manual 模式收到 confirm 后）
```

- `awaiting_confirm`：仅 `confirm_mode=manual` 出现，见 §7.3。
- 终态：`succeeded` / `failed` / `cancelled`。终态记录保留 `MAXMCP_JOB_RETENTION`（默认 7 天）。

---

## 5. 存储与持久化

- **存储目录：** `MAXMCP_JOBS_DIR`，默认 `%LOCALAPPDATA%\3dsmax-mcp\jobs`。
- **格式：** 追加式 JSONL，每条一行（提交、状态迁移、日志行、结果、取消）。只 append、不原地改，避免并发写锁。
- **每 job 一个文件** `jobs/{job_id}.jsonl`，头行即完整 Job 快照，后续为增量事件；索引文件 `index.json`（job_id → 状态/时间）仅做快速列表，可由 JSONL 重建。
- **重启恢复规则：**
  - `queued` → 重新入队。
  - `running` / `awaiting_confirm` → 标记 `failed(reason=interrupted)`（或按 `MAXMCP_JOB_RECOVER_RUNNING` 重入队一次，默认不重入队，因为租约与场景状态已不可信）。
  - 终态 → 保留不动。
- **与场景文件的关系：** 场景上传复用现有 `/files/upload` 落 WORKSPACE_DIR；job 只存 `scene_local_path`。任务完成后可配置是否清理场景文件（`MAXMCP_JOB_CLEANUP_FILES`，默认保留）。

---

## 6. 调度器设计（核心）

### 6.1 worker session 与租约复用

调度器为每个运行中任务创建一个**合成 session 对象**（普通对象即可，`InstanceManager` 的锁只以对象身份区分）：

```python
worker_session = object()          # 每个 job 一个独立身份
client = manager.acquire(worker_session, name=job.instance,
                         wait=False, reset_scene=True)
# ... 用 client 直接驱动 Max ...
manager.release(worker_session)
```

- **完全复用** `InstanceManager.acquire / release / _by_session / dispatch_waiters`，不新造锁机制。
- `reset_scene=True`：多租户隔离，每个任务拿到的实例场景从干净状态开始（与现有 `MAXMCP_RESET_ON_ACQUIRE` 语义一致，但任务级强制默认开启）。
- **为什么 `wait=False` 快抢而非进交互 FIFO 排队：**
  - 交互 FIFO 面向短交互（默认等 60s、上限 32 等待者），任务可能排队很久，不应占交互等待槽。
  - 任务调度器自持有持久化任务队列，公平性由任务队列保证，无需再用内存 FIFO。
  - 代价：空闲瞬间存在与交互 acquire 的竞态（先到先得），可接受，作为已知取舍记录。
  - 补充建议：若后续需要严格优先级抢占空闲实例，可在 `InstanceManager` 加一个可选"释放回调"钩子（`on_instance_freed`），调度器注册后免轮询。v1 用短轮询（`MAXMCP_JOB_POLL_SECONDS`，默认 5s）。

### 6.2 并发与调度规则

| 规则 | 值/说明 |
|------|---------|
| 并发上限 | `MIN(MAXMCP_JOB_MAX_CONCURRENT, 在线实例数)`，默认 = 在线实例数 |
| 出队顺序 | 按 `priority`（high>normal>low）→ FIFO（created_at） |
| 实例绑定 | 指定 `instance` 的任务只等该实例空闲（且必须属于 `shared`/`jobs` 池）；未指定的任务任取空闲（分配顺序见 §6.5） |
| 提交者配额 | `MAXMCP_JOB_MAX_QUEUED_PER_OWNER`（默认 5），防止一人塞满 |
| 排队过期 | `MAXMCP_JOB_QUEUE_TTL`（默认 2h），过期自动 `failed(timeout)` |

### 6.3 租约续期（关键风险，必须处理）

**问题：** `MAXMCP_LOCK_TTL`（默认 180s）对"空闲"租约生效；而 GoSkin 单步最长 `complete_timeout_s`（OCR 等「完成」默认 300s），任务运行中可能超过 TTL 被 `_purge_stale_locks` 误回收，导致另一个任务/会话抢走实例。

**方案（推荐 v1）：** 给租约增加"任务占用"标识，TTL 清扫对其豁免：

- `IMaxInstance` 增加 `lease_kind: "session" | "job"`（或 `locked_by_job: bool`）。
- `_purge_stale_locks()` 跳过 `lease_kind == "job"` 且对应任务状态为 `running` / `awaiting_confirm` 的实例。
- JobManager 保证：任务终态（含异常/取消）一律在 `finally` 中 `manager.release(worker_session)`，不依赖 TTL。

**备选：** 心跳续期（调度器每 30s 调一次 `manager.touch(worker_session)`）。侵入更小但依赖周期线程，且清扫窗口内仍有竞态窗口。**推荐前者**——语义更清晰：任务在跑 = 实例不可让渡。

### 6.4 调度器生命周期

- 随 `server.py` 启动：`JobManager.start()` 起后台线程（daemon，非阻塞）。
- 循环：`空转 sleep(MAXMCP_JOB_POLL_SECONDS)` → 出队 → `acquire(wait=False)` → 成功则启动 executor 线程（每运行任务一线程，受并发上限约束）→ 回到循环。
- 任务结束（含异常）→ 写终态 → release → 继续调度下一个。

### 6.5 多实例分配：实例池（pool）

**需求：** 部分 Max 实例只服务于队列任务（无人值守、长时间占用），与交互式 Agent 会话隔离：任务排队时不抢走交互实例，交互会话也无法占住队列专用实例。

**配置（`max_instances.ini`）：**

```ini
[instances]
max1 = 192.168.139.45:8765          ; 未标注 → shared（默认：交互 + 队列共用）
max2 = 192.168.139.46:8765          ; 经 [pools] 标注后成为队列专用
max3 = 192.168.139.47:8765

[pools]
; 只分配给队列任务的实例（按 [instances] 中的实例名，逗号分隔）
jobs = max2, max3
; 可选对称扩展：只供交互会话使用的实例
; interactive = max4
```

**等效环境变量：**

| 环境变量 | 说明 |
|----------|------|
| `MAXMCP_JOB_INSTANCES` | 队列专用实例名（逗号分隔，与 `[pools] jobs` 等价，合并取并集） |
| `MAXMCP_INTERACTIVE_INSTANCES` | 交互专用实例名（可选） |

**池模型与可用性矩阵：**

| pool | 交互式 acquire | 队列任务调度 | 说明 |
|------|----------------|--------------|------|
| `shared`（默认） | ✅ | ✅（受 `MAXMCP_JOB_USE_SHARED` 约束） | 两边共用，先到先得 |
| `jobs`（队列专用） | ❌ 拒绝 | ✅ 优先 | 只服务队列任务 |
| `interactive`（可选） | ✅ | ❌ | 只服务交互会话 |

**任务分配实例的优先级顺序：**

1. 任务指定了 `instance`：校验该实例必须属于 `shared`/`jobs` 池，指向 `interactive` 池 → 提交时即失败。
2. 未指定：**先取空闲的 `jobs` 池实例**（专为队列而生，优先消化），全部被占用后再取 `shared` 池空闲实例。
3. `MAXMCP_JOB_USE_SHARED`（默认 true）：`false` = 严格隔离，队列任务只用 `jobs` 池，不占用任何 shared 实例。

**交互侧行为（A 端既有规则无感兼容）：**

- `list_instances` / `get_my_instance` 新增 `pool` 字段（`shared` / `jobs` / `interactive`）。
- 交互式 `acquire_instance`：
  - 未指定 name（自动选择）时**跳过** `jobs` 池实例；
  - 显式指定 `jobs` 池实例名 → 返回结构化错误 `INSTANCE_RESERVED`（含 `pool`、`retryable=false`），Agent 依现有规则停止重试、不做 busy-poll（与 instance-locks.md 的失败码约定一致）。
- 池过滤统一收敛到 `InstanceManager.acquire(..., for_job: bool)`：`for_job=true`（worker session 调用）允许 `{shared, jobs}`；默认 `false`（交互会话）允许 `{shared, interactive}`。

**调度并发统计：**

- 运行中任务数（含占用 jobs 池与溢出占用 shared 池的任务）计入 `MAXMCP_JOB_MAX_CONCURRENT`；
- shared 池被交互会话占用不影响任务并发上限，只影响任务可用的空闲实例数。

**边界：**

- 全部实例均为 `jobs` 池 → 交互 acquire 自动选择返回 `NO_FREE_INSTANCE`（错误信息明确提示"无交互可用实例"）。
- `jobs` 池实例离线 → 调度器照常跳过，队列任务仍可溢出到 shared（若 `MAXMCP_JOB_USE_SHARED=true`）。
- 队列为空时 `jobs` 池实例在 `list_instances` 中显示 `pool=jobs, busy=false`，交互会话仍不可用。

---

## 7. 执行流程（GoSkin Executor）

### 7.1 步骤（直接复用 `dialog_monitor.goskin_flow`，不新写点击逻辑）

与 A 侧 `goskin_dev_flow.py` 相同顺序，但由 B 端 executor 持 `MaxClient` 直接调用（这些函数签名本就接收 client 参数，见 `maxmcp/tools/goskin.py`）：

```
1. ensure_goskin_ready(client, menu, item)      # 开窗切到 蒙皮/全局蒙皮
2. restore_max_window（若最小化，OCR 点不到）
3. get_unhidden_meshes_bones / propose_skin_bones
4. run_goskin_skin(client, ..., click_start=False)   # 选网格/骨骼，停在「开始蒙皮」前
5. [confirm_mode=auto] run_goskin_skin(click_start=True) → 等 OCR「完成」→ 关「确定」
   [confirm_mode=manual] → 状态切 awaiting_confirm，等待 confirm 动作（§7.3）
```

**注意：** executor **不用** `server.py::SessionRoutedClient`（它按 MCP 会话路由），直接用 `acquire` 拿到的 `MaxClient`。

### 7.2 安全点与取消

任务线程在以下**离散步骤之间**检查 `job.cancel_requested`：

- 每步开始前（ensure_ready / cleanup / run_skin / confirm_start 前）；
- OCR 等待期间不硬中断（避免点击错位），等当前步超时/完成后在下一个安全点退出。

取消语义：`queued` → 立即 `cancelled`；`running` → 置标志，安全点中止，`failed(cancelled)`→ 实际为 `cancelled` 终态。

### 7.3 「开始蒙皮」确认门禁（首要待确认决策）

队列的意义是无人值守，但点「开始蒙皮」会真实改写场景骨骼，需明确策略：

| 模式 | 行为 | 适用 |
|------|------|------|
| `auto`（推荐默认，`MAXMCP_JOB_AUTO_CONFIRM=true`） | 任务跑到底，自动点击 | 有把握的批量/CI 场景 |
| `manual` | 任务停在 `awaiting_confirm`，**继续持租约**，等待 `POST /jobs/{id}/confirm` 或 MCP `confirm_goskin_job`；带持有上限 `MAXMCP_JOB_HOLD_TTL`（默认 30min），超时自动 `cancelled` | 需要人工把关 |

- 服务端提供全局开关 `MAXMCP_JOB_AUTO_CONFIRM`，单个任务可用 `confirm_mode` 覆盖。
- `awaiting_confirm` 必须**持租约**（场景已就绪，释放后他人会换场景，确认将失效）→ 这正是 §6.3 续期方案要覆盖的第二种状态。

### 7.4 超时

- `MAXMCP_JOB_RUN_TIMEOUT`（默认 1800s）：任务整体超时 → `failed(timeout)`，释放租约。
- 单步 OCR 等待沿用 `complete_timeout_s`（默认 300s，可随任务参数覆盖）。

---

## 8. API 设计

### 8.1 MCP 工具（主接口，Agent 首选）

| 工具 | 入参 | 返回 |
|------|------|------|
| `submit_goskin_job` | `scene_local_path`（可选，null=当前场景）、`instance`、`mesh_names`、`bone_names`、`confirm_mode`、`priority` | `{job_id, status=queued, queue_position, estimated}` |
| `get_goskin_job` | `job_id` | 完整 Job（状态、日志尾 N 条、结果/错误） |
| `list_goskin_jobs` | `status`、`limit`、`offset`（默认只看自己的） | Job 列表 |
| `cancel_goskin_job` | `job_id` | `{ok, status}` |
| `confirm_goskin_job` | `job_id`（manual 模式确认「开始蒙皮」） | `{ok, status=running}` |

- 走既有工具注册路径（`mcp.tool()`，与 `goskin.py` 一致），full/core 直呼，progressive 走 `call_tool`。
- 上传场景文件：MCP 侧走 `workspace_upload` 拿到 `local_path` 再提交；HTTP 侧直接 multipart 一步到位（§8.2）。

### 8.2 HTTP 路由（参照 `files.py` 的 `@mcp.custom_route`）

| 方法/路径 | 说明 |
|-----------|------|
| `POST /jobs` | multipart（scene 文件 + 参数 JSON 字段），一步提交 |
| `GET /jobs/{id}` | 查询状态/日志/结果 |
| `GET /jobs?status=&owner=&limit=` | 列表（分页） |
| `POST /jobs/{id}/cancel` | 取消 |
| `POST /jobs/{id}/confirm` | manual 模式确认「开始蒙皮」 |

- `owner`：HTTP 侧取 `X-Client-Id` 头；缺失则视为"匿名公共队列"（受配额与 `MAXMCP_JOB_ANON_QUEUE_MAX` 约束）。跨用户查询/取消权限：仅本人 + `MAXMCP_JOB_ADMIN_IDS`（逗号分隔 client id）可全量。
- 审计 `user_id`：提交方可额外带 `X-Maxmcp-User-Id` 头（MCP 工具则传 `user_id` 参数），**仅写入任务日志与 `Job.user_id`**，用于追踪谁提交的，不影响 owner。
- 鉴权沿用现状（该服务无登录态），文档明示"内部工具，勿裸暴露公网"。

### 8.3 提交示例（HTTP）

```
POST /jobs   （multipart: file=scene.max, params={"confirm_mode":"auto","mesh_names":[...]})
→ 202 {"job_id":"...","status":"queued","queue_position":3}

GET /jobs/{id}
→ 200 {"job_id":"...","status":"running","progress":[{"t":"...","step":"run_skin","note":"等待OCR完成"}],
        "log_tail":[...]}
```

---

## 9. 公平性、配额与安全

| 维度 | 措施 |
|------|------|
| 公平 | FIFO + 优先级字段；`MAXMCP_JOB_MAX_QUEUED_PER_OWNER`（默认 5） |
| 并发隔离 | 每任务独立 worker session + `reset_scene=True` |
| 取消/查询权限 | 仅 owner + 管理员白名单 |
| 场景文件 | 复用 WORKSPACE_DIR 沙箱（files.py 已有路径穿越防护） |
| 结果保留 | `MAXMCP_JOB_RETENTION`（默认 7 天）后清理 JSONL 与产物 |
| 与交互租约 | 任务占用实例时 `list_instances` 显示 busy，交互 acquire 正常排队/失败——现有 A 侧规则（instance-locks.md）无需改动 |

---

## 10. 配置项（新增，全部可选，均有默认）

| 环境变量 | 默认 | 说明 |
|----------|------|------|
| `MAXMCP_JOBS_DIR` | `%LOCALAPPDATA%\3dsmax-mcp\jobs` | 任务持久化目录 |
| `MAXMCP_JOB_MAX_CONCURRENT` | = 在线实例数 | 同时运行任务上限 |
| `MAXMCP_JOB_RUN_TIMEOUT` | 1800s | 单任务总超时 |
| `MAXMCP_JOB_QUEUE_TTL` | 7200s | 排队过期 |
| `MAXMCP_JOB_MAX_QUEUED_PER_OWNER` | 5 | 每提交者排队上限 |
| `MAXMCP_JOB_INSTANCES` | 空 | 队列专用实例名（逗号分隔；亦可用 `[pools] jobs=`） |
| `MAXMCP_INTERACTIVE_INSTANCES` | 空 | 交互专用实例名（可选；`[pools] interactive=`） |
| `MAXMCP_JOB_USE_SHARED` | true | 任务是否可占用 `shared` 池实例（false=严格隔离只用 jobs 池） |
| `MAXMCP_JOB_POLL_SECONDS` | 5 | 调度器轮询间隔 |
| `MAXMCP_JOB_AUTO_CONFIRM` | true | 全局「开始蒙皮」自动确认开关 |
| `MAXMCP_JOB_HOLD_TTL` | 1800s | manual 模式 awaiting_confirm 持有上限 |
| `MAXMCP_JOB_RETENTION` | 7 天 | 终态任务保留时长 |
| `MAXMCP_JOB_RECOVER_RUNNING` | false | 重启后 running 任务是否重入队 |
| `MAXMCP_JOB_CLEANUP_FILES` | false | 终态后是否删场景文件 |
| `MAXMCP_JOB_ADMIN_IDS` | 空 | 可管理他人任务的白名单 |
| `MAXMCP_JOB_ANON_QUEUE_MAX` | 3 | 匿名（无 X-Client-Id）排队上限 |

---

## 11. 与现有代码的集成点（引用）

| 现有代码 | 改动/对接方式 |
|----------|---------------|
| `maxmcp/instance_manager.py` → `InstanceManager` 单例 `manager` | **复用** acquire/release/`_by_session`；**小改**：① `IMaxInstance` 增 `lease_kind` 与 `pool`（`shared`/`jobs`/`interactive`），`_purge_stale_locks()` 豁免 `job` 型占用；② 解析 `[pools]` 节 / `MAXMCP_JOB_INSTANCES` 等池标注；③ `acquire(..., for_job: bool)` 做池过滤，交互 acquire 跳过/拒绝 `jobs` 池（`INSTANCE_RESERVED`）；④ `list_instances` / `get_my_instance` 暴露 `pool`；可选：`release` 处加 `on_instance_freed` 回调（免轮询） |
| `maxmcp/server.py` | `import` JobManager 并 `start()`；`_install_session_release_hook` 不动（worker session 由 JobManager 自己释放） |
| `maxmcp/tools/files.py` 的 `@mcp.custom_route` 范式 | 新 `maxmcp/tools/jobs.py` 同范式挂 `/jobs/*` |
| `maxmcp/tools/goskin.py` + `dialog_monitor/goskin_flow.py` | executor 直接调用 `ensure_goskin_ready / run_goskin_skin / confirm_goskin_start(client, ...)`，不经过 SessionRoutedClient |
| `maxmcp/workspace_config.py` `get_ocr_base()` | executor 取 OCR 端点 |
| A 侧 `skills/3dsmax-mcp-remote` | 不改排队逻辑；新增 `submit/get/list/cancel` 说明与脚本（后续另出文档） |

---

## 12. 边界情况与恢复

| 场景 | 行为 |
|------|------|
| 服务端重启 | `queued` 重入队；`running`/`awaiting_confirm` 标记 `failed(interrupted)`（默认）或重入队一次 |
| Max 离线（任务运行中） | 对应调用报错 → `failed`，记录 `instance_offline` |
| 提交时无任何在线实例 | 照常入队；调度器等到有实例再跑（受 `QUEUE_TTL` 约束） |
| 排队期间实例下线 | 指定实例的任务保持排队直到 TTL；未指定实例的任务等到有空闲实例 |
| 取消竞态 | `running` 取消后，若当前步 OCR 等待仍在进行，等其结束再在安全点退出 |
| 多实例并行 | 每个空闲实例各跑一个任务；`list_instances` 对每个都正确显示 busy |
| 与交互式会话撞车 | 双方都是"空闲即抢"，先到先得；不做抢占 |
| 场景已是目标场景（未传文件） | `reset_scene=True` 会清掉；因此"复用当前场景"的提交默认要求 `reset_scene=false`（由 `scene_local_path=null` 且 `preserve_scene=true` 显式声明），否则按干净场景执行 |

---

## 13. 分期实施建议

**v1（最小可用）**

- Job 模型 + JSONL 持久化 + 恢复（queued 重入队）。
- Scheduler（wait=False 快抢 + 短轮询 + 并发上限）。
- `lease_kind="job"` + TTL 豁免 + finally 必释放。
- GoSkin executor（复用 goskin_flow，`confirm_mode=auto` 为主）。
- MCP 工具 `submit/get/list/cancel` + HTTP `/jobs` 基础四路由。
- 配置项按 §10 全量落地（均有默认）。

**v2（增强，可选）**

- `confirm_mode=manual` + `awaiting_confirm` 持有语义 + confirm 路由。
- `on_instance_freed` 回调免轮询；优先级严格排序。
- Webhook 回调（终态通知）或 SSE 进度流。
- 运行中视口截图回传（在 worker 会话内直接 `capture_viewport`，不额外租约）。
- A 侧技能包新增"提交任务"脚本与文档。

---

## 14. 待确认决策清单

1. **「开始蒙皮」门禁**：`auto`（无人值守，默认建议）还是 `manual`（人工确认，代价是占住实例）？
2. **任务持久化位置**：`%LOCALAPPDATA%`（随 B 机器）还是 WORKSPACE_DIR（可共享/备份）？
3. **HTTP API 是否需要**：仅 MCP 工具是否足够（A 全走 MCP）？HTTP `/jobs` 主要给非 MCP 客户端/运维用。
4. **失败是否自动重试**：默认 0 次（推荐），或允许 `retries` 字段。
5. **running 任务重启恢复**：默认 `failed(interrupted)`（推荐）还是重入队？
6. **多实例并行是否需要按任务指定实例**：默认支持（`instance` 字段可空）。
