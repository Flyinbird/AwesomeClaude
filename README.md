# AwesomeClaude

类似 Claude Code 的 AI Agent 框架，采用 Client-Server 架构。Core 作为常驻守护进程（daemon），通过 TCP Socket + JSON-RPC 2.0 协议接收 Client 命令并执行响应。

## 项目简介

- **架构**：Client（CLI）↔ Core（守护进程），通过 `protocol/` 共享的消息层通信
- **通信协议**：TCP Socket + JSON-RPC 2.0（每消息一行 JSON，`\n` 分帧）
- **技术栈**：Python 3.12+ / asyncio / uv / pytest / mypy (strict) / ruff / structlog
- **LLM**：基于 Anthropic SDK 的流式调用（可对接 Anthropic 官方或 DeepSeek 等 Anthropic 兼容端点），通过供应商无关的 `LLMProvider` 协议抽象

## 快速开始

### 安装依赖

```bash
uv sync
cp .env.example .env   # 然后编辑 .env 填入 API key
```

### 启动 Core Server

```bash
uv run python -m awesome_claude.core.app
```

默认监听 `127.0.0.1:9527`，配置从 `.env` 或环境变量读取（见下文）。

### 启动 Client

```bash
uv run python -m awesome_claude.client.cli
```

可用命令行参数：

```bash
uv run python -m awesome_claude.client.cli --host 127.0.0.1 --port 9528
uv run python -m awesome_claude.client.cli --session my-session  # 指定共享会话 ID
```

多个客户端指定同一 `--session` 即可共享会话、实时同步对话；不指定则各自使用新会话。

启动后直接输入文本即可与 LLM 对话（流式逐块输出）；输入 `/help` 查看命令。

## 环境变量配置（.env）

| 变量 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `ANTHROPIC_API_KEY` | ✅ | — | Anthropic（或兼容端点）API key |
| `AWESOME_CLAUDE_HOST` | | `127.0.0.1` | 服务端监听地址 |
| `AWESOME_CLAUDE_PORT` | | `9527` | 服务端监听端口 |
| `AWESOME_CLAUDE_MODEL` | | `claude-sonnet-4-20250514` | 模型名（DeepSeek 用 `deepseek-chat`） |
| `AWESOME_CLAUDE_BASE_URL` | | 空 | 自定义 API 基地址（如 DeepSeek 的 `https://api.deepseek.com/anthropic`） |
| `AWESOME_CLAUDE_MAX_TOKENS` | | `4096` | 最大输出 token 数 |
| `AWESOME_CLAUDE_LOG_LEVEL` | | `INFO` | 日志级别 |
| `AWESOME_CLAUDE_LOG_DIR` | | `logs` | 日志根目录 |

### 使用 DeepSeek

在 `.env` 中配置：

```bash
ANTHROPIC_API_KEY=sk-xxx-your-deepseek-key
AWESOME_CLAUDE_MODEL=deepseek-chat
AWESOME_CLAUDE_BASE_URL=https://api.deepseek.com/anthropic
```

## Phase 2 功能

- **LLM 流式对话**：输入文本后文字逐块出现在终端（`chat.stream` 通知实时推送）
- **任务生命周期追踪**：每次对话生成唯一 `task_id`，完整生命周期（创建→上下文→LLM 请求→流式→完成/失败）写入 JSONL
- **结构化日志**：stdout 彩色文本 + `logs/server.log` JSON 双通道输出
- **token 用量统计**：每次对话结束后显示 input/output tokens 与耗时；`/stats` 显示会话累计用量
- **错误映射**：认证 / 超时 / 限流 / 内容过滤等 LLM 错误映射为对应 JSON-RPC 错误码，失败任务记录 `TASK_FAILED`
- 保留 Phase 1 能力：`/ping`、`/echo`、`/quit`、多客户端并发、优雅退出

## Phase 3（部分）功能

- **Agent Loop**：`AgentLoop` 编排多轮 LLM 调用与工具调用，支持多步任务
- **工具调用**：`ToolRegistry` 注册与执行工具（内置 `get_time`），工具结果回填 LLM 继续推理
- **任务 step 化**：任务日志按 `step_index` 区分多轮，含 `step_started` / `tool_started` / `tool_failed` 等阶段
- **多客户端会话**：多个 CLI/TUI 客户端经 `session.attach` 共享同一会话，实时广播 `chat.stream` / `chat.tool_*` 通知，晚加入客户端回放历史（`--session` 参数指定会话）

