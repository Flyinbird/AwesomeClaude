# 架构文档

## 架构概述

AwesomeClaude 采用 **Client-Server 架构**：`client/`（命令行 CLI）与 `core/`（常驻守护进程）通过 TCP Socket 传输 JSON-RPC 2.0 消息通信，两者只能依赖 `protocol/` 包中定义的共享协议（消息编解码、方法名与参数/返回类型、错误码），不能互相直接 import。客户端负责命令解析、请求发送、响应与流式输出渲染；核心服务端负责 TCP 监听、请求路由、业务处理器分发，并通过 `core/llm/`（LLMClient 流式调用 Anthropic API）、`core/task/`（TaskManager 任务生命周期追踪）、`shared/logging/`（structlog 结构化日志 + TaskTracker JSONL）支撑完整链路。所有网络 I/O 基于 asyncio，支持多客户端并发与 SIGTERM/SIGINT 优雅退出。

## 架构图

```
                         TCP Socket / JSON-RPC 2.0
                    （每消息一行 JSON，\n 分帧）

+---------------------------+        请求 / 通知          +----------------------------+
|   Client (CLI)            |  ---------------------->   |   Core (Daemon)            |
|                           |                             |                            |
|  client/cli/app.py        |                             |  core/server/tcp.py        |
|  · REPL 主循环            |                             |  · TCP 监听 / 多客户端     |
|  client/cli/commands.py   |                             |  core/server/session.py    |
|  · 命令解析               |                             |  · 逐行读取 / 分发 / 回写   |
|  client/cli/renderer.py   |                             |  core/router/dispatcher.py |
|  · 流式渲染 / 摘要        |                             |  · 方法路由               |
|  client/transport/connection.py |                        |  core/router/context.py    |
|  · TCP 连接 / 请求响应    |                             |  · HandlerContext          |
|  client/transport/receiver.py   |                        |  core/handlers/            |
|  · 后台读 / 通知分发       |                             |  · ping/echo/chat/shutdown |
|                           |   响应（result/error）       |  · chat: 流式 + 任务追踪    |
|                           |  <----------------------   |                            |
|                           |   chat.stream 通知（Server→Client，实时推送）  |
+-----------+---------------+                             +--------------+-------------+
            |                                                     |
            |  protocol/jsonrpc.py · 消息构造/解析/校验             |
            |  protocol/methods.py · 方法名/参数/返回类型            |
            |  protocol/errors.py  · 标准+应用错误码                |
            +--------------------------+--------------------------+
                                       |
            +--------------------------+--------------------------+
            |  core/llm/client.py  · Anthropic SDK 流式封装         |
            |  core/task/manager.py · TaskManager 生命周期          |
            |  shared/logging/task_tracker.py · 写入 logs/tasks/*.jsonl |
            |  shared/logging/app_logger.py · structlog（stdout 彩色 + 文件 JSON） |
            +------------------------------------------------------+
```

## 各模块职责

| 模块 | 职责 |
| --- | --- |
| `protocol/jsonrpc.py` | JSON-RPC 2.0 消息构造/解析/校验、字节编解码（`\n` 分帧） |
| `protocol/methods.py` | 方法名常量与参数/返回类型 TypedDict，client/core 共用 |
| `protocol/errors.py` | 标准错误码与 LLM 应用错误码、`build_error_response` |
| `core/server/tcp.py` | TCP 监听、多客户端并发、优雅停止 |
| `core/server/session.py` | 逐行读取 → 解析 → 分发 → 回写；注入 `send_notification` 上下文 |
| `core/router/dispatcher.py` | 方法分发，未注册方法返回 METHOD_NOT_FOUND |
| `core/router/context.py` | HandlerContext（task_manager / llm_client / send_notification / config） |
| `core/handlers/chat.py` | chat 完整流程：任务追踪 + LLM 流式调用 + chat.stream 推送 |
| `core/llm/client.py` | Anthropic SDK 封装：`chat_stream`（TextDelta/Done 事件）、`chat`、异常映射 |
| `core/task/manager.py` | 任务创建与阶段事件记录（TASK_CREATED → … → COMPLETED/FAILED） |
| `core/app.py` | 装配 TaskManager/LLMClient/Dispatcher/TCPServer，信号处理 |
| `core/config.py` | ServerConfig + `load_server_config()`（.env / 环境变量） |
| `client/cli/app.py` | REPL 主循环，注册 chat.stream 通知处理器 |
| `client/transport/receiver.py` | 后台读取，response → Future，notification → handler |
| `client/transport/connection.py` | 连接管理、request-response、通知注册 |
| `shared/types.py` | TaskStage / TaskEvent / StreamChunk / TokenUsage / ChatResponse |
| `shared/logging/task_tracker.py` | TaskEvent 写入 `{log_dir}/{date}/{task_id}.jsonl` |
| `shared/logging/app_logger.py` | structlog 配置（stdout 彩色文本 + 文件 JSON） |

## chat 请求完整数据流

```
用户输入 "你好"
  → client/cli/app.py 判定为 chat 输入
  → connection.send_request("chat", {"message": "你好"})   # id=N
  → core/session 收到请求 → dispatcher.dispatch("chat", params, context)
  → handle_chat:
      1. task_manager.create_task()      → task_id + TASK_CREATED 写入 JSONL
      2. record_stage(CONTEXT_BUILT)     → 构建 messages
      3. record_stage(LLM_REQUEST_SENT)  → 记录 model
      4. record_stage(LLM_STREAMING)     → 开始 llm_client.chat_stream(messages)
         └─ 每个 TextDeltaEvent →
              session.send_notification("chat.stream", {task_id, chunk_index, text, is_final:false})
              └─ client/receiver 收到 → 渲染 render_chunk(text)  → 终端逐块输出
         └─ DoneEvent →
              send_notification("chat.stream", {…, is_final:true})
              record_stage(LLM_RESPONSE_DONE)  → token 统计
      5. complete_task() / ChatResponse 返回
  → session 回写 response（含 task_id / text / usage / duration_ms / model）
  → client 收到 → render_summary() 显示 tokens / 耗时 / task_id
```

## 任务生命周期阶段

| 阶段 | 说明 |
| --- | --- |
| `task_created` | 任务创建，记录 user_input、client_addr |
| `context_built` | 上下文构建，记录 message_count |
| `llm_request_sent` | LLM 请求已发出，记录 model |
| `llm_streaming` | 流式输出中，逐块推送 chat.stream |
| `llm_response_done` | 流式结束，记录 chunk_count、stop_reason、token 用量 |
| `task_completed` | 正常完成，记录 text_length、stop_reason |
| `task_failed` | 失败，记录 failed_stage、error_type、error_message、traceback |

每个阶段事件以 JSON 行写入 `logs/tasks/{date}/{task_id}.jsonl`，含 `task_id / stage / timestamp / duration_ms / data`。

## 架构约束

- `core/` 与 `client/` 只能通过 `protocol/` 中定义的接口通信，不能直接互相 import
- 所有网络 I/O 均使用 asyncio，不阻塞事件循环
- 消息严格遵循 JSON-RPC 2.0 规范（request / response / notification / error，标准错误码 -32700 ~ -32000）
- 配置从 `.env` / 环境变量读取，不硬编码
- 守护进程支持优雅退出（SIGTERM / SIGINT）
