# 3dsmax-MCP



MCP 服务器通过 TCP 套接字将 Claude 和其他代理桥接到 Autodesk 3ds Max。

## 先决条件

- [Python 3.10+](https://www.python.org/)
- [紫外线](https://docs.astral.sh/uv/) （Python 包管理器）
- Autodesk 3ds Max 2025+（仅测试了 2026！）

## 您可以尝试的想法

- 直接编写MaxScript/Python。克劳德将阅读和调试代码、修复问题并保持代理循环运行直到成功。
- 编写 OSL 着色器
- 读取和操作场景数据。
- 组织对象。
- 设置项目文件夹并组织它们。
- 获取有关渲染的反馈。 （克劳德可以看到3dsmax窗口外面）
- 将从错误中学习并将其保存在 SKILL.md 中
- 包含基本 3dsmax 技能文件。欢迎贡献。
- 您还可以使用 AI 重命名对象。（仅适用于 Claude Code）。要求克劳德使用俳句重命名对象。 Claude 将运行 haiku 子代理并分析场景中选定的对象。请注意，这会疯狂地燃烧代币。只有当你很有钱时才这样做。
- 尝试使用 Forest Pack 和 tyFlow 等插件。
- 在渲染器之间转换场景

## 设置

### 1. 克隆仓库

```bash
git clone https://github.com/cl0nazepamm/3dsmax-mcp.git
cd 3dsmax-mcp
```

### 2.安装依赖

```bash
uv sync
```

### 3. 建立并注册技能文件

```bash
python scripts/build_skill.py
python scripts/register.py
```
这会将开发技能复制到 `.claude/skills/` 并将技能文件注册到 .claude json。

#### 所有代理的全局技能（可选）

我建议为技能文件创建一个符号链接，这样 Claude、Codex 和 Gemini 都可以获取它。为此，请使用命令提示符而不是 powershell。 

如果您没有代理技能，请先安装它。通过powershell  `npm install -g @govcraft/agent-skills`

然后

```bash
mklink /D "%USERPROFILE%\.agents\skills\3dsmax-mcp-dev" "C:\path\to\3dsmax-mcp\skills\3dsmax-mcp-dev"
mklink /D "%USERPROFILE%\.claude\skills\3dsmax-mcp-dev" "C:\path\to\3dsmax-mcp\skills\3dsmax-mcp-dev"
```

代替 `C:\path\to\3dsmax-mcp` 与您克隆存储库的实际路径。这样，即使您在该项目之外工作，编码代理也可以加载 3ds Max 技能。需要管理员权限。如果您没有代理技能，您可以将其安装到 `.codex/skills` 或者 `.gemini/skills` 等等。克劳德可能会要求你创建符号链接 `.claude/skills`

### 4. 设置 3ds Max（MAXScript 监听器）

将 MAXScript 文件复制到 3ds Max 安装中：

1. 复制 `maxscript/mcp_server.ms` 到：
   ```
   [3ds Max Install Dir]/scripts/mcp/mcp_server.ms
   ```

2. 复制 `maxscript/startup/mcp_autostart.ms` 到：
   ```
   [3ds Max Install Dir]/scripts/startup/mcp_autostart.ms
   ```

3. 重新启动 3ds Max。你应该看到 `MCP: Auto-start complete` 在 MAXScript 监听器中。

### 5. 为代理设置MCP。

在 powershell 中 

```bash
claude mcp add --scope user 3dsmax-mcp -- uv run --directory "C:\path\to\3dsmax-mcp" 3dsmax-mcp
codex mcp add 3dsmax-mcp -- uv run --directory "C:\path\to\3dsmax-mcp 3dsmax-mcp" 3dsmax-mcp
gemini mcp add --scope user 3dsmax-mcp -- uv run --directory "C:\path\to\3dsmax-mcp" 3dsmax-mcp

```

#### 克劳德桌面应用程序

编辑 `%APPDATA%\Claude\claude_desktop_config.json`

```bash
{
  "mcpServers": {
    "3dsmax-mcp": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "C:\\path\\to\\3dsmax-mcp",
        "3dsmax-mcp"
      ]
    }
  }
}
```
代替 `C:\\path\\to\\3dsmax-mcp` 与您克隆存储库的实际路径。编辑后重新启动 Claude Desktop 应用程序。

#### 为克劳德应用程序添加技能
打开 Claude 应用程序，转到“设置”>“功能”部分并上传 .MD

## 如何更新

在 powershell 中
```
git pull
python scripts/build_skill.py

```


## 它是如何运作的

1. MAXScript 侦听器在 3ds Max 内部的 TCP 端口 8765 上运行
2. MCP 服务器 (Python) 通过 TCP 套接字发送 MAXScript 命令
3. 3ds Max 执行命令并返回 JSON 响应
4. Claude 通过 MCP 服务器发送命令并返回结果

## 多人共享与多实例使用

默认情况下，MCP 服务器与 3ds Max 都在本机运行（单用户）。也可以架设一台"共享服务器"，让局域网内的多人同时使用同一台或多台 3ds Max。

### 架构

- 一个 Python MCP 服务器进程，监听 `0.0.0.0:8000`（streamable-http 传输）
- 启动一个或多个 3ds Max实例，每个独立运行的3dsMax `maxscript/mcp_server.ms`，各自监听独立 TCP 端口
- 每个用户通过自己的 MCP 客户端（Claude Desktop / Codex / Gemini 等）连接共享服务器
- 每个 3ds Max 实例**同一时刻只允许一个用户独占**使用；用户操作完成后必须显式释放，实例才会恢复空闲状态

### 1. 启动 3ds Max 端（端口自动分配）

在每个 3ds Max 中直接运行脚本即可，**无需任何配置**：

- 第一个实例自动占用 8765，第二个自动占用 8766，依此类推（从 8765 起扫描第一个空闲端口）
- 实例启动后把自己的端口（含 30 秒心跳）写入注册文件 `%LOCALAPPDATA%\3dsmax-mcp\instances.jsonl`，供 Python 端自动发现
- 也可以显式指定端口：启动前设置环境变量 `MAXMCP_PORT=19001`（此时不做自动扫描）

### 2. 启动 Python 服务端（共享服务器）

```bash
uv run --directory "C:\path\to\3dsmax-mcp" 3dsmax-mcp
```

**无需设置 `MAXMCP_INSTANCES`** —— 服务器启动时会自动发现注册文件里所有存活的 3ds Max 实例，并持续刷新（新启动的实例自动加入，关闭的实例自动移除并释放其锁）。

如需手动指定（可选的旧方式）：

```bash
set MAXMCP_INSTANCES=127.0.0.1:8765:maxA,127.0.0.1:8766:maxB
```

### 3. 其他人如何连接（客户端配置）

把 MCP 客户端的 URL 指向共享服务器的 IP（streamable-http 端点 `/mcp`），例如：

Claude Desktop（`%APPDATA%\Claude\claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "3dsmax-mcp": {
      "url": "http://192.168.1.100:8000/mcp"
    }
  }
}
```

命令行代理：

```bash
claude mcp add --scope user 3dsmax-mcp --url http://192.168.1.100:8000/mcp
```

注意：
- 将 `192.168.1.100` 替换为共享服务器的实际 IP
- 服务器防火墙需放行 8000 端口
- 各用户的 MCP 客户端会分别建立独立会话，互不干扰

### 4. 使用流程（实例生命周期）

每个用户遵循"**获取 → 使用 → 释放**"三步：

1. `list_instances` —— 查看有哪些实例、哪些空闲
2. `acquire_instance` —— 独占一个空闲实例（可指定名字；不指定则自动分配）
3. 正常调用场景工具（命令自动路由到该实例，其他用户无法同时使用它）
4. `release_instance` —— 任务完成，明确释放实例，使其恢复空闲供他人使用

规则与提示：
- 一个实例同时只允许一个用户；其他人获取同一实例会收到 `busy` 错误，可改选空闲实例或等待
- 未获取实例就调用场景工具，会提示先调用 `acquire_instance`
- 会话意外断开时，遗留的锁会在 `MAXMCP_LOCK_TTL`（默认 1800 秒）后自动清理
- 3ds Max 关闭后，其注册条目 90 秒内未收到心跳即视为离线，相关锁自动释放

# 安全模式注意事项

默认情况下，安全模式 (safeExecute) 处于开启状态。这是一项安全功能，因此代理无法运行恶意命令。
 
 被阻止
  - DOSCommand — shell/cmd 执行
  - ShellLaunch — 启动外部应用程序
  - deleteFile — 从磁盘删除文件
  - python.Execute — 在 3ds Max 内执行 Python
  - createFile — 将新文件写入磁盘
 

  允许：
  - 所有场景操作（创建、修改、删除对象、材质、修改器）
  - openFile / readLine — 读取文件
  - getDir / getFiles — 列出目录和文件
  - render — 渲染场景
  - saveMaxFile — 保存 .max 文件
  - gw.getViewportDib() — 视口捕获
  - fileIn — 加载 MAXScript 文件（但重新加载服务器只是以 safeMode = true 再次重新启动）

如果您想禁用 safeExecute，请翻转 `safeMode = true` 到 `false` 在 `mcp_server.ms`


## 当前工具列表

- `build_structure` - 按程序建造更大的建筑物（房屋、塔楼、城堡等）
- `clone_objects` - 将对象克隆为具有可选偏移量的副本/实例/引用。
- `add_data_channel` - 创建数据通道修改器图。
- `inspect_data_channel` - 读取数据通道修改器的完整操作图。
- `set_data_channel_operator` - 编辑一个数据通道操作器的参数。
- `add_dc_script_operator` - 添加基于 MAXScript 的数据通道脚本运算符。
- `list_dc_presets` - 列出可用的数据通道预设。
- `load_dc_preset` - 将数据通道预设应用于对象。
- `get_effects` - 列出场景中的大气/渲染效果。
- `toggle_effect` - 按索引启用/禁用效果。
- `delete_effect` - 按索引删除大气/渲染效果。
- `execute_maxscript` - 运行任意 MAXScript 并返回结果。
- `build_floor_plan` - 根据房间/单元定义构建平面图。
- `place_on_grid` - 将一个对象放置在网格索引处。
- `place_grid_array` - 用重复的对象填充网格体积。
- `place_circle` - 将物体均匀地放置在一个圆圈周围。
- `set_parent` - 父母/取消父母对象。
- `get_hierarchy` - 返回对象的递归子层次结构。
- `isolate_and_capture_selected` - 隔离选定的对象并捕获视口图像。
- `batch_rename_objects` - 在一项操作中重命名多个对象。
- `inspect_object` - 对一个物体进行高级深度检查。
- `inspect_properties` - 对象/基础/修改器/材质的深层属性转储。
- `inspect_modifier_properties` - 一个修改器的深度属性转储。
- `assign_material` - 创建材质并将其分配给对象。
- `set_material_property` - 设置对象材质/子材质的一项属性。
- `set_material_properties` - 一次设置多个材料属性。
- `get_material_slots` - 具有低令牌范围的运行时槽检查器（`map`/`summary`/`all`）加上位图/普通帮助器类提示。
- `create_texture_map` - 创建纹理贴图并将其存储为全局变量。
- `set_texture_map_properties` - 设置存储的纹理贴图的属性。
- `set_sub_material` - 在多材质/子材质中创建/分配子材质槽。
- `write_osl_shader` - 将 OSL 写入磁盘并从中创建 OSLMap。
- `create_material_from_textures` - 从纹理文件夹自动构建 PBR 材质。
- `get_materials` - 列出分配的材料及其对象用途。
- `add_modifier` - 向对象添加修饰符。
- `remove_modifier` - 从对象中删除修饰符。
- `set_modifier_state` - 切换修改器启用/查看/渲染状态。
- `collapse_modifier_stack` - 将修改器堆栈折叠为烘焙几何体。
- `make_modifier_unique` - 取消共享修改器的实例。
- `batch_modify` - 类的所有修饰符的场景范围属性编辑。
- `get_object_properties` - 详细的对象属性（变换/材质/修改器）。
- `set_object_property` - 通过 MAXScript 表达式设置一个对象属性。
- `create_object` - 在场景中创建一个基元/对象。
- `delete_objects` - 按名称删除对象。
- `render_scene` - 运行实际渲染（可以选择保存文件）。
- `manage_scene` - 场景状态操作（保持/获取/重置/保存/信息）。
- `find_class_instances` - 在场景范围内查找类实例（`getclassinstances` 风格）。
- `get_instances` - 获取共享相同基础对象的所有对象实例。
- `get_dependencies` - 跟踪对象的依赖关系图。
- `find_objects_by_property` - 通过属性/值匹配查找对象。
- `get_scene_info` - 列出带有过滤器的场景对象。
- `get_selection` - 返回当前选择信息。
- `select_objects` - 按名称/模式/类/全部选择对象。
- `get_state_sets` - 使用相机/范围元数据读取状态集。
- `get_camera_sequence` - 访问摄像机序列器。
- `transform_object` - 移动/旋转/缩放对象。
- `capture_viewport` - 快速活动视口屏幕截图（安全默认）。
- `capture_screen` - 全屏捕获，默认禁用；需要 `enabled=true`.
- `scatter_forest_pack` - 从命名表面和源对象构建森林包分散对象。
- `set_visibility` - 隐藏/显示/切换/冻结/解冻对象。
