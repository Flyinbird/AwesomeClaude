# JSON-RPC 协议文档

## 协议概述

AwesomeClaude 的 Client 与 Core 之间使用 **JSON-RPC 2.0** 规范进行通信，运行于 TCP 之上。每条消息是一个 JSON 对象，序列化为 UTF-8 字节后以换行符（`\n`）作为消息边界分帧，一行即一条消息，便于流式解析。消息必须包含 `"jsonrpc": "2.0"` 字段。

## 消息类型

- **请求（Request）**：`{"jsonrpc": "2.0", "method": "<名称>", "params": {...}, "id": <string|number>}`，`params` 可选，`id` 用于关联响应。
- **通知（Notification）**：同请求但不含 `id`，接收方**不返回**响应。
- **成功响应**：`{"jsonrpc": "2.0", "result": <任意 JSON>, "id": <与请求一致>}`。
- **错误响应**：`{"jsonrpc": "2.0", "error": {"code": <int>, "message": <str>, "data": <可选>}, "id": ...}`，`result` 与 `error` 互斥。
- **批量（Batch）**：当前版本**不支持**数组形式的批量消息。

解析或校验失败时，若无法识别请求 id，错误响应中的 `id` 置为 `null`；通知消息即使失败也不回错误响应。

## JSON-RPC 方法

### ping — 健康检查

- 类型：Request
- 参数：无
- 响应：`{"status": "ok", "timestamp": "<ISO 8601>"}`

```json
{"jsonrpc": "2.0", "method": "ping", "id": 1}
{"jsonrpc": "2.0", "result": {"status": "ok", "timestamp": "2026-08-28T04:00:00+00:00"}, "id": 1}
```

### echo — 回显测试

- 类型：Request
- 参数：`{"message": str}`
- 响应：`{"echo": str}`

### chat — LLM 对话

- 类型：Request（**受理 + 通知**模型，最终结果不在本请求响应中返回）
- 参数：`{"message": str, "session_id": str | null, "conversation_id": str | null, "max_tokens": int | null}`（后三者可选）
  - `session_id`：多客户端共享会话时指定；服务端据此广播通知并记录会话历史
- 受理响应：`{"run_id": str, "accepted": true, "heartbeat_interval_ms": int}`
  - 服务端在 Run 创建成功后**立即**返回该受理应答，表示对话已被接受，不代表对话完成
  - 非法参数（缺少 `message`）返回 `-32602`；目标会话已有在途 Run 返回 `-32004`，均不创建 Run
- 完成/失败：对话终态通过 `chat.completed` / `chat.failed` 通知推送（见下），二者互斥且各至多一次
  - `stop_reason` 取值：`end_turn` / `max_tokens` / `stop_sequence` / `tool_use` / `max_steps`（达到最大步数）/ `unknown`
- 流程：服务端经 AgentLoop 编排多轮 LLM 调用与工具调用（含任务计划工具 add_tasks / update_task_deps / start_task / complete_task / reopen_task / suspend_task），期间持续推送 `chat.stream`、`chat.tool_started`、`chat.tool_finished` 通知（见下）；任务清单变化时推送 `chat.plan_updated`；Run 执行期间周期性推送 `chat.heartbeat`；达到最大步数时额外推送 `chat.interrupted` 通知

```json
{"jsonrpc": "2.0", "method": "chat", "params": {"message": "你好", "session_id": "shared-123"}, "id": 7}
{"jsonrpc": "2.0", "result": {"run_id": "a1b2c3d4", "accepted": true, "heartbeat_interval_ms": 15000}, "id": 7}
```

### session.attach — 订阅会话

- 类型：Request
- 参数：`{"session_id": str}`
- 响应：`{"session_id": str, "history": [{"role", "content", "run_id"}], "active_runs": [str]}`
  - `history` 为该会话已有对话历史，供晚加入的客户端回放

### session.detach — 退订会话

- 类型：Request
- 参数：无
- 响应：`{}`

### shutdown — 服务端关闭

- 类型：Notification（无 id，服务端不回响应）
- 触发服务端优雅退出

## Notification：chat.stream

Server → Client 实时推送，在 `chat` 请求处理期间逐块发送，最后一条 `is_final: true` 表示流结束。若请求带 `session_id`，则广播到会话内所有订阅连接。

```json
{
  "jsonrpc": "2.0",
  "method": "chat.stream",
  "params": {
    "run_id": "a1b2c3d4",
    "chunk_index": 1,
    "text": "增量文本",
    "is_final": false,
    "session_id": "shared-123"
  }
}
```

## Notification：chat.tool_started / chat.tool_finished

