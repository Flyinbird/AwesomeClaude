# 架构文档

## 架构概述

AwesomeClaude 采用 **Client-Server 架构**：`client/`（命令行 CLI）与 `core/`（常驻守护进程）通过 TCP Socket 传输 JSON-RPC 2.0 消息通信，两者只能依赖 `protocol/` 包中定义的共享协议（消息编解码、方法名与参数/返回类型、错误码），不能互相直接 import。客户端负责命令解析、请求发送、响应与流式输出渲染；核心服务端负责 TCP 监听、请求路由、业务处理器分发，通过 `core/agent/`（AgentLoop 多轮 LLM + 工具编排）、`core/tools/`（ToolRegistry 工具注册与执行）、`core/session/`（多客户端会话共享）、`core/llm/`（LLMProvider 协议 + AnthropicClient 流式调用 Anthropic API）、`core/task/`（TaskManager 任务生命周期追踪）、`shared/logging/`（structlog 结构化日志 + TaskTracker JSONL）支撑完整链路。所有网络 I/O 基于 asyncio，支持多客户端并发与 SIGTERM/SIGINT 优雅退出。

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
|  · 后台读 / 通知分发       |                             |  · ping/echo/shutdown      |
|                           |   响应（result/error）       |  · chat: 委托 AgentLoop    |
|                           |  <----------------------   |  · session: attach/detach  |
|                           |  通知（Server→Client，实时推送）                          |
|                           |   chat.stream / chat.tool_* / chat.user_message          |
+-----------+---------------+                             +--------------+-------------+
            |                                                     |
            |  protocol/jsonrpc.py · 消息构造/解析/校验             |
            |  protocol/methods.py · 方法名/参数/返回类型            |
            |  protocol/errors.py  · 标准+应用错误码                |
            +--------------------------+--------------------------+
                                       |
            +--------------------------+--------------------------+
            |  core/agent/loop.py   · AgentLoop 多轮编排           |
            |  core/tools/          · ToolRegistry 工具注册/执行     |
            |  core/session/        · SessionRegistry 会话共享      |
            |  core/llm/anthropic_client.py · Anthropic SDK 流式封装   |
            |  core/task/manager.py · TaskManager 生命周期（step 化）|
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
| `core/server/session.py` | 逐行读取 → 解析 → 分发 → 回写；注入 `SessionChannel` 上下文 |
| `core/router/dispatcher.py` | 方法分发，未注册方法返回 METHOD_NOT_FOUND |
| `core/router/context.py` | HandlerContext（task_manager / llm_client / sessions / config / agent_loop） |
| `core/agent/loop.py` | AgentLoop：多轮 LLM + 工具编排，`on_event` / `on_step` 回调透出事件 |
| `core/agent/events.py` | StepStarted / StepFinished / ToolStarted / ToolFinished |
| `core/agent/result.py` | AgentResult（文本、消息历史、用量、步数、停止原因） |
| `core/tools/registry.py` | ToolRegistry：注册 / 查询 / 执行 / 转 Anthropic tools schema |
| `core/tools/builtin/time.py` | 内置 `get_time` 工具 |
| `core/session/registry.py` | ConnectionSink / Session / SessionRegistry（订阅、广播、状态） |
| `core/session/channel.py` | SessionChannel：每连接门面，广播或单播 |
| `core/handlers/chat.py` | chat 完整流程：任务追踪 + 委托 AgentLoop + 通知广播 |
| `core/handlers/session.py` | session.attach / session.detach |
| `core/llm/base.py` | LLMProvider 协议：供应商无关的流式/非流式客户端接口 |
| `core/llm/anthropic_client.py` | AnthropicClient：Anthropic SDK 封装（`chat_stream` 文本/思考/工具事件、`chat`、异常映射） |
| `core/task/manager.py` | 任务创建与阶段事件记录（含 step 维度） |
| `core/app.py` | 装配 TaskManager/AnthropicClient/ToolRegistry/AgentLoop/SessionRegistry/TCPServer |
| `core/config.py` | ServerConfig + `load_server_config()`（.env / 环境变量） |
| `client/cli/app.py` | REPL 主循环，attach 会话、注册各类通知处理器 |
| `client/transport/receiver.py` | 后台读取，response → Future，notification → handler |
| `client/transport/connection.py` | 连接管理、request-response、通知注册 |
| `shared/types.py` | TaskStage / TaskEvent / StreamChunk / TokenUsage / ChatResponse |
| `shared/logging/task_tracker.py` | TaskEvent 写入 `{log_dir}/{date}/{task_id}.jsonl` |
| `shared/logging/app_logger.py` | structlog 配置（stdout 彩色文本 + 文件 JSON） |

