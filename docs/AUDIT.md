# 审计日志（重要 MCP 操作）

重要 / 破坏性工具调用会以 JSON Lines 追加写入：

```text
%TEMP%/3dsmax-mcp/audit/YYYYMMDD.jsonl
```

环境变量 `MAXMCP_AUDIT=0` 可关闭；默认开启。

目录信息可通过 `get_file_service_info` → `audit` 查询。在 streamable-http 下，通讯目录（含 `audit/` 子目录）内的文件可按其它临时产物同样方式下载；日文件位于通讯根下的 `audit/`，建议优先使用返回的绝对路径 `audit.dir`。

## 哪些操作会记审计

工具名落在 `_AUDIT_TOOLS` 中时写入审计：

```text
_AUDIT_TOOLS = _DESTRUCTIVE_TOOLS ∪ _AUDIT_EXTRA
```

定义见 `maxmcp/server.py`。

| 集合 | 含义 |
|------|------|
| `_DESTRUCTIVE_TOOLS` | 会改场景 / 文件（同时带 MCP `destructiveHint`） |
| `_AUDIT_EXTRA` | 确认门、实例租约、加载 / 渲染、裸脚本、窗口还原、OCR 点击等 |

只读工具**不会**记审计。新增有风险的工具时，请加入上述集合之一。

## 可选 `user_id`

调用方可在每次 `tools/call` 的 **`arguments` 内**附带用户唯一 id。该字段**不**进入各工具的 inputSchema（避免污染 100+ 工具定义）。服务端在调用真实工具前剥离该字段，并写入审计行。

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "delete_objects",
    "arguments": {
      "user_id": "tenant-42:alice",
      "names": ["Box001"]
    }
  }
}
```

HTTP（streamable-http）备选请求头（当 `arguments` 未带 `user_id` 时）：

```http
X-Maxmcp-User-Id: tenant-42:alice
```

优先级：`arguments.user_id` > 请求头（请求头需 HTTP 栈暴露请求上下文时才生效；优先使用 arguments）。

**不要**把密码或令牌放进 `user_id`。最长 128 字符。

## 日志字段

```json
{
  "ts": "2026-09-15T18:46:01.234+08:00",
  "date": "2026-09-15",
  "user_id": "tenant-42:alice",
  "tool": "delete_objects",
  "audit_reason": "destructive",
  "scene_path": "D:/scenes/char.max",
  "ok": true,
  "elapsed_ms": 12.3,
  "args": { "names": ["Box001"] },
  "error": null,
  "transport": { "target_pid": 12345 }
}
```

| 字段 | 说明 |
|------|------|
| `ts` | 本地时区 ISO-8601 时间戳 |
| `date` | 日历日 `YYYY-MM-DD`（与文件名一致） |
| `user_id` | 可选调用方 id，缺省为 `null` |
| `tool` | MCP 工具名 |
| `audit_reason` | `destructive` 或 `extra` |
| `scene_path` | 当前 `.max` 路径（可知时），否则 `null` |
| `ok` / `error` / `elapsed_ms` | 来自工具返回信封 |
| `args` | 脱敏 / 截断后的参数摘要（已去掉 `data_b64` 等） |

`scene_path` 在 `load_scene` / `manage_scene` 成功后会刷新缓存；其它情况尽量从 Max 查询（`maxFilePath` + `maxFileName`）。未保存场景或桥断开 → `null`。审计写盘失败**不会**导致工具调用失败。

## 隐私

- 大二进制与 base64 载荷会从 `args` 中脱敏。
- 若策略要求，优先使用不透明租户 / 用户 id，避免邮箱或显示名。
- 按留存策略轮转或删除旧的 `audit/*.jsonl`。