Server → Client 推送，报告 Agent Loop 中的工具调用进度。

```json
{
  "jsonrpc": "2.0",
  "method": "chat.tool_started",
  "params": {
    "run_id": "a1b2c3d4",
    "step_index": 1,
    "tool_name": "get_time",
    "args": {},
    "session_id": "shared-123"
  }
}
```

```json
{
  "jsonrpc": "2.0",
  "method": "chat.tool_finished",
  "params": {
    "run_id": "a1b2c3d4",
    "step_index": 1,
    "tool_name": "get_time",
    "is_error": false,
    "session_id": "shared-123"
  }
}
```

## Notification：chat.user_message

Server → Client 推送，广播会话内某个客户端发起的用户输入（排除发起方自身）。

```json
{
  "jsonrpc": "2.0",
  "method": "chat.user_message",
  "params": {
    "session_id": "shared-123",
    "message": "你好"
  }
}
```

## Notification：chat.interrupted

Server → Client 推送，当 agent 循环达到最大步数（max_steps）但 Run 未完整完成时触发，用于提示用户结果可能不完整。

```json
{
  "jsonrpc": "2.0",
  "method": "chat.interrupted",
  "params": {
    "run_id": "a1b2c3d4",
    "stop_reason": "max_steps",
    "step_index": 26,
    "session_id": "shared-123"
  }
}
```

## Notification：chat.completed

Server → Client 推送，表示对话正常结束（含达到步数上限）。携带最终文本、停止原因、token 用量、耗时与模型。若请求带 `session_id`，则广播到会话内所有订阅连接。同一 Run 至多推送一次。

```json
{
  "jsonrpc": "2.0",
  "method": "chat.completed",
  "params": {
    "run_id": "a1b2c3d4",
    "text": "最终答复",
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 120, "output_tokens": 45},
    "duration_ms": 3210.5,
    "model": "claude-sonnet-4-20250514",
    "session_id": "shared-123"
  }
}
```

## Notification：chat.failed

Server → Client 推送，表示对话因错误终止。`error` 与 JSON-RPC 错误对象同构（至少含 `code` 与 `message`）。与 `chat.completed` 互斥，同一 Run 至多推送一次。

```json
{
  "jsonrpc": "2.0",
  "method": "chat.failed",
  "params": {
    "run_id": "a1b2c3d4",
    "error": {"code": -32003, "message": "LLM 请求超时: ..."},
    "session_id": "shared-123"
  }
}
```

## Notification：chat.heartbeat

Server → Client 推送，在 Run 执行期间周期性发送（间隔由服务端配置，默认 15s），作为对话仍在进行、连接仍存活的证明。Run 进入终态后停止推送。

```json
{
  "jsonrpc": "2.0",
  "method": "chat.heartbeat",
  "params": {
    "run_id": "a1b2c3d4",
    "timestamp": "2026-09-19T04:00:00+00:00",
    "session_id": "shared-123"
  }
}
```

## Notification：chat.plan_updated

Server → Client 推送，当本次 Run 的任务清单发生变化（新增 / 启动 / 完成 / 重试 / 让位）时，携带最新任务快照。若请求带 `session_id`，则广播到会话内所有订阅连接。

```json
{
  "jsonrpc": "2.0",
  "method": "chat.plan_updated",
  "params": {
    "run_id": "a1b2c3d4",
    "session_id": "shared-123",
    "tasks": [
      {"id": "t1", "goal": "读取文件", "status": "completed", "deps": [], "attempts": 0},
      {"id": "t2", "goal": "写入结果", "status": "pending", "deps": ["t1"], "attempts": 0}
    ]
  }
}
```

任务状态取值：`pending` / `in_progress` / `completed`（无失败状态，失败经 `reopen` 回到 `pending`）。

## 错误码

### 标准 JSON-RPC 错误码

| 错误码 | 含义 | 说明 |
| --- | --- | --- |
| `-32700` | Parse error | JSON 无法解析 |
| `-32600` | Invalid Request | 结构非法（非对象、缺 method 等） |
| `-32601` | Method not found | 方法未注册 |
| `-32602` | Invalid params | 参数不合法（如 chat 缺少 message） |
| `-32603` | Internal error | 处理器内部异常 |

### 应用错误码

| 错误码 | 含义 | 触发条件 |
| --- | --- | --- |
| `-32001` | LLM error | LLM API 调用失败 / 限流 / 内容过滤 |
| `-32002` | LLM auth error | API key 无效（AuthenticationError） |
| `-32003` | LLM timeout | 请求超时（APITimeoutError） |
| `-32004` | Session busy | 目标会话已有在途对话（活跃 Run），并发 `chat` 被拒绝 |
