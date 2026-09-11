# dialog_monitor

独立模块：发现 3ds Max 插件对话框 / 菜单 → 截图 → 外部 OCR → 按文字点击。  
已注册到 MCP（`maxmcp/tools/dialog_monitor.py`、`maxmcp/tools/goskin.py`）。

## 共享 workspace（跨机必配）

在 `max_instances.ini`：

```ini
[workspace]
path = \\fileserver\share\3dsmax-mcp\workspace
```

或环境变量 `MAXMCP_WORKSPACE`（优先级更高）。

| 状态 | 行为 |
|------|------|
| 已配置 | 截图写到该路径；Python/OCR 直接读同一路径（Max 与 MCP 须都能读写） |
| **未配置** | **不存在共享 workspace**；截图落在 Max 本机 temp，仅同机可读 |

本地 Max：截图在本机 temp，Python 可直读。  
远程 Max：优先 HTTP 把 PNG 推到 Python 主机（不经桥接传大 base64）；CJK 共享盘路径请避免给 Max `save bmp`。

`get_file_service_info` / `check_dialog_ocr_health` 会返回 `shared_configured`。

## MCP tools

| Tool | 作用 |
|------|------|
| `check_dialog_ocr_health` | OCR 健康检查 + workspace 状态 |
| `recognize_plugin_dialog` | 找对话框并 OCR（不点击） |
| `click_plugin_dialog_button` | 对话框按钮 OCR 点击 |
| `click_plugin_menu_path` | 菜单栏 → 弹出项 两步点击 |
| `goskin_ensure_ready` | 打开/定位 GoSkin，切到「蒙皮 / 全局蒙皮」 |
| `goskin_cleanup_lists` | 清空模型/关节编辑区残留 |
| `goskin_run_skin` | 清理→选定模型/关节后**暂停**，返回确认摘要（默认不点开始蒙皮） |
| `goskin_confirm_start` | 用户确认后才点「开始蒙皮」并等待「完成」（`user_confirmed=true`） |
| `goskin_run_auto` | ensure + run；默认同样在开始前暂停 |

OCR 基址：`MAXMCP_OCR_BASE`，默认 `http://192.168.139.130:8000`。  
Progressive 工具集：`dialog_ui`。

## Auto GoSkin 流程

Python 编排：`dialog_monitor/goskin_flow.py`。

固化顺序（勿打乱）：

1. **ensure**：探测/打开窗口 →「蒙皮」→「全局蒙皮」
2. **cleanup**（若脏）：出现 `模型：N`、或「在场景中选择模型/关节」下方有名称时，点两行「清空」
3. **mesh**：Max 选中模型 →「选定」→ OCR 校验 `模型：N>=1`
4. **bones**：聚焦列表 → Max 选中骨骼 →「选定」→ 校验 `关节：N>=1`
5. **确认**：返回模型名/数量、关节名/数量摘要；**默认不点「开始蒙皮」**
6. **start**：用户确认后调用 `goskin_confirm_start(user_confirmed=true)` 才点击并等待「完成」

空选择时禁止点「选定」。MCP 另有 `goskin_cleanup_lists`。

```python
from maxmcp.max_client import MaxClient
from dialog_monitor.goskin_flow import ensure_goskin_ready, run_goskin_skin, confirm_goskin_start

c = MaxClient(host="127.0.0.1", port=8765, transport="tcp")
ensure_goskin_ready(c)
prep = run_goskin_skin(
    c,
    mesh_names=["Box001"],
    bone_names=["Bone001", "Bone002", "Bone003"],
)  # click_start 默认 False
print(prep["user_prompt"])  # 展示给用户确认
# 用户同意后：
# confirm_goskin_start(c, user_confirmed=True)
```

### 限制

- **需要交互桌面未锁屏**。锁屏 / 断开 RDP 时截图与点击常会空成功；流程靠二次 OCR 校验，失败时返回 `tab_not_ready` 等错误。
- Qt 插件子控件通常无独立 HWND；本模块一律顶层窗截图 + OCR 坐标点击。
- 「选定」依赖场景中已有选择（或传入名称由流程 `select_objects`）。

## CLI

```bash
python -m dialog_monitor --health
python -m dialog_monitor --menu NDBox --item "天晴盒子"
python dialog_monitor/_test_goskin_ensure.py
```
