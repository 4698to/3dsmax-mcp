# GoSkin OCR 模拟点击（本地 / 维护者 + 服务端说明）

完整说明：含 **B 服务器配置**、点击原理、以及仓库内 `_test_goskin_*` 冒烟。  
远程 Agent（A）请优先读 **`3dsmax-mcp-remote`** 包内同名文档（无 ini、无 MaxClient）；本文件主要给 **运维 / 完整仓库维护者**。

配套实现（在 **MCP 服务端 B**）：`dialog_monitor/` · MCP 工具：`goskin_*`、`recognize_plugin_dialog`、`click_plugin_dialog_button`。

## 何时使用

- 用户要求运行 Auto GoSkin / 自动蒙皮 / 全局蒙皮，且没有可用的 MaxScript API。
- 插件 UI 为 Qt：没有可靠的子 HWND → 截图 + 外部 OCR + 模拟点击。
- 渐进式工具集：先加载 toolset `dialog_ui`，再 `call_tool`。

## 硬性规则（顺序不可打乱）

1. **任务开始前先选定 Max 端，再做 OCR / 场景操作。** 见下方「选择 Max 实例」。有多个空闲可用实例时必须让用户选，禁止自动抢第一台。
2. 优先使用专用 GoSkin 工具，而不是反复调用原始的 `click_plugin_dialog_button`。
3. **在用户明确确认** `goskin_run_skin` 返回的摘要之前，**绝不**点击「开始蒙皮」。
4. **在先聚焦列表槽位**「(选中后在编辑区添加)」（或已有的 `模型：N` 行）之前，**绝不**点击「选定」。跳过这一步会弹出警告盖住对话框，后续 OCR 会失败。
5. 不要只信点击返回的 `ok:true`——必须用 OCR（`模型：N` / `关节：N`）或返回的计数做校验。
6. 场景选择为空时 → 拒绝「选定」（会触发 GoSkin 警告）。
7. 远程主机必须保持 **未锁定**。锁屏 / RDP 断连时，点击会报告成功但 UI 不变。关闭显示器通常没问题。

## 点击机制

Max 内部（`MCP_DialogMonitor.clickAtScreen`）：

1. 对对话框执行 `SetForegroundWindow`
2. `SetCursorPos(x, y)` — 物理屏幕像素
3. `mouse_event(LEFTDOWN)` → sleep → `mouse_event(LEFTUP)`

坐标链路：对话框快照 → OCR 框 → `image_to_screen`（优先使用 **client rect**；当图像高度大于客户区高度且宽度一致时，减去顶部 padding——用外层窗口映射会点偏列表行）。

## 配置（`max_instances.ini` — 仅 MCP 服务器 B）

**Agent 主机 A 不编辑此文件。** 下面由 B 上运维配置；A 只调 `check_dialog_ocr_health` / `list_instances`。

```ini
[instances]
max1 = 192.168.139.45:8765

[workspace]
path = \\share\3dsmax-mcp\workspace

[ocr]
base = http://192.168.139.130:8000
```

| 键 | 作用 |
|-----|------|
| `[ocr] base` | HTTP OCR 根地址。优先级：工具参数 `ocr_base` > `MAXMCP_OCR_BASE` > ini > 内置默认值 |
| Endpoints | `GET {base}/v1/ocr/health` · `POST {base}/v1/ocr` |
| `[workspace]` | 跨机共享目录。Max 先写 `%TEMP%\3dsmax-mcp`，有效可写时再复制到此路径（同机或 HTTP 上传可用时可省略） |

探测：`check_dialog_ocr_health` → `ok`、`endpoints`、`workspace.shared_configured`。

## 选择 Max 实例（任务第一步，先于 OCR）

GoSkin 点击打在 **当前绑定的那一台** Max 的桌面上。选错实例会点到别人的会话或离线端口。

1. 调用 **`list_instances`**（不要并行再调 `list_max_instances` / `get_bridge_status`）。空闲可用 = `online=true` 且 `busy=false`。`online` 表示 TCP 或 Native 任一可达（见 `tcp_online` / `native_online` / `transports`）。`busy=true` 不要抢。
2. 按空闲台数分支：
   - **0 台**：停止。向用户说明没有空闲在线 Max，不要继续 `goskin_*`。
   - **1 台**：告知用户将使用该实例（name / host:port / transports），然后 `acquire_instance(name=<该实例>)`。
   - **2 台及以上**：列出空闲实例（name、host、port、pid、transports），**询问用户选哪一台**。收到明确选择前不要 `acquire_instance`，也不要调用无 `name` 的自动领取。
3. 用户选定后：`acquire_instance(name=<用户选的名字>)`，可用 `get_my_instance` 确认绑定。
4. 本机 native 信息已包含在 `list_instances` 的 `pid` / `native_online` 里。不要为了再查一遍去并发 `list_max_instances`。仅当用户指定 pid 且尚未绑定 native 时，再单独 `select_max_instance(pid)`。
5. 用户已指定实例名 / 已持有锁且仍空闲属于自己：跳过询问，沿用当前实例。
6. Max 单线程：全程一次只发一个请求（探活、OCR、场景工具都串行）。

## MCP Agent 标准工作流

流程图（已保存）：
- 完整版：[`images/goskin-ocr-flow.png`](../../images/goskin-ocr-flow.png)
- 简化版：[`images/goskin-ocr-flow-simple.png`](../../images/goskin-ocr-flow-simple.png)
- 工作报告：[`docs/goskin-ocr-click-work-report.md`](../../docs/goskin-ocr-click-work-report.md)

