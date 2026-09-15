# 3dsmax-mcp

通过 [Model Context Protocol](https://modelcontextprotocol.io) 把 AI 智能体接入 **Autodesk 3ds Max**。

用中文描述你要做的事，智能体通过专用 MCP 工具直接操作场景——创建物体、构建材质、驱动修改器与控制器、
截取视口、检查插件。progressive 配置只公开三个发现/调用工具，再按需加载完整工具定义，避免一次性占用大量上下文。

**当前版本：1.6.7** — 见 [CHANGELOG.md](docs/CHANGELOG.md)。

> English: [README.md](README.md)

## 特点

- **原生桥接（Native Bridge）** — C++ 插件，支持 3ds Max 2023–2027，无需 MAXScript 轮询；低版本走 TCP
- **运行时自省** — 可发现任意 Max 类、插件接口与参数，方便自动化与二次开发
- **灯光工具链** — 渲染器无关的灯光发现、创建、检查与受控编辑，支持各渲染器专属发光体、输出单位与环境绑定（1.6.7 新增）
- **插件自省 v2** — 精确身份、有界查询、声明式枚举与状态令牌，配合原子类型化 `plugin_patch` 修改插件（1.6.7 新增）
- **多人共享与多实例** — 一台共享服务器对接多台 3ds Max；公共 Agent 通过有界 FIFO 短租约队列获取实例，空闲 TTL / 断线自动释放（队列满硬背压）
- **深度插件支持** — tyFlow、Data Channel、MCG、OSL、Forest Pack、RailClone、Octane
- **对话框 OCR 点击** — 对无 HWND 的 Qt 插件窗（如自动蒙皮 GoSkin）截图 → 外部 OCR → 按文字模拟鼠标点击
- **内置智能体技能包** — 附带 MAXScript 参考文档，便于你编写自己的工具

## 环境要求

- Windows
- [Python 3.12+](https://www.python.org/)
- Autodesk **3ds Max**（见下方版本与传输方式）
- [uv](https://docs.astral.sh/uv/)（仅源代码安装或开发时需要）

### 3ds Max 版本与传输方式

MCP 服务器与 3ds Max 之间有两种通信方式，按版本选择：

| Max 版本 | 传输方式 | 说明 |
|----------|----------|------|
| **2015 等较低版本**（无原生桥接） | **TCP（必需）** | 在 Max 中运行 `maxscript/mcp/mcp_server.ms`（或菜单 **MCP Start**），监听 TCP 端口（默认自 8765 起自动分配）。高版本以外的环境只能走这条路径。 |
| **2023–2027** | **Native Bridge（推荐）**，可选 TCP | 安装后自动加载 C++ 原生桥接（命名管道），延迟更低、无需 MAXScript 轮询。需要跨机、排查或兼容旧流程时，仍可额外启用 TCP 作为备选。 |

要点：

- **低版本（如 2015）**：只支持 TCP；启动 Max 端监听后，再连 Python MCP 服务器即可。
- **高版本（2023+）**：优先用原生桥接；TCP 为可选备选，不必与 Native 二选一长期独占。
- 远程 / 跨机场景：命名管道仅本机可用，远程 Max 一律通过 **TCP** 对接（`max_instances.ini` 写 `host:port`）。

---

## 安装

### 1. 从 PyPI 安装（推荐，国内加速）

无需克隆 GitHub 仓库。使用清华 TUNA 镜像安装完整软件包：

```powershell
python -m pip install 3dsmax-mcp -i https://pypi.tuna.tsinghua.edu.cn/simple
3dsmax-mcp-install
```

如果 PowerShell 找不到 `3dsmax-mcp-install`，可直接运行：

```powershell
python -m maxmcp.installer
```

安装程序会让你选择 MCP 工具配置，默认使用兼容性最好的 `full`。`progressive` 额外暴露实例路由控制（`list_max_instances` 等），再公开三个发现/调用工具，按需加载精确工具参数，可明显减少本地或较小模型的上下文占用。无人值守安装可使用 `3dsmax-mcp-install --tool-profile progressive`。
**必须重启 3ds Max** 插件才会加载。

> 其他可用镜像：阿里云 `https://mirrors.aliyun.com/pypi/simple/`、腾讯云 `https://mirrors.cloud.tencent.com/pypi/simple/`。

### 2. 从源代码安装（开发者）

先通过国内镜像安装 uv：

```powershell
python -m pip install uv -i https://pypi.tuna.tsinghua.edu.cn/simple
```

然后克隆仓库并安装：

```powershell
git clone https://github.com/cl0nazepamm/3dsmax-mcp.git
cd 3dsmax-mcp
uv sync
uv run python install.py
```

依赖下载慢或超时，先设置国内镜像再执行 `uv sync`：

```powershell
$env:UV_DEFAULT_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"
uv sync
```

> 较早版本的 uv 使用 `UV_INDEX_URL` 环境变量，若上面这个不生效请改用它。

### 更新

PyPI 安装：

```powershell
python -m pip install --upgrade 3dsmax-mcp -i https://pypi.tuna.tsinghua.edu.cn/simple
3dsmax-mcp-install
```

源代码安装：

```powershell
git pull
uv sync
uv run python install.py
```

---

## 启动服务器（启动器与传输模式）

仓库自带两个启动器，分别对应一种 MCP 传输方式：

| 启动器 | 传输方式 | 适用场景 |
|--------|----------|----------|
| `start_python_server.bat` | `streamable-http` | 以 HTTP 绑定 `0.0.0.0:8000` 启动，局域网内的 MCP 客户端可直接连接。窗口会打印本机局域网 IP，例如 `http://192.168.x.x:8000/mcp`。 |
| `start_python_server_stdio.bat` | `stdio` | 以 stdin/stdout 独立运行服务器。Claude Desktop、Cursor 等客户端通常自行拉起该模式；此启动器主要用于测试。 |

两者都需要先执行一次 `install_deps.bat` 安装依赖，启动后需保持窗口不关闭。

传输方式由环境变量 `MCP_TRANSPORT` 决定：`stdio`（默认）或 `streamable-http`。HTTP 模式下，`MCP_HTTP_HOST`（默认 `0.0.0.0`）和 `MCP_HTTP_PORT`（默认 `8000`）控制绑定地址与端口。

把 MCP 客户端指向 HTTP 端点 `http://<ip>:8000/mcp`（把 `<ip>` 换成窗口打印的局域网 IP）：

```json
{
  "mcpServers": {
    "3dsmax-mcp": {
      "url": "http://192.168.x.x:8000/mcp"
    }
  }
}
```

每个 MCP 进程会保持绑定到它首先连接的那个 Max 实例。在任何配置下都可以用 `list_max_instances`、
`select_max_instance(pid)`、`get_selected_max_instance`、`release_max_instance` 管理路由；
`MCP_MAX_PID` 和已有的 `MCP_MAX_PIPE` 支持启动时固定目标。启动或占用另一个 Max 只会改变
未绑定客户端的默认目标。

---

## 多人共享与多实例使用

默认情况下，MCP 服务器与 3ds Max 都在本机运行（单用户）。也可以架设一台"共享服务器"，让局域网内的多人同时使用同一台或多台 3ds Max。

### 架构

- 一个 Python MCP 服务器进程，监听 `0.0.0.0:8000`（streamable-http 传输，见上文"启动服务器"）
- 启动一个或多个 3ds Max 实例，每个实例独立运行 `maxscript/mcp_server.ms`，各自监听独立 TCP 端口
- 每个用户通过自己的 MCP 客户端连接共享服务器，建立独立会话，互不干扰
- 每个 3ds Max 实例**同一时刻只允许一个用户独占**使用；用户操作完成后必须显式释放，实例才会恢复空闲

### 1. 启动 3ds Max 端（端口自动分配）

在每个 3ds Max 中直接运行脚本即可，**无需任何配置**：

- 第一个实例自动占用 8765，第二个自动占用 8766，依此类推（从 8765 起扫描第一个空闲端口）
- 实例启动后把自己的端口（含 30 秒心跳）写入注册文件 `%LOCALAPPDATA%\3dsmax-mcp\instances.jsonl`，供 Python 端自动发现
- 也可以显式指定端口：启动前设置环境变量 `MAXMCP_PORT=19001`（此时不做自动扫描）

### 2. 启动 Python 服务端（共享服务器）

直接双击 `start_python_server.bat` 即可（HTTP 绑定 `0.0.0.0:8000`，窗口会打印本机局域网 IP）。

**无需设置 `MAXMCP_INSTANCES`** —— 服务器启动时会自动发现注册文件里所有存活的 3ds Max 实例，并持续刷新（新启动的实例自动加入，关闭的实例自动移除并释放其锁）。

跨机（Python 与 3ds Max 不在同一台电脑）时，本机注册表发现不到远程 Max，请用配置文件或环境变量显式指定：

**推荐：编辑项目根目录的 `max_instances.ini`**（可从 `max_instances.ini.example` 复制）：

```ini
[instances]
max1 = 192.168.139.45:8765

# 跨机截图 / OCR 点击时建议配置双方均可读写的共享目录
[workspace]
path = K:\共享\3dsmax-mcp\workspace

# 外部 OCR 服务（对话框文字识别）
[ocr]
base = http://192.168.139.130:8000
```

查找顺序：`MAXMCP_INSTANCES_FILE` → 当前目录 / 项目根 `max_instances.ini` → `%LOCALAPPDATA%\3dsmax-mcp\max_instances.ini`。

OCR 基址优先级：`MAXMCP_OCR_BASE` 环境变量 > `[ocr] base=` > 代码默认 `http://192.168.139.130:8000`。  
接口约定：`GET {base}/v1/ocr/health`、`POST {base}/v1/ocr`。可用 `check_dialog_ocr_health` 探测。



### 3. 其他人如何连接（客户端配置）

把 MCP 客户端的 URL 指向共享服务器的 IP（streamable-http 端点 `/mcp`），例如 `http://192.168.1.100:8000/mcp`。客户端配置的写法见上文"启动服务器"段的 JSON 示例；命令行代理可用：

```bash
claude mcp add --scope user 3dsmax-mcp --url http://192.168.1.100:8000/mcp
```

注意：

- 将 `192.168.1.100` 替换为共享服务器的实际 IP
- 服务器防火墙需放行 8000 端口

### 4. 使用流程（实例生命周期）

每个用户遵循"**获取 → 使用 → 释放**"三步：

1. `list_instances` —— 查看有哪些实例、哪些空闲，以及等待队列深度
2. `acquire_instance` —— 申请短租约（可指定名字；不指定则自动分配）。无空闲时在有界 FIFO 队列中等待（默认最多 60 秒）；队列满返回 `QUEUE_FULL`
3. 正常调用场景工具（命令自动路由到该实例；工具活动会续期空闲计时）
4. `release_instance` —— 任务完成立即释放，唤醒排队中的其他 Agent

规则与提示：

- 一个实例同时只允许一个用户；公共多 Agent 场景下请勿在「思考」时长时间占着 Max
- 新租约默认重置场景（`MAXMCP_RESET_ON_ACQUIRE=true`），避免租户间场景串台
- 未获取实例就调用场景工具，会提示先调用 `acquire_instance`
- 空闲超过 `MAXMCP_LOCK_TTL`（默认 **180 秒**）无工具活动会自动释放；会话断开也会立刻释放并取消排队
- 相关环境变量：`MAXMCP_ACQUIRE_WAIT_SECONDS`（默认 60）、`MAXMCP_ACQUIRE_QUEUE_MAX`（默认 32）
- 3ds Max 关闭后，其注册条目 90 秒内未收到心跳即视为离线，相关锁自动释放

---

## 配置 AI 客户端

国内用户最常见的组合是 **Cline + DeepSeek**（VS Code 插件），下面以它为主。

### Cline + DeepSeek（推荐）

1. 在 VS Code 中安装 **Cline** 扩展
2. 在 Cline 设置里选择 API Provider 为 **DeepSeek**，填入 [DeepSeek 开放平台](https://platform.deepseek.com/)
   的 API Key，模型选择支持函数调用的对话模型（如 `deepseek-chat`）
3. 打开 Cline 的 **MCP Servers → Configure MCP Servers**，编辑配置文件：

```
%APPDATA%\Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json
```

如果使用上面的 PyPI 安装，先查询 Python 的绝对路径：

```powershell
python -c "import sys; print(sys.executable)"
```

填入（把 `command` 换成上一步返回的实际路径）：

```json
{
  "mcpServers": {
    "3dsmax-mcp": {
      "command": "C:/Users/你的用户名/AppData/Local/Programs/Python/Python312/python.exe",
      "args": ["-m", "maxmcp.server"],
      "disabled": false,
      "autoApprove": []
    }
  }
}
```

> 如果该 Python 的 `Scripts` 目录已在系统 PATH 中（pip 默认会加入），
> 也可以直接写 `"command": "3dsmax-mcp"`，与上面的写法等价（都指向 `maxmcp.server:main`）。

如果使用源代码安装，也可以继续使用：

```json
{
  "mcpServers": {
    "3dsmax-mcp": {
      "command": "uv",
      "args": ["run", "--directory", "C:/path/to/3dsmax-mcp", "3dsmax-mcp"],
      "disabled": false,
      "autoApprove": []
    }
  }
}
```

保存后 Cline 会自动重连。3ds Max 处于打开状态时，让智能体调用 `get_bridge_status`，
返回正常即表示打通。

> **推理模型注意**：`deepseek-reasoner` 一类纯推理模型对工具调用的支持与对话模型不同，
> 接 MCP 建议优先使用对话模型。

### 通义千问 Qwen / 智谱 GLM

这两家都提供 OpenAI 兼容接口，在 Cline 里选择 **OpenAI Compatible** provider，填入对应
Base URL、API Key 和模型名：

| 平台 | Base URL |
|------|----------|
| 通义千问（阿里云百炼） | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4/` |

模型名请以各平台文档为准，务必选择**支持函数调用（Function Calling）**的型号，否则无法调用 MCP 工具。

### Claude Desktop / Cursor（可选）

配置文件位置：

| 客户端 | 路径 |
|--------|------|
| Claude Desktop | `%APPDATA%\Claude\claude_desktop_config.json` |
| Cursor | `%USERPROFILE%\.cursor\mcp.json` |

服务器条目与上面的 `mcpServers` 写法相同。更多手动配置见
[docs/ADVANCED.md](docs/ADVANCED.md)。

---

## 快速上手一：建筑可视化

打开 3ds Max，在 AI 客户端里直接用中文提要求。典型流程：

**1. 批量导入与场景速览**

> 把 D:\assets\furniture 里的模型批量导入，然后给我一个场景概览。

`smart_import` 批量导入并自动匹配 PBR 材质，`query_scene` 给出场景结构概览。

**2. 构建材质**

> 用 D:\textures\wood_oak 里的贴图做一套木地板材质，赋给地面。

`create_material_from_textures` 从贴图文件夹自动搭好一整套连线完整的 PBR 材质节点，
再由 `assign_material` 赋给物体——不用一个个槽位手动接。

**3. 检查材质网络**

> 检查一下这个材质的节点连接有没有问题。

`inspect_material_network` 返回语义化的材质节点图：已连接的槽位、贴图清单、以及常见错误的
健康检查。材质"看起来不对"但不知道问题在哪时，先用它。

**4. 复用与批量替换**

> 把这个材质的结构复制到墙面物体上，贴图换成 concrete 那一套。
> 然后把场景里所有旧材质统一替换成新做的这个。

`replicate_material` 做保结构的材质克隆并重映射贴图路径，`batch_replace_materials`
批量替换所有引用——比在材质编辑器里逐个改快得多。

**5. 预览与渲染**

> 截个视口图看看，没问题的话渲染一张 1920×1080 的图。

`capture_viewport` 先出快速预览（智能体能"看到"结果并据此调整），确认后 `render_scene` 正式渲染。

> **渲染器说明**：国内建筑可视化多用 V-Ray / Corona。当前对 Octane 的材质连线支持最完整，
> V-Ray / Corona 的深度支持正在推进中——如果你在用，欢迎提 issue 告诉我们你的具体需求。

---

## 快速上手二：MMD / 动画

**1. 导入模型并理清结构**

> 把 D:\models\ 里的模型批量导入，然后告诉我场景里的骨骼层级是什么样的。

`smart_import` 批量导入并自动匹配 PBR 材质，`get_hierarchy` 输出父子层级树。

**2. 检查朝向与轴心**

> 检查一下头部骨骼的轴心和坐标轴朝向对不对。

`analyze_node_orientation` 返回轴心、包围盒、局部坐标轴和世界矩阵——绑定和摆放出问题时先看这个。

**3. 动画与控制器**

> 给这个骨骼加一个注视约束，目标是摄像机。
> 把第 0 帧到第 60 帧的循环接顺，首尾姿势对齐。

`assign_controller` + `add_controller_target` 建立约束，`keyframe_tracks` 处理关键帧、
姿势匹配、循环闭合与切线设置。

**4. 预览**

> 截个视口图看看动作效果。

`capture_viewport` 出图，智能体可以据此判断并继续调整。

---

## 对话框 OCR 

部分 Qt 插件对话框（如「自动蒙皮 / GoSkinning」）没有可用的子控件 HWND，无法用常规 MaxScript UI 访问。
本仓库的 `dialog_monitor` 模块走：**找窗 → 截图 → 外部 OCR → 按文字坐标模拟鼠标点击**。

更细的工具表与限制见 [dialog_monitor/README.md](dialog_monitor/README.md)。

### 点击原理

在 3ds Max 进程内通过 .NET 调用 `user32.dll`：

1. `SetForegroundWindow` — 将目标对话框置前  
2. `SetCursorPos` — 移动到屏幕物理坐标  
3. `mouse_event(LEFTDOWN / LEFTUP)` — 模拟左键单击  

坐标由 OCR 文字框映射到客户区屏幕坐标（`image_to_screen`）。这是**系统输入桌面注入**，不是给控件发 `WM_LBUTTON*`。

| 远程桌面状态 | 截图 / OCR | 模拟点击 |
|--------------|------------|----------|
| 已解锁（关显示器也可） | 正常 | 正常 |
| **锁屏 / 断开 RDP** | 常仍可读界面 | **空成功**（回报 ok，UI 不变） |

因此 GoSkin 自动化要求远程主机保持**解锁的交互桌面**。

### Auto GoSkin 固化顺序（勿打乱）

1. 打开/定位对话框，切到「蒙皮 / 全局蒙皮」  
2. 关掉上次误操作留下的警告窗（否则会挡住主界面）  
3. 列表有残留时点「清空」  
4. **先点**「(选中后在编辑区添加)」（或已有 `模型：N`）——不点就点「选定」会弹警告  
5. 场景选中模型 →「选定」→ OCR 校验 `模型：N≥1`  
6. 再聚焦列表行 → 场景选中骨骼 →「选定」→ 校验 `关节：N≥1`  
7. **暂停**：返回模型/关节名称与数量摘要，**默认不点「开始蒙皮」**  
8. 用户确认后调用 `goskin_confirm_start(user_confirmed=true)` 才点击并等待 OCR「完成」

### 相关 MCP 工具

| 工具 | 作用 |
|------|------|
| `goskin_ensure_ready` | 打开/定位 GoSkin，切到「蒙皮 / 全局蒙皮」 |
| `goskin_cleanup_lists` | 清空模型/关节编辑区残留 |
| `goskin_run_skin` | 准备列表后暂停，返回确认摘要（默认 `click_start=false`） |
| `goskin_confirm_start` | 仅当 `user_confirmed=true` 时点击「开始蒙皮」 |
| `goskin_run_auto` | ensure + run；默认同样在开始前暂停 |
| `check_dialog_ocr_health` / `recognize_plugin_dialog` / `click_plugin_dialog_button` | 通用对话框 OCR 与点击 |

OCR 服务基址配置（优先级从高到低）：

1. 工具参数 `ocr_base`  
2. 环境变量 `MAXMCP_OCR_BASE`  
3. `max_instances.ini` 的 `[ocr] base=`  
4. 代码默认 `http://192.168.139.130:8000`

接口：`GET {base}/v1/ocr/health`、`POST {base}/v1/ocr`。跨机请同时配置 `[workspace]`。Progressive 工具集名：`dialog_ui`。

---

## 工具配置（Tool Profile）

安装程序默认选择 **full**，让现有 MCP 客户端直接看到全部工具。对于上下文有限的本地或较小模型，
可选择 **progressive**：只公开 `list_toolsets`、`describe_toolset`、`call_tool` 三个元工具，再按需加载精确工具参数。

```powershell
$env:MCP_TOOL_PROFILE = "progressive"
```

| 配置 | 包含范围 |
|------|----------|
| **progressive（节省上下文）** | 三个发现/调用元工具；按需加载完整操作工具与参数，适合本地或较小模型 |
| **core** | 场景、物体、材质、修改器、控制器、视口、文件、插件、组织管理、学习、**对话框 OCR / GoSkin** |
| **full（安装默认）** | core 全部，外加 tyFlow、MCG、Forest Pack、RailClone、Data Channel、特效、状态集、参数关联、**渲染**、户型平面、Max 内置聊天 |

progressive 模式下先列出并描述对应工具组，再通过 `call_tool` 调用所需工具。`tools/list` 始终保持三个条目；
core/full 仍可用于需要一次性公开全部参数的旧客户端。

---

## 常见问题

**智能体说连不上 / 工具报错**
让它调用 `get_bridge_status`。先确认 3ds Max 正在运行、且安装后已经**重启过**。

**支持哪些 Max 版本**
- **2023–2027**：原生桥接（推荐）+ 可选 TCP。原生插件按版本单独编译，安装脚本会自动匹配已安装的版本。
- **更低版本（如 2015）**：无原生桥接，使用 **TCP** 传输（运行 `mcp_server.ms` / **MCP Start**）。详见上文「3ds Max 版本与传输方式」。

**安全模式**
安全模式默认开启，`execute_maxscript` 等通道受其限制，用于阻止代理执行危险命令。以下命令会被拦截：

| 被阻止 | 说明 |
|--------|------|
| `DOSCommand` / `hiddenDOSCommand` | shell / cmd 执行 |
| `ShellLaunch` | 启动外部应用程序 |
| `deleteFile` | 从磁盘删除文件 |
| `python.Execute` | 在 3ds Max 内执行 Python |
| `createFile` | 将新文件写入磁盘 |

允许：所有场景操作（创建、修改、删除对象、材质、修改器）、`openFile` / `readLine` 读取文件、
`getDir` / `getFiles` 列出目录与文件、`render` 渲染场景、`saveMaxFile` 保存 `.max` 文件、
`gw.getViewportDib()` 视口捕获、`fileIn` 加载 MAXScript 文件。

如需禁用，在配置文件 `%LOCALAPPDATA%\3dsmax-mcp\mcp_config.ini` 中设置 `safe_mode=false`。
详见 [docs/ADVANCED.md](docs/ADVANCED.md)。

**能自己加工具吗**
可以。安装脚本会生成一个智能体技能包，内含 MAXScript 参考资料，专门用来指导 AI 写新工具。

**自动蒙皮 / 对话框点击为什么“点了没反应”**
常见原因：① 远程主机**锁屏或断开 RDP**（TCP/截图可能仍正常，但 `mouse_event` 空成功）；② 未先点「(选中后在编辑区添加)」就点了「选定」，弹出警告窗挡住界面。请保持桌面解锁，并按上文 GoSkin 固化顺序操作。详见 [dialog_monitor/README.md](dialog_monitor/README.md)。

---

## 反馈

**提 issue 用中文完全可以**，不必勉强写英文——中文 issue 一样会被认真处理。

- 问题反馈：https://github.com/cl0nazepamm/3dsmax-mcp/issues
- 更新日志：[docs/CHANGELOG.md](docs/CHANGELOG.md)
- 进阶配置：[docs/ADVANCED.md](docs/ADVANCED.md)

如果这个工具对你有用，欢迎在 GitHub 点个 Star，也欢迎录制视频、写文章分享——
让更多中文用户看到。

## 许可证

[MIT](LICENSE)
