# GoSkin 批量任务队列（Job Queue）

> 服务端 B 的**任务仲裁层**（`maxmcp/jobs/`，设计文档 `docs/goskin-job-queue-design.md`）。
> 多人/批量提交自动蒙皮时**优先走队列**，不要各自 `acquire_instance` 抢实例。

## 什么时候用队列

自动蒙皮**统一走队列**，`goskin_dev_flow.py` 不再用于自动蒙皮任务（保留仅供低层 OCR 流程调试参考，不面向任务提交）。

| 场景 | 用 |
|------|-----|
| 单个自动蒙皮任务（也要尽快独占跑完） | **队列工具（本页）** |
| 多用户并发提交 / 批量文件排队 / 不想自己管实例租约 | **队列工具（本页）** |

队列的核心承诺：提交后服务端**自动排队、自动挑空闲实例（jobs/shared 池）、自动跑完整 GoSkin 流程**，Agent 只需轮询终态。**不要在队列任务上自己 `acquire_instance`**——实例由服务端 worker 独占租给任务，你手动 acquire 会互相卡死（`INSTANCE_BUSY`）。

## 工具速查（5 个）

| 工具 | 用途 | 关键返回 |
|------|------|----------|
| `submit_goskin_job` | 提交任务（唯一入口） | `{job_id, status=queued, queue_position, user_id, confirm_mode, status_url}` |
| `get_goskin_job` | 查单个：状态/日志尾/结果 | `{status, log[], result, error}` |
| `list_goskin_jobs` | 列自己的任务（`status`/`limit`/`offset` 过滤） | `{jobs[], count}` |
| `cancel_goskin_job` | 取消（自己或管理员） | `{ok, status}` |
| `confirm_goskin_job` | manual 模式点「开始蒙皮」放行 | `{ok, status=running}` |

## 提交参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `scene_local_path` | 否 | 上传后拿到的本地路径；**省略 = 用被分配实例的当前场景** |
| `instance` | 否 | 钉到指定实例（必须是 `shared`/`jobs` 池；`interactive` 池会被拒 `INSTANCE_RESERVED`）。省略 = 服务端任选空闲 |
| `mesh_names` / `bone_names` | 否 | 省略 = 自动解析场景里未隐藏的网格/骨骼 |
| `confirm_mode` | 否 | `auto`（默认，服务端自动点「开始蒙皮」）或 `manual`（停在 `awaiting_confirm` 等人确认） |
| `priority` | 否 | `high` / `normal`（默认）/ `low` |
| `user_id` | 否（尽力而为） | **审计字段**（谁提交的）。**能拿到标识就带**：MCP 参数 `user_id`，或 HTTP 请求头 `X-Maxmcp-User-Id`（**二选一**；MCP 工具参数未传/为空时自动兜底读该请求头）。**拿不到（如 agent 环境无用户标识）就空着照常提交**——空串被归一为 null，靠 owner/session 兜底审计，不会导致任务失败。用 `McpHttpSession` 脚本提交时构造传 `audit_user_id=<用户标识>` 自动带该头。只记录到任务日志与 Job 的 `user_id` 字段，**不参与鉴权** |

## 标准流程

```
1. workspace_upload <本地.max>          # 拿 local_path（绝对路径）
2. submit_goskin_job(scene_local_path=..., user_id=<你的用户标识>)   # → job_id（user_id 必传）
3. 轮询 get_goskin_job(job_id)              # 直到终态
```

状态机：`queued → running → succeeded | failed | cancelled`；manual 模式多一个 `running → awaiting_confirm → running`（确认后自动跑完）。

**提交入队不需要有在线/空闲实例**：`submit_goskin_job` 只做入队，服务端按队列调度、等有可用实例（jobs/shared 池）时自动派发。**即使当前没有任何在线/空闲 Max 实例也要照常提交**——任务会保持在 `queued` 排队等待，实例一释放就自动开始；无实例只是排队更久，不是提交失败。**不要**用"没有在线或空闲实例"当理由拒绝入队，也**不要**先查实例列表再决定是否提交。