```
Task Progress:
- [ ] list_instances（空闲>1 则停下来让用户选）
- [ ] acquire_instance(name=用户选定)
- [ ] check_dialog_ocr_health
- [ ] goskin_ensure_ready
- [ ] get_unhidden_meshes_bones（或已知名时跳过）
- [ ] goskin_run_skin(mesh_handles=..., bone_handles=..., click_start=false)
- [ ] 向用户展示 user_prompt / 确认摘要；等待用户 OK
- [ ] goskin_confirm_start(user_confirmed=true)   # 仅在用户确认后
```

### 步骤明细

| 步骤 | 工具 | 成功信号 |
|------|------|----------|
| 选 Max | `list_instances` → 用户选择（仅当空闲>1）→ `acquire_instance` | 已绑定一台空闲在线实例 |
| 健康检查 | `check_dialog_ocr_health` | OCR 可达 |
| 打开 UI | `goskin_ensure_ready` | 全局蒙皮页可见「编辑区」/「开始蒙皮」 |
| 收集对象 | `get_unhidden_meshes_bones` | `meshes_handle` / `bones_handle` 非空 |
| 准备 | `goskin_run_skin` | `awaiting_start_confirm=true`，模型≥1、关节≥1 |
| 门禁 | （对话） | 用户同意名称/数量 |
| 开始 | `goskin_confirm_start(user_confirmed=true)` | OCR 看到「完成」（或工具等待成功） |

`goskin_run_auto` = ensure + run_skin；默认仍会在「开始蒙皮」前 **暂停**。

优先传入 `mesh_handles` / `bone_handles`（`get_unhidden_meshes_bones` 返回的 AnimHandle）。已知对象名时也可传 `mesh_names` / `bone_names`。否则在点「选定」前确保 Max 中已有有效选择。不要为收集未隐藏网格/骨骼再写临时 MAXScript。

`run_goskin_skin` 内部顺序（已实现——不要自行重写）：

1. 关闭残留警告  
2. 必要时清理脏列表  
3. 点击「(选中后在编辑区添加)」  
4. Max 选中模型 → 在「在场景中选择模型」上点「选定」→ 校验 `模型：N≥1`  
5. 聚焦 `模型：N` → Max 选中骨骼 → 在「在场景中选择关节」上点「选定」→ 校验 `关节：N≥1`  
6. 返回确认摘要；**停止**

## 失败模式

| 现象 | 可能原因 | 处理 |
|------|----------|------|
| 点击 ok 但 OCR 无变化 | 锁屏 / RDP 断连 | 解锁可交互桌面 |
| 警告弹窗 / UI 卡住 | 未聚焦列表槽位就点「选定」 | 点「确定」dismiss，再先聚焦「(选中后在编辑区添加)」 |
| `mesh_not_added` | 槽位点偏、Y 映射错误、或选择为空 | 解锁后重跑；传入 mesh 名；检查 client-rect 映射 |
| OCR 不可达 | `[ocr] base` 错误 / 防火墙 | 在 **B** 修正 ini / `MAXMCP_OCR_BASE`；重新健康检查 |
| 截图全黑 / 无用 | snapshot 参数 / 会话状态 | 使用普通 `windows.snapshot`；保持会话可交互 |

## 维护者本机脚本（完整仓库，非 remote skill）

仅当本机有完整 `3dsmax-mcp` 仓库（或已安装 `maxmcp`），且能直连目标 Max 时使用。  
**硬性规则：** 冒烟先复用 `_test_goskin_*.py`，不要聊天里随手新写一次性脚本。

| 脚本 | 用途 | 默认 Max 目标 |
|------|------|----------------|
| `_test_goskin_ensure.py` | 仅冒烟 `ensure_goskin_ready` | 先试 `127.0.0.1`，再试 `192.168.139.45` |
| `_test_goskin_prestart.py` | 完整准备到确认门禁（`click_start=False`） | `127.0.0.1:8765` |
| `_test_goskin_slotfocus.py` | 槽位 + 选定 | `192.168.139.45:8765` |
| `_test_goskin_tabs.py` | 标签/按钮冒烟 | 本地 |
| `_test_goskin_user_scene.py` | 远程加载用户场景再准备 | `192.168.139.45:8765` |

```bash
uv run --directory <repo> python dialog_monitor/_test_goskin_prestart.py
```

```python
from maxmcp.max_client import MaxClient
from dialog_monitor.goskin_flow import ensure_goskin_ready, run_goskin_skin, confirm_goskin_start

c = MaxClient(host="127.0.0.1", port=8765, transport="tcp")
ensure_goskin_ready(c)
prep = run_goskin_skin(c, mesh_names=["Box001"], bone_names=["Bone001", "Bone002", "Bone003"])
print(prep["user_prompt"])
```

远程 A 请用 `3dsmax-mcp-remote` 的 `scripts/goskin_dev_flow.py` 或 MCP `goskin_*`。

## 相关文件

- MCP 工具：`goskin_*`、`recognize_plugin_dialog`、`click_plugin_dialog_button`
- `dialog_monitor/_test_goskin_*.py` — 维护者冒烟  
- `dialog_monitor/goskin_flow.py` / `click_button.py` — B 侧实现  
- `maxscript/mcp/mcp_dialog_monitor.ms` — C 侧  
- `maxmcp/tools/goskin.py` — MCP 封装（B）  
- `dialog_monitor/README.md` · `README.zh-CN.md`
