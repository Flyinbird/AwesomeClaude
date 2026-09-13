# AwesomeClaude — 项目规范

## 项目概述
AwesomeClaude 是一个类似 Claude Code 的 AI Agent 框架，采用 Client-Server 架构。
`core/`（常驻守护进程）通过 TCP Socket + JSON-RPC 2.0 协议接收 `client/`（CLI）命令并执行响应。
当前已完成 Phase 2（基于 Anthropic SDK 的流式 LLM 对话、任务生命周期追踪、结构化日志），
并落地了 Agent Loop 骨架（Tool Use 多轮编排）、任务模型 step 化、多客户端会话共享（Session）。
Tool Use 已内置沙箱约束的文件系统四件套（read_file / write_file / edit_file / list_dir）。
未来将扩展命令执行、Memory Compression、Planning 等能力。

## 技术栈
- 语言：Python 3.12+
- 包管理：uv（默认源：阿里云镜像，见 `pyproject.toml`）
- 通信协议：TCP Socket + JSON-RPC 2.0（每消息一行 JSON，`\n` 分帧）
- 异步框架：asyncio（标准库）
- LLM：anthropic SDK（可对接官方或 DeepSeek 等 Anthropic 兼容端点）
- 配置：python-dotenv（.env / 环境变量）
- 日志：structlog（stdout 彩色文本 + 文件 JSON）
- 测试：pytest + pytest-asyncio（`asyncio_mode = "auto"`）
- 类型检查：mypy（strict mode）
- Lint / 格式化：ruff