## chat 请求完整数据流

```
用户输入 "你好"
  → client/cli/app.py 判定为 chat 输入
  → connection.send_request("chat", {"message": "你好", "session_id": "…"})   # id=N
  → core/session 收到请求 → dispatcher.dispatch("chat", params, context)
  → handle_chat:
      1. task_manager.create_task()      → task_id + TASK_CREATED 写入 JSONL
      2. record_stage(CONTEXT_BUILT)     → 构建上下文
      3. （若带 session_id）channel.attach(session_id) 订阅会话
      4. agent_loop.run(message, on_event, on_step)
          └─ 每轮 step：
             on_step(StepStarted) → record_stage(STEP_STARTED / LLM_REQUEST_SENT / LLM_STREAMING, step_index)
             on_event(TextDeltaEvent) → broadcast("chat.stream", {…, is_final:false})
             on_step(ToolStarted/ToolFinished) → record_stage(TOOL_*) + broadcast("chat.tool_*")
             on_step(StepFinished) → record_stage(LLM_RESPONSE_DONE, step_index)
      5. broadcast("chat.stream", {…, is_final:true})  → 流结束
      6. （若带 session_id）channel.record_turn(user, assistant, task_id) 记录会话历史
      7. complete_task() / ChatResponse 返回
  → session 回写 response（含 task_id / text / usage / duration_ms / model）
  → client 收到 → render_summary() 显示 tokens / 耗时 / task_id
```

## 任务生命周期阶段

| 阶段 | 说明 |
| --- | --- |
| `task_created` | 任务创建，记录 user_input、client_addr |
| `context_built` | 上下文构建，记录 message_count |
| `step_started` | 一轮 agent step 开始（每次 LLM 调用） |
| `llm_request_sent` | LLM 请求已发出，记录 model |
| `llm_streaming` | 流式输出中，逐块推送 chat.stream |
| `llm_response_done` | 一轮结束，记录 stop_reason、token 用量、has_tool_calls |
| `tool_started` | 工具调用开始，记录 tool_name、args |
| `tool_completed` | 工具调用成功，记录 tool_name |
| `tool_failed` | 工具调用失败，记录 tool_name |
| `task_completed` | 正常完成，记录 text_length、stop_reason、steps |
| `task_failed` | 失败，记录 failed_stage、error_type、error_message、traceback |

每个阶段事件以 JSON 行写入 `logs/tasks/{date}/{task_id}.jsonl`，含 `task_id / stage / timestamp / duration_ms / data / step_index`。`step_index` 用于区分多轮 Agent Loop 中的轮次（顶层阶段为 null）。

## 多客户端会话

- `SessionRegistry` 维护 `session_id → Session`，每个 `Session` 持有订阅连接集合（`ConnectionSink`）、对话历史（`history`）与关联任务（`task_ids`）。
- 客户端通过 `session.attach` 订阅会话并回放历史；`SessionChannel.broadcast` 在已订阅时扇出到会话内所有连接，未订阅时单播（向后兼容）。
- `chat.user_message` 广播用户输入时排除发起方（`exclude_self`）。

## 架构约束

- `core/` 与 `client/` 只能通过 `protocol/` 中定义的接口通信，不能直接互相 import
- 所有网络 I/O 均使用 asyncio，不阻塞事件循环
- 消息严格遵循 JSON-RPC 2.0 规范（request / response / notification / error，标准错误码 -32700 ~ -32000）
- 配置从 `.env` / 环境变量读取，不硬编码
- 守护进程支持优雅退出（SIGTERM / SIGINT）
