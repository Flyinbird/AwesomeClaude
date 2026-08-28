# JSON-RPC 协议文档

## 协议概述

AwesomeClaude 的 Client 与 Core 之间使用 **JSON-RPC 2.0** 规范进行通信，运行于 TCP 之上。每条消息是一个 JSON 对象，序列化为 UTF-8 字节后以换行符（`\n`）作为消息边界分帧，一行即一条消息，便于流式解析。消息必须包含 `"jsonrpc": "2.0"` 字段。

## 消息类型

- **请求（Request）**：`{"jsonrpc": "2.0", "method": "<名称>", "params": {...}, "id": <string|number>}`，`params` 可选，`id` 用于关联响应。
- **通知（Notification）**：同请求但不含 `id`，服务端**不返回**响应。
- **成功响应**：`{"jsonrpc": "2.0", "result": <任意 JSON>, "id": <与请求一致>}`。
- **错误响应**：`{"jsonrpc": "2.0", "error": {"code": <int>, "message": <str>, "data": <可选>}, "id": ...}`，`result` 与 `error` 互斥。
- **批量（Batch）**：当前版本**不支持**数组形式的批量消息。

## 标准错误码

| 错误码 | 含义 | 说明 |
| --- | --- | --- |
| `-32700` | Parse error | JSON 无法解析 |
| `-32600` | Invalid Request | 结构非法（非对象、缺 method 等） |
| `-32601` | Method not found | 方法未注册 |
| `-32602` | Invalid params | 参数不合法 |
| `-32603` | Internal error | 处理器内部异常 |

解析或校验失败时，若无法识别请求 id，错误响应中的 `id` 置为 `null`；通知消息即使失败也不回错误响应。

## 当前方法

- `ping`（Phase 1）：无参数，返回 `{"status": "ok", "timestamp": "<ISO 时间>"}`
- `echo`（Phase 1）：参数 `{"message": str}`，返回 `{"echo": str}`
- `shutdown`（Phase 1）：通知类型，触发服务端优雅退出，无返回
- `chat`（Phase 2）：接入 Anthropic LLM，实现流式对话