## 项目结构
```
awesome-claude/
├── pyproject.toml          # 依赖、pytest 配置、uv 镜像
├── AGENTS.md
├── README.md
├── .env.example            # 环境变量模板
├── src/
│   └── awesome_claude/
│       ├── core/           # 守护进程 (Server)
│       │   ├── app.py          # 应用装配与启动（run_server）
│       │   ├── config.py       # ServerConfig + load_server_config()
│       │   ├── agent/          # Agent 运行时
│       │   │   ├── loop.py         # AgentLoop：多轮 LLM + 工具编排
│       │   │   ├── events.py       # StepStarted/StepFinished/ToolStarted/ToolFinished
│       │   │   └── result.py       # AgentResult
│       │   ├── tools/         # 工具抽象
│       │   │   ├── base.py         # Tool / ToolResult / ToolHandler 类型
│       │   │   ├── context.py      # ToolContext（handler(args, ctx) 注入）
│       │   │   ├── registry.py     # ToolRegistry（绑定 ctx、注册/执行/转 schema）
│       │   │   └── builtin/        # 内置工具（time.py: get_time；fs.py: 文件四件套）
│       │   ├── session/       # 会话管理（多客户端共享）
│       │   │   ├── registry.py     # ConnectionSink / Session / SessionRegistry
│       │   │   └── channel.py      # SessionChannel（每连接门面）
│       │   ├── server/
│       │   │   ├── tcp.py      # TCPServer：TCP 监听、多客户端、优雅停止
│       │   │   └── session.py  # ClientSession：逐行读取、解析、分发、回写、注入 SessionChannel
│       │   ├── router/
│       │   │   ├── dispatcher.py  # Dispatcher + create_dispatcher()
│       │   │   └── context.py     # HandlerContext
│       │   ├── handlers/
│       │   │   ├── base.py        # HandlerFunc 类型 + register_handler 装饰器
│       │   │   ├── ping.py / echo.py / shutdown.py / chat.py / session.py
│       │   ├── llm/
│       │   │   ├── base.py            # LLMProvider 协议（供应商无关接口）
│       │   │   ├── anthropic_client.py # AnthropicClient（Anthropic SDK 实现）
│       │   │   ├── events.py          # Text/Thinking/ToolUse/Done 事件 + LLMStreamEvent
│       │   │   └── exceptions.py      # LLMError 异常层级
│       │   └── task/
│       │       ├── manager.py     # TaskManager（任务生命周期 + step 维度）
│       │       └── stages.py      # re-export TaskStage
│       ├── client/         # 客户端 (CLI)
│       │   ├── cli/
│       │   │   ├── __main__.py    # 入口（python -m awesome_claude.client.cli）
│       │   │   ├── app.py         # CLIApp REPL 主循环
│       │   │   ├── commands.py    # parse_command
│       │   │   └── renderer.py    # StreamRenderer（流式渲染/摘要）
│       │   └── transport/
│       │       ├── connection.py  # ClientConnection
│       │       └── receiver.py    # MessageReceiver（response Future / notification handler）
│       ├── protocol/       # 共享协议（client/core 唯一通信接口）
│       │   ├── jsonrpc.py  # JSON-RPC 2.0 消息构造/解析/校验
│       │   ├── errors.py   # 标准 + LLM 错误码、build_error_response
│       │   └── methods.py  # 方法名常量与参数/返回类型 TypedDict
│       └── shared/         # 共享工具
│           ├── types.py    # TaskStage / TaskEvent / StreamChunk / TokenUsage / ChatResponse
│           └── logging/
│               ├── app_logger.py    # structlog 配置（setup_app_logging / get_app_logger）
│               └── task_tracker.py  # TaskTracker（JSONL 写入）
│           # 注：shared/logger.py 与 shared/config.py 为遗留占位文件，未被引用
├── tests/
│   ├── conftest.py         # 共享 fixtures（server / client / RpcTestClient）
│   ├── unit/               # 单元测试（协议、server、llm、task、handlers、cli、tools…）
│   ├── integration/        # 集成测试（预留）
│   └── e2e/                # 端到端测试（test_chat.py）
├── logs/                   # 运行时日志（git 忽略）
│   ├── server.log
│   └── tasks/{date}/{task_id}.jsonl
└── docs/
    ├── architecture.md
    └── protocol.md
```
**注意，项目结构并非一层不变，随着项目迭代，项目结构也需要迭代**
## 核心概念
- **HandlerContext**：传给 handler 的运行时上下文，含 `task_manager`、`llm_client`、`sessions`（SessionChannel，多客户端会话广播）、`config`、`agent_loop`。由 ClientSession 每连接创建一次（注入绑定到该连接的 `SessionChannel`）。
- **HandlerFunc**：`Callable[[dict | None, HandlerContext], Awaitable[dict]]`。handler 返回**结果 dict**；出错时返回含 `"error"` 键的错误响应 dict（`build_error_response`），session 负责补全 `id`。
- **方法注册**：handler 用 `@register_handler(METHOD_X)` 装饰；`create_dispatcher()` 显式注册 ping/echo/shutdown/chat/session.attach/session.detach 六个方法。
- **Agent Loop**：`AgentLoop` 编排多轮 LLM + 工具调用；chat handler 委托 `agent_loop.run()`，通过 `on_event`（LLM 流式事件）与 `on_step`（step/tool 结构事件）回调转为 `chat.stream` / `chat.tool_*` 通知并记录任务阶段。
- **工具执行上下文（ToolContext）**：`ToolHandler` 签名为 `Callable[[dict, ToolContext], Awaitable[Any]]`；`ToolRegistry` 构造时绑定全局 `ToolContext`（workspace_root + fs 读写限额），执行时统一注入。后续新工具能力（命令执行策略等）直接扩展 ToolContext 字段。
- **文件工具沙箱**：内置 fs 工具的路径参数统一做 realpath 解析（含符号链接）后必须落在 `workspace_root` 内，越界抛 `PathOutsideRootError`；只处理 UTF-8 文本（二进制/含空字节拒绝）；`write_file` 不自动创建父目录、超出 `fs_max_write` 拒绝；`edit_file` 要求 `old_string` 唯一匹配；`read_file` 超出 `fs_max_read` 截断并标记。
- **多客户端会话**：`SessionRegistry` 维护 `session_id → Session`（订阅连接集合 + 对话历史）。客户端经 `session.attach` 订阅，`SessionChannel.broadcast` 在已订阅时扇出到会话内所有连接，未订阅时单播（向后兼容）。
- **任务生命周期**：`TaskManager.create_task()` 生成 8 位 UUID task_id（记录 `time.monotonic()` 起点）→ `record_stage` / `complete_task` / `fail_task`，由 `TaskTracker` 写入 JSONL。多轮 Agent Loop 下用 `step_index` 区分轮次。
- **时间基准**：所有阶段 duration 计算必须用单调时钟 `time.monotonic()`（TaskTracker 内部同样使用），禁止混用 `time.time()`。

