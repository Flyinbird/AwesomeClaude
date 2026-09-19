# 架构文档

## 架构概述

AwesomeClaude 采用 **Client-Server 架构**：`client/`（命令行 CLI）与 `core/`（常驻守护进程）通过 TCP Socket 传输 JSON-RPC 2.0 消息通信，两者只能依赖 `protocol/` 包中定义的共享协议（消息编解码、方法名与参数/返回类型、错误码），不能互相直接 import。客户端负责命令解析、请求发送、响应与流式输出渲染；核心服务端负责 TCP 监听、请求路由、业务处理器分发，通过 `core/agent/`（AgentLoop 多轮 LLM + 工具编排）、`core/tools/`（ToolRegistry 工具注册与执行）、`core/session/`（多客户端会话共享）、`core/llm/`（LLMProvider 协议 + AnthropicClient 流式调用 Anthropic API）、`core/observability/`（TraceRecorder Run 轨迹记录）、`shared/logging/`（structlog 结构化日志 + TraceStore JSONL）支撑完整链路。所有网络 I/O 基于 asyncio，支持多客户端并发与 SIGTERM/SIGINT 优雅退出。

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
|                           |   chat.plan_updated（任务计划快照）                       |
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
            |  core/task/           · TaskGraph 任务 DAG / 状态机     |
            |  core/session/        · SessionRegistry 会话共享      |
            |  core/llm/anthropic_client.py · Anthropic SDK 流式封装   |
            |  core/observability/trace_recorder.py · TraceRecorder Run 轨迹（step 化）|
            |  shared/logging/trace_store.py · 写入 logs/runs/*.jsonl |
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
| `core/server/session.py` | 逐行读取 → 解析 → 分发 → 回写；控制类请求内联、chat 任务化为 Run；断连取消与发送串行化 |
| `core/router/dispatcher.py` | 方法分发，未注册方法返回 METHOD_NOT_FOUND |
| `core/router/context.py` | HandlerContext（trace_store / llm_client / sessions / config / agent_loop / run） |
| `core/agent/loop.py` | AgentLoop：多轮 LLM + 工具编排，`on_event` / `on_step` 回调透出事件，撞 max_steps 时收尾/截断 |
| `core/agent/events.py` | StepStarted / StepFinished / ToolStarted / ToolFinished |
| `core/agent/result.py` | AgentResult（文本、消息历史、用量、步数、StopReason） |
| `core/tools/registry.py` | ToolRegistry：注册 / 查询 / 执行（注入 ToolContext + ToolScope）/ 转 Anthropic tools schema |
| `core/tools/builtin/time.py` | 内置 `get_time` 工具 |
| `core/tools/builtin/plan.py` | 任务计划工具：add_tasks / update_task_deps / start_task / complete_task / reopen_task / suspend_task |
| `core/task/graph.py` | TaskGraph：Run 内任务依赖 DAG 的校验与状态转换，变更经 on_change 派发 |
| `core/task/task.py` | Task / TaskStatus：三态任务与 attempts / last_error 元数据 |
| `core/session/registry.py` | ConnectionSink / Session / SessionRegistry（订阅、在途 Run、广播、状态、零订阅取消与临时会话销毁） |
| `core/session/run.py` | Run / RunState / RunInitiator：会话拥有的对话执行实体与唯一终态状态机 |
| `core/session/channel.py` | SessionChannel：每连接门面，广播（含指定会话）或单播 |
| `core/handlers/chat.py` | chat 完整流程：Run 轨迹记录 + 任务计划（TaskGraph）+ 委托 AgentLoop + 通知广播 |
| `core/handlers/session.py` | session.attach / session.detach |
| `core/llm/base.py` | LLMProvider 协议：供应商无关的流式/非流式客户端接口 |
| `core/llm/anthropic_client.py` | AnthropicClient：Anthropic SDK 封装（`chat_stream` 文本/思考/工具事件、`chat`、异常映射） |
| `core/observability/trace_recorder.py` | Run 作用域轨迹记录（绑定 run_id 与起点，含 step 维度） |
| `core/app.py` | 装配 TraceStore/AnthropicClient/ToolRegistry/AgentLoop/SessionRegistry/TCPServer |
| `core/config.py` | ServerConfig + `load_server_config()`（.env / 环境变量） |
| `client/cli/app.py` | REPL 主循环，attach 会话、注册各类通知处理器 |
| `client/transport/receiver.py` | 后台读取，response → Future，notification → handler |
| `client/transport/connection.py` | 连接管理、request-response、通知注册 |
| `shared/types.py` | TraceStage / StopReason / TraceEvent / StreamChunk / TokenUsage / ChatResponse |
| `shared/logging/trace_store.py` | TraceEvent 写入 `{log_dir}/{date}/{run_id}.jsonl` |
| `shared/logging/app_logger.py` | structlog 配置（stdout 彩色文本 + 文件 JSON） |

## chat 请求完整数据流

```
用户输入 "你好"
  → client/cli/app.py 判定为 chat 输入
  → connection.send_request("chat", {"message": "你好", "session_id": "…"})   # id=N
  → core/server/session._start_chat:
      1. 建 Run + recorder.run_created()  → RUN_CREATED 写入 JSONL
      2. 立即回写受理 ack（run_id / accepted / heartbeat_interval_ms）→ client 解除阻塞
      3. 启动心跳任务（每 heartbeat_interval 推送 chat.heartbeat）
      4. 异步派发 handle_chat：
           a. record_stage(CONTEXT_BUILT)  → 构建上下文
           b. （若带 session_id）channel.attach(session_id) 订阅会话
           c. agent_loop.run(message, on_event, on_step)
               └─ 每轮 step：
                  on_step(StepStarted) → record_stage(STEP_STARTED / LLM_REQUEST_SENT / LLM_STREAMING, step_index)
                  on_event(TextDeltaEvent) → broadcast("chat.stream", {…, is_final:false})
                  on_step(ToolStarted/ToolFinished) → record_stage(TOOL_*) + broadcast("chat.tool_*")
                  on_step(StepFinished) → record_stage(LLM_RESPONSE_DONE, step_index)
           d. broadcast("chat.stream", {…, is_final:true})  → 流结束
           e. （若带 session_id）channel.record_turn(user, assistant, run_id) 记录会话历史
           f. recorder.run_completed() / run_interrupted() / run_failed()
      5. session 停心跳后广播终态：
           chat.completed（含 run_id / text / stop_reason / usage / duration_ms / model）
           或 chat.failed（含 run_id / error）
  → client 收到终态通知 → render_summary()（或 render_error()）显示 tokens / 耗时 / run_id
```

无 session_id 时完成/失败/心跳通知单播发起连接；带 session_id 时扇出会话内全部订阅者。

## 执行轨迹阶段

| 阶段 | 说明 |
| --- | --- |
| `run_created` | Run 创建，记录 user_input、client_addr |
| `context_built` | 上下文构建，记录 message_count |
| `step_started` | 一轮 agent step 开始（每次 LLM 调用） |
| `llm_request_sent` | LLM 请求已发出，记录 model |
| `llm_streaming` | 流式输出中，逐块推送 chat.stream |
| `llm_response_done` | 一轮结束，记录 stop_reason、token 用量、has_tool_calls |
| `tool_started` | 工具调用开始，记录 tool_name、args |
| `tool_completed` | 工具调用成功，记录 tool_name |
| `tool_failed` | 工具调用失败，记录 tool_name |
| `task_added` | 任务加入计划，记录 goal / deps |
| `task_started` | 任务开始执行 |
| `task_completed` | 任务完成 |
| `task_reopened` | 任务失败退回待启动，累计 attempts、记录 last_error |
| `task_suspended` | 任务让位退回待启动（不计 attempts） |
| `run_completed` | 正常完成，记录 text_length、stop_reason、steps |
| `run_failed` | 失败，记录 failed_stage、error_type、error_message、traceback |
| `run_interrupted` | 达到最大步数（max_steps）未完整完成，记录 stop_reason、steps |
| `run_cancelled` | 在途 Run 被取消（断连零订阅 / 服务端退出），记录 session_id |

每个阶段事件以 JSON 行写入 `logs/runs/{date}/{run_id}.jsonl`，含 `run_id / stage / timestamp / duration_ms / data / step_index`。`step_index` 用于区分多轮 Agent Loop 中的轮次（顶层阶段为 null）。

## 多客户端会话与 Run 生命周期

- `SessionRegistry` 维护 `session_id → Session`，每个 `Session` 持有订阅连接集合（`ConnectionSink`）、对话历史（`history`）、至多一个在途 `Run`（`active_run`）与临时会话标记（`ephemeral`）。
- 客户端通过 `session.attach` 订阅会话并回放历史；`SessionChannel.broadcast` 在已订阅时扇出到会话内所有连接，未订阅时单播（向后兼容）。`broadcast_to` 可显式指定会话，供在途 Run 在发起连接断开后继续送达其余订阅者。
- `chat.user_message` 广播用户输入时排除发起方（`exclude_self`）。
- **Run**：每次对话执行由目标会话拥有，承载一个 asyncio 任务，状态机为 `RUNNING → {COMPLETED, FAILED, INTERRUPTED, CANCELLED}`，终态只记录一次。未携带 `session_id` 的对话使用服务端生成的临时会话，对话结束即销毁；命名会话在无订阅时保留历史以供回放。
- **每会话单活跃 Run**：会话已有在途 Run 时，新对话被拒绝并返回应用错误码 `-32004 SESSION_BUSY`。
- **零订阅取消**：最后一个订阅连接 detach 后，会话活跃 Run 被取消并在非取消上下文记录 `run_cancelled`。`session.attach` 返回的 `active_runs` 仅包含在途 Run 标识。
- **取消不写历史**：被取消的 Run 不向会话历史追加轮次，避免半截对话污染回放。

## 连接生命周期

- 连接读循环与请求执行解耦：ping / echo / session.* / shutdown 等控制类请求按到达顺序内联处理；仅 `chat` 作为 Run 异步执行，使读循环在对话期间仍能受理消息并及时感知断开。
- 连接发送串行化：每个连接持有发送锁与关闭标志，统一处理并发写入（控制响应、受理 ack、对话终态与心跳等会话广播），连接关闭后的发送被安全丢弃。
- 服务端优雅退出：关闭流程先取消全部在途 Run 并落地终态，再取消连接任务，避免连接任务被取消后二次打断 Run 清理。

## 架构约束

- `core/` 与 `client/` 只能通过 `protocol/` 中定义的接口通信，不能直接互相 import
- 所有网络 I/O 均使用 asyncio，不阻塞事件循环
- 消息严格遵循 JSON-RPC 2.0 规范（request / response / notification / error，标准错误码 -32700 ~ -32000）
- 配置从 `.env` / 环境变量读取，不硬编码
- 守护进程支持优雅退出（SIGTERM / SIGINT）
