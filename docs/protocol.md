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

- 类型：Request
- 参数：`{"message": str, "conversation_id": str | null, "max_tokens": int | null}`（后两者可选，Phase 2 暂不实现多轮）
- 响应：`{"task_id": str, "text": str, "stop_reason": str, "usage": {"input_tokens": int, "output_tokens": int, ...}, "duration_ms": float, "model": str}`
- 流式：处理期间服务端持续推送 `chat.stream` 通知（见下）

### shutdown — 服务端关闭

- 类型：Notification（无 id，服务端不回响应）
- 触发服务端优雅退出

## Notification：chat.stream

Server → Client 实时推送，在 `chat` 请求处理期间逐块发送，最后一条 `is_final: true` 表示流结束。

```json
{
  "jsonrpc": "2.0",
  "method": "chat.stream",
  "params": {
    "task_id": "a1b2c3d4",
    "chunk_index": 1,
    "text": "增量文本",
    "is_final": false
  }
}
```

## 错误码

### 标准 JSON-RPC 错误码

| 错误码 | 含义 | 说明 |
| --- | --- | --- |
| `-32700` | Parse error | JSON 无法解析 |
| `-32600` | Invalid Request | 结构非法（非对象、缺 method 等） |
| `-32601` | Method not found | 方法未注册 |
| `-32602` | Invalid params | 参数不合法（如 chat 缺少 message） |
| `-32603` | Internal error | 处理器内部异常 |

### 应用错误码（LLM）

| 错误码 | 含义 | 触发条件 |
| --- | --- | --- |
| `-32001` | LLM error | LLM API 调用失败 / 限流 / 内容过滤 |
| `-32002` | LLM auth error | API key 无效（AuthenticationError） |
| `-32003` | LLM timeout | 请求超时（APITimeoutError） |
