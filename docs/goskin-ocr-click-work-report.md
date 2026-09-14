# GoSkin OCR 技能工作报告

**技能：** `skills/3dsmax-mcp-dev/goskin-ocr-click.md`  
**日期：** 2026-09-14  
**范围：** 自动蒙皮 / GoSkinning Qt 对话框的 OCR 模拟点击自动化（Agent 操作规范 + MCP 工具链）

---

## 1. 背景与目标

自动蒙皮插件（`自动蒙皮4.*` / 天晴数码）为 Qt UI，**没有可靠子 HWND**，无法用常规 Win32 控件遍历点击。技能目标是让 Agent 通过：

**截图 → 外部 OCR → 屏幕坐标模拟点击**

在受控、可校验、可人工门禁的前提下完成「全局蒙皮」准备与启动。

| 目标 | 说明 |
|------|------|
| 可重复 | 固定工具顺序，禁止临时 MAXScript / 乱点 |
| 可校验 | 不信任点击 `ok:true`，用 OCR 计数与摘要确认 |
| 可中断 | 默认不点「开始蒙皮」，必须用户确认 |
| 可多机 | 先选空闲 Max；远程强制 TCP；探活串行 |

---

## 2. 本期交付摘要

1. **技能文档完善**  
   - 硬性规则、选 Max、标准工作流、失败模式、复用 `_test_goskin_*.py`  
   - 流程图：完整版 + 本报告简化版  

2. **MCP 工具链对齐**  
   - `get_unhidden_meshes_bones` / `select_by_handles`（`MCP_SceneManage`）  
   - `goskin_run_skin` / `goskin_run_auto` 支持 `mesh_handles` / `bone_handles`  
   - `list_instances` 串行探活（TCP + Native），`acquire_instance` 不探活  

3. **运行约束写清**  
   - 空闲 ≥2 必须用户选实例；禁止无 `name` 自动抢台  
   - Max 单线程：探活与业务调用全程串行  

---

## 3. 简化工作流

![GoSkin OCR 简化流程图](../images/goskin-ocr-flow-simple.png)

| 步骤 | 动作 | 成功信号 |
|------|------|----------|
| 1 | `list_instances` →（必要时用户选）→ `acquire_instance` | 绑定一台空闲在线 Max |
| 2 | `check_dialog_ocr_health` | OCR / 工作区可达 |
| 3 | `goskin_ensure_ready` | 全局蒙皮页可见 |
| 4 | `get_unhidden_meshes_bones` | handles 非空（或已知名） |
| 5 | `goskin_run_skin(..., click_start=false)` | 模型/关节 N≥1，返回确认摘要 |
| 6 | 向用户展示 `user_prompt` | 用户明确同意 |
| 7 | `goskin_confirm_start(user_confirmed=true)` | OCR 见到「完成」 |

**完整流程图：** [`images/goskin-ocr-flow.png`](../images/goskin-ocr-flow.png)  
**简化 Mermaid：** [`images/goskin-ocr-flow-simple.mmd`](../images/goskin-ocr-flow-simple.mmd)

```mermaid
flowchart LR
  A[选 Max] --> B[OCR 检查]
  B --> C[打开 UI]
  C --> D[收集 handles]
  D --> E[准备列表]
  E --> F{用户确认?}
  F -->|否| X([停止])
  F -->|是| G[开始蒙皮]
```

---

## 4. 硬性规则（摘要）

1. 先选 Max，再 OCR / 场景操作；多空闲必须用户选。  
2. 优先 `goskin_*`，少用原始 `click_plugin_dialog_button`。  
3. **用户确认前绝不点「开始蒙皮」。**  
4. **先聚焦「(选中后在编辑区添加)」再点「选定」。**  
5. 用 OCR / 计数校验，不单信点击成功。  
6. 场景选择为空禁止「选定」。  
7. 远程桌面必须解锁（锁屏会出现空成功点击）。

`run_goskin_skin` 内部固定顺序：关警告 → 清列表 → 聚焦槽位 → 选定模型并校验 → 选定骨骼并校验 → **返回摘要并停止**。

---

## 5. 模块与依赖

| 层级 | 路径 / 工具 |
|------|-------------|
| 技能 | `skills/3dsmax-mcp-dev/goskin-ocr-click.md` |
| MCP 封装 | `maxmcp/tools/goskin.py` |
| 编排 | `dialog_monitor/goskin_flow.py` |
| 点击原语 | `dialog_monitor/click_button.py` |
| Max 侧 | `maxscript/mcp/mcp_dialog_monitor.ms` |
| 场景辅助 | `MCP_SceneManage`（unhidden / selectByHandles） |
| 配置 | `max_instances.ini` → `[ocr]` / `[workspace]` |
| 冒烟 | `dialog_monitor/_test_goskin_*.py` |

---

## 6. 风险与已知限制

| 风险 | 影响 | 缓解 |
|------|------|------|
| 锁屏 / RDP 断连 | 点击假成功 | 技能要求解锁桌面 |
| 跳过列表槽位聚焦 | 警告盖住 UI，OCR 失败 | 硬性规则 + 流程实现 |
| OCR 服务不可达 | 全流程阻断 | health 检查优先 |
| 选错 Max 实例 | 点到他人会话 | 多空闲强制用户选择 |
| 并发探活 / 命令 | Max 单线程异常 | `PROBE_SERIAL` + Agent 串行规范 |
| 坐标映射偏差 | `mesh_not_added` | client-rect 映射；复用已有测试脚本 |

---

## 7. 验收建议

- [ ] 本机 / 远程各跑一次 `_test_goskin_prestart.py`（`click_start=False`）  
- [ ] 多空闲实例时 Agent 停下来询问，不自动 `acquire`  
- [ ] `goskin_run_skin` 返回 `awaiting_start_confirm=true` 且未点「开始蒙皮」  
- [ ] 用户确认后 `goskin_confirm_start(true)` 能等到 OCR「完成」  
- [ ] 锁屏场景人工验证：点击不可静默“成功”

---

## 8. 结论

`goskin-ocr-click` 已把 **选实例 → OCR → 准备 → 人工门禁 → 启动** 固化为可执行技能与 MCP 工具链。简化流程图适合汇报与入门；完整流程图（含 `run_skin` 六步与失败模式）适合 Agent 执行对照。后续重点是回归冒烟覆盖与远程桌面稳定性，而不是再发明临时点击脚本。
