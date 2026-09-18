# GoSkin OCR 模拟点击（远程 Agent）

面向 **`3dsmax-mcp-remote`**（Agent 主机 A）。自动化 **自动蒙皮 / GoSkinning** 前请完整阅读。

在 A 上只通过 **MCP 工具**或本 skill 的 `scripts/goskin_dev_flow.py` 驱动 B；**不要**在 A 上改 `max_instances.ini`、`import maxmcp` 或跑仓库 `dialog_monitor/_probe_*.py`。

OCR / 实例表 / workspace 由 **MCP 服务器 B** 的运维配置；A 用 `check_dialog_ocr_health` 与 `list_instances` 探测即可。完整服务端与维护者说明见本地 skill 包中的同名文档（`3dsmax-mcp-dev`）。

## 何时使用

- 用户要求 Auto GoSkin / 自动蒙皮 / 全局蒙皮，且无可用 MaxScript API。
- 插件 UI 为 Qt：无可靠子 HWND → 截图 + OCR + 模拟点击（在 B/C 上执行）。
- 渐进式工具集：先加载 `dialog_ui`，再 `call_tool`。

## 硬性规则（顺序不可打乱）

1. **先选定 Max**，再 OCR / 场景操作。空闲实例 >1 时必须让用户选，禁止自动抢第一台。
2. 优先 `goskin_*`，不要反复裸调 `click_plugin_dialog_button`。
3. 打开插件时 **`menu`=顶栏、`item`=下拉项**（默认 `自动蒙皮` → `GoSkinning`），**禁止对调**。
4. 用户确认 `goskin_run_skin` 摘要前，**绝不**点「开始蒙皮」。
5. 先聚焦列表槽位「(选中后在编辑区添加)」再点「选定」。
6. 用 OCR 计数（`模型：N` / `关节：N`）校验，不单信点击 `ok`。
7. 场景选择为空时禁止「选定」。
8. Max 所在桌面须 **未锁定**（锁屏 / RDP 断连 → 空成功点击）。

## 选择 Max 实例（第一步）

1. `list_instances`（不要并行 `list_max_instances` / `get_bridge_status`）。空闲 = `online` 且非 `busy`。
2. 0 台 → 停止；1 台 → 告知用户后 `acquire_instance(name=...)`；≥2 台 → 列出并等用户点名。
3. `get_my_instance` 可确认绑定。全程串行请求。

便捷脚本：优先直接调 MCP `list_instances`。若跑 `scripts/list_online_instances.py`，须 `--url` / `MAXMCP_URL` = IDE 已配置的 MCP URL（勿猜 localhost）。

## 打开插件：`--menu` / `--item`（勿对调）

`goskin_ensure_ready` / `goskin_dev_flow.py` 用 OCR 打开菜单，**两段文字含义不同，顺序固定**：

| 参数 | 点哪里 | 默认值 | 说明 |
|------|--------|--------|------|
| `menu` / `--menu` | Max **顶栏**菜单文字 | `自动蒙皮` | 先点顶栏，展开下拉 |
| `item` / `--item` | 下拉里的**菜单项** | `GoSkinning` | 再点子项，打开对话框 |

- 默认等价于：顶栏「自动蒙皮」→ 子项「GoSkinning」。
- **禁止**写成 `--menu GoSkinning --item 自动蒙皮`（对调后会在顶栏找英文 `GoSkinning`，OCR 匹配失败）。
- 若现场顶栏不是「自动蒙皮」（例如 `NDBox`），只改 `--menu` 为**肉眼所见的顶栏文字**；`--item` 仍用下拉里真实子项名。
- 不确定时：不传 `--menu`/`--item`，用脚本默认；或让用户读出顶栏/下拉原文再填。

典型错误（参数对调）：

```text
failed to open menu 'GoSkinning': no OCR line matched text='GoSkinning' with score>=0.5
```

→ 把 `menu`/`item` 换回默认，或按上表对照现场 UI 改正。

## 标准工作流