## 日志系统

```
logs/
├── server.log          # 服务端结构化日志（JSON，每行一条）
└── tasks/
    └── YYYY-MM-DD/
        └── {task_id}.jsonl   # 每个任务的完整生命周期事件（JSONL）
```

任务 JSONL 每行一个事件，字段：`task_id`、`stage`、`timestamp`、`duration_ms`、`data`、`step_index`（多轮 Agent Loop 时区分轮次）。

顶层阶段序列：`task_created` → `context_built` → `task_completed`（或 `task_failed`）。

每个 agent step（一次 LLM 调用）内：`step_started` → `llm_request_sent` → `llm_streaming` → `llm_response_done`，并可能伴随 `tool_started` → `tool_completed` / `tool_failed`。

## 项目结构

```
awesome-claude/
├── pyproject.toml          # 项目配置（依赖、pytest、工具配置）
├── AGENTS.md               # 项目规范
├── .env.example            # 环境变量模板
├── src/
│   └── awesome_claude/
│       ├── core/           # 守护进程 (Server)
│       │   ├── server/
│       │   │   ├── tcp.py      # TCP 服务器主循环
│       │   │   └── session.py  # 客户端会话
│       │   ├── router/
│       │   │   ├── dispatcher.py  # 方法分发器
│       │   │   └── context.py     # 请求上下文
│       │   ├── agent/        # Agent 运行时
│       │   │   ├── loop.py      # AgentLoop：多轮 LLM + 工具编排
│       │   │   └── events.py    # step/tool 结构事件
│       │   ├── tools/        # 工具抽象（base/registry/builtin）
│       │   ├── session/      # 会话管理（多客户端共享）
│       │   ├── handlers/     # 业务处理器（ping/echo/shutdown/chat/session）
│       │   ├── llm/          # LLM 客户端（LLMProvider 协议 + Anthropic 实现）
│       │   ├── task/         # 任务生命周期管理（step 化）
│       │   ├── app.py        # 应用装配与启动
│       │   └── config.py     # 服务端配置（环境变量）
│       ├── client/         # 客户端 (CLI)
│       │   ├── cli/         # 交互式 REPL（app/commands/renderer）
│       │   └── transport/   # 传输层（connection/receiver，支持流式通知）
│       ├── protocol/       # 共享协议定义（client/core 唯一通信接口）
│       │   ├── jsonrpc.py  # JSON-RPC 2.0 消息构造/解析/校验
│       │   ├── errors.py   # 错误码定义
│       │   └── methods.py  # 方法名常量与参数/返回类型定义
│       └── shared/         # 共享工具
│           ├── types.py    # 共享数据类型（任务/流式/用量）
│           └── logging/    # 结构化日志（app_logger/task_tracker）
├── tests/
│   ├── unit/               # 单元测试
│   ├── integration/        # 集成测试
│   ├── e2e/                # 端到端测试
│   └── conftest.py         # 共享 fixtures
├── logs/                   # 运行时日志（git 忽略）
└── docs/
    ├── architecture.md     # 架构文档
    └── protocol.md         # JSON-RPC 协议文档
```

## 开发指南

```bash
# 运行测试
uv run pytest -v

# 类型检查（strict）
uv run mypy src/

# Lint
uv run ruff check src/ tests/

# 格式化
uv run ruff format src/ tests/
```

## 阶段规划

- ✅ Phase 1：最小骨架 — client → core → response 完整链路（ping / echo / shutdown）
- ✅ Phase 2：集成 LLM — 流式对话、任务生命周期追踪、结构化日志
- 🔶 Phase 3：Tool Use — Agent Loop 骨架 + get_time 内置工具已落地，待完整工具集（文件读写、命令执行）
- ⬜ Phase 4：Memory — 会话历史管理与压缩
- ⬜ Phase 5：Planning — 多步任务规划与执行
- ⬜ Phase 6：TUI/Web — 扩展客户端形态