- **auto（默认）**：提交后**不要询问用户"是否开始蒙皮"**——服务端自动点「开始蒙皮」并跑完，Agent 直接展示 status_url 后轮询到 `succeeded`/`failed` 即可。
- **manual**：看到 `awaiting_confirm` 后（征得用户同意再）调 `confirm_goskin_job`；该状态下任务**继续持有实例租约**，有持有上限（默认 30min，超时自动 `cancelled`）。
- **向用户展示 status_url（必做）**：提交成功返回的 `status_url`（`http://<host>/jobs/<id>/view?t=<token>`，**已含访问令牌**）**必须作为可点击链接放进你的回复**，并附一句说明——"打开可实时查看进度/排队位置，也可在页面上取消任务"。不要只回 job_id，**也不要在 auto 模式下附带"是否开始蒙皮"的询问**。
- 轮询：MCP 工具往返自带延迟，多调几次 `get`/`list` 即可；没有服务端推送。
- 取消：`queued`/`awaiting_confirm` 立即终态；`running` 在下一个安全点停止（executor 每步检查）。

## 权限与错误

- 查询/取消/确认**只允许任务 owner（提交的 MCP 会话 / `X-Client-Id`）和管理员**（`MAXMCP_JOB_ADMIN_IDS`）。看别人的任务 → `JOB_PERMISSION_DENIED`。
- 审计 user_id（**尽力而为，不阻塞提交**）：`submit_goskin_job` 参数尽量传 `user_id`（HTTP 提交带 `X-Maxmcp-User-Id` 头；`McpHttpSession` 构造传 `audit_user_id` 自动带该头）。**仅记录到任务日志/Job 字段**，供追踪"谁提交的"，不改变 owner；拿不到标识就空着提交，空串归一为 null、靠 owner/session 兜底审计。
- 结构化错误（同 instance-locks 约定，`retryable=false` 就**不要重试**）：

| code | 含义 | 动作 |
|------|------|------|
| `JOB_NOT_FOUND` | job_id 不存在 | 检查 id |
| `JOB_QUOTA_EXCEEDED` | 每提交者排队上限（默认 5） | 先取消/等跑完 |
| `JOB_PERMISSION_DENIED` | 非 owner/非管理员 | 不重试 |
| `JOB_STATE` | 状态不允许该操作（如重复 confirm） | 按返回的状态走 |
| `INSTANCE_RESERVED` | 指定的实例属于 interactive 池 | 换实例或不指定 |

## HTTP 等价路由（有 `MAXMCP_URL` 时）

| 方法/路径 | 说明 |
|-----------|------|
| `POST /jobs` | multipart：`file`=场景 + `params`=JSON（其余提交参数） |
| `GET /jobs?status=&owner=&limit=&offset=` | 列表（非管理员强制只看自己的） |
| `GET /jobs/{job_id}` | 详情 |
| `POST /jobs/{job_id}/cancel` | 取消 |
| `POST /jobs/{job_id}/confirm` | manual 确认 |

owner 取 `X-Client-Id` 请求头，缺失走匿名公共队列（上限 `MAXMCP_JOB_ANON_QUEUE_MAX`，默认 3）。**每次提交尽量带 `X-Maxmcp-User-Id` 头**做审计记录（写入任务日志/`user_id` 字段）；agent 拿不到用户标识时可不带，不影响提交。

## 队列专用实例（可选配置）

- `[pools] jobs=`（`max_instances.ini`）或 `MAXMCP_JOB_INSTANCES` 可声明**只服务队列任务**的 Max 实例，交互会话不可占用它们。
- `list_instances` 会显示 `pool`（`shared`/`jobs`/`interactive`）与 `lease_kind`；`MAXMCP_JOB_USE_SHARED=false` 时队列严格只用 jobs 池。

## 常见陷阱

- 别在提交前自己 `acquire_instance` 再等任务——任务和你的会话抢同一实例。
- 传 `scene_local_path` 时用**上传返回的 `local_path`**，不是 URL、不是文件名。
- `confirm_goskin_job` 只对 `awaiting_confirm` 有效，`auto` 任务重复确认会 `JOB_STATE`。
- 队列空时 jobs 池实例在 `list_instances` 显示 `busy=false` 但交互会话仍不可用，属正常。