也可用一键准备（默认不点「开始蒙皮」）。**优先**用 IDE 已配置的 MCP 调 `goskin_*`；若跑脚本，`MAXMCP_URL`/`--url` 必须等于该 MCP 的 URL（**勿猜** `127.0.0.1`）：

```bash
set MAXMCP_URL=<与 mcp.json 中 3dsmax-mcp 相同的 URL>
python scripts/goskin_dev_flow.py --instance <name>
# 等同显式默认（勿对调）：
# python scripts/goskin_dev_flow.py --instance <name> --menu 自动蒙皮 --item GoSkinning
# 用户确认后：
python scripts/goskin_dev_flow.py --instance <name> --confirm-start
```

或逐步 MCP：

```
Task Progress:
- [ ] list_instances（空闲>1 则停下来让用户选）
- [ ] acquire_instance(name=用户选定)
- [ ] check_dialog_ocr_health
- [ ] goskin_ensure_ready(menu="自动蒙皮", item="GoSkinning")   # menu=顶栏, item=下拉项
- [ ] get_unhidden_meshes_bones（或已知名时跳过）
- [ ] propose_skin_bones(mesh_handles=...)（多骨架/杂骨时筛骨；单骨架可跳过）
- [ ] goskin_run_skin(mesh_handles=..., bone_handles=..., click_start=false)
- [ ] 向用户展示 user_prompt；等待 OK
- [ ] goskin_confirm_start(user_confirmed=true)
```

| 步骤 | 工具 | 成功信号 |
|------|------|----------|
| 选 Max | `list_instances` → `acquire_instance` | 已绑定空闲实例 |
| 健康检查 | `check_dialog_ocr_health` | OCR 可达 |
| 打开 UI | `goskin_ensure_ready(menu=顶栏, item=下拉项)` | 全局蒙皮页就绪 |
| 收集 | `get_unhidden_meshes_bones` | handles 非空 |
| 筛骨 | `propose_skin_bones(mesh_handles=...)` | `bones_handle` 为邻近骨 |
| 准备 | `goskin_run_skin` | `awaiting_start_confirm`，模型/关节≥1 |
| 开始 | `goskin_confirm_start(user_confirmed=true)` | OCR「完成」后点「操作成功」→「确定」（默认等满 300s） |

优先传 `mesh_handles` / `bone_handles`。多骨架时先 `propose_skin_bones` 再把返回的 `bones_handle` 交给 `goskin_run_skin`。`goskin_run_auto` 默认仍在开始前暂停。

服务端内部顺序（勿自行重写）：dismiss 警告 → cleanup → 槽位 → 选定模型 → 选定关节 → **停止等确认** →（确认后）开始蒙皮 → 等「完成」→ 点「操作成功 / 确定」。

`goskin_confirm_start` 默认 `complete_timeout_s=300`（5 分钟）；复杂模型可再加大。远程脚本 HTTP `--timeout` 须 ≥ 该值（默认 600）。

## 失败模式（A 侧处理）

| 现象 | 处理 |
|------|------|
| `no OCR line matched text='GoSkinning'`（open menu） | 服务端 `click_menu_path` **已自动再试 1 次**（防桌面误触）。仍失败：多半是 **menu/item 对调**或顶栏文字与 `--menu` 不一致；改回 `menu=自动蒙皮` / `item=GoSkinning` |
| 点击 ok 但 OCR 无变化 | 请用户解锁 Max 桌面后重跑 |
| 警告弹窗 / UI 卡住 | 先 dismiss「确定」，再聚焦槽位后选定 |
| `mesh_not_added` | 确认选择非空；重跑 ensure/run；仍失败交 B 运维查坐标映射 |
| OCR 不可达 | `check_dialog_ocr_health`；A **不改** ini，请 B 运维修 OCR/防火墙 |
| 截图无用 | 保持会话可交互；必要时请 B 查 snapshot |

## 相关

- MCP：`goskin_*`、`recognize_plugin_dialog`、`click_plugin_dialog_button`、`check_dialog_ocr_health`
- 本包脚本：`scripts/goskin_dev_flow.py`、`scripts/README.md`
- 租约细节：[instance-locks.md](instance-locks.md)