## 编码规范
- 所有函数必须有完整的 type hints（包括返回值）
- 所有 public 函数/类必须有 docstring（Google 风格：Args / Returns / Raises）
- 异步函数优先，不阻塞事件循环；同步 I/O 尽量用 `asyncio.to_thread`
- 错误处理使用自定义异常层级（如 `LLMError`、`JsonRpcProtocolError`），不吞没异常
- 所有 JSON-RPC 错误必须返回标准 error code；LLM 错误用 -32001/-32002/-32003
- 日志使用 `shared/logging/app_logger.py` 的 `get_app_logger()`，禁止在 src/ 中 `print`（仅 CLI 渲染器允许 print）
- 每个模块顶部写明该模块的职责（一句话 docstring）
- 禁止写死敏感信息（API key 等一律走环境变量）

## 环境变量（.env）
| 变量 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `ANTHROPIC_API_KEY` | ✅ | — | Anthropic / 兼容端点 API key |
| `AWESOME_CLAUDE_HOST` | | `127.0.0.1` | 监听地址 |
| `AWESOME_CLAUDE_PORT` | | `9527` | 监听端口 |
| `AWESOME_CLAUDE_MODEL` | | `claude-sonnet-4-20250514` | 模型名（DeepSeek 用 `deepseek-chat`） |
| `AWESOME_CLAUDE_BASE_URL` | | 空 | 自定义端点（DeepSeek 用 `https://api.deepseek.com/anthropic`） |
| `AWESOME_CLAUDE_MAX_TOKENS` | | `4096` | 最大输出 token |
| `AWESOME_CLAUDE_LOG_LEVEL` | | `INFO` | 日志级别 |
| `AWESOME_CLAUDE_LOG_DIR` | | `logs` | 日志根目录 |
| `AWESOME_CLAUDE_WORKSPACE_DIR` | | 进程启动 cwd | 文件工具沙箱根目录 |
| `AWESOME_CLAUDE_FS_MAX_READ` | | `30000` | 单次读取字节上限 |
| `AWESOME_CLAUDE_FS_MAX_WRITE` | | `100000` | 单次写入字节上限 |

## 架构约束
- `core/` 和 `client/` 只能通过 `protocol/` 中定义的接口通信，不能直接互相 import
- 所有网络 I/O 必须是异步的（asyncio）
- JSON-RPC 2.0 严格遵循规范：request / response / notification / error
- 配置项从环境变量或 .env 读取，不硬编码
- 守护进程必须支持优雅退出（SIGTERM / SIGINT / shutdown 通知）

## 常用命令
```bash
# 安装依赖
uv sync

# 启动 server（需先配置 .env 中的 API key）
uv run python -m awesome_claude.core.app

# 启动 client
uv run python -m awesome_claude.client.cli --host 127.0.0.1 --port 9527

# 运行测试（全部）
uv run pytest -v
uv run pytest tests/e2e/ tests/unit/test_llm.py

# 类型检查（strict）
uv run mypy src/

# Lint & Format
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

## 阶段规划
- ✅ Phase 1：最小骨架 — client → core → response 完整链路（ping / echo / shutdown）
- ✅ Phase 2：集成 LLM — 流式对话、任务生命周期追踪、结构化日志
- 🔶 Phase 3：Tool Use — Agent Loop + ToolContext/工具沙箱已落地，内置 get_time 与文件四件套，待命令执行等扩展
- ⬜ Phase 4：Memory — 会话历史管理与压缩
- ⬜ Phase 5：Planning — 多步任务规划与执行
- ⬜ Phase 6：TUI/Web — 扩展客户端形态
