# AwesomeClaude

类似 Claude Code 的 AI Agent 框架，采用 Client-Server 架构。Core 作为常驻守护进程（daemon），通过 TCP Socket + JSON-RPC 2.0 协议接收 Client 命令并执行响应。

## 项目简介

- **架构**：Client（CLI）↔ Core（守护进程），通过 `protocol/` 共享的消息层通信
- **通信协议**：TCP Socket + JSON-RPC 2.0（每消息一行 JSON，`\n` 分帧）
- **技术栈**：Python 3.12+ / asyncio / uv / pytest / mypy (strict) / ruff

## 快速开始

### 安装依赖

```bash
uv sync
```

### 启动 Core Server

```bash
uv run python -m awesome_claude.core.server
```

默认监听 `127.0.0.1:9527`，可用环境变量覆盖（前缀 `AWESOME_CLAUDE_`）：

```bash
AWESOME_CLAUDE_PORT=9528 AWESOME_CLAUDE_LOG_LEVEL=DEBUG uv run python -m awesome_claude.core.server
```

### 启动 Client

```bash
uv run python -m awesome_claude.client.cli
```

可用命令行参数：

```bash
uv run python -m awesome_claude.client.cli --host 127.0.0.1 --port 9528
```

## Phase 1 功能

- JSON-RPC 2.0 消息层：request / response / error / notification 的构造、解析、校验
- `ping`：健康检查，返回状态与 ISO 时间戳
- `echo`：回显测试，原样返回 `message`
- `shutdown`：通知服务端优雅退出（notification）
- 多客户端并发连接，单连接独立 coroutine 处理
- 服务端支持 SIGTERM / SIGINT 优雅退出

## 项目结构

```
awesome-claude/
├── pyproject.toml          # 项目配置（依赖、pytest、工具配置）
├── AGENTS.md               # 项目规范
├── README.md
├── src/
│   └── awesome_claude/
│       ├── core/           # 守护进程 (Server)
│       │   ├── server.py   # TCP Server 主循环
│       │   ├── router.py   # JSON-RPC 请求路由与方法注册
│       │   ├── handler.py  # 业务处理器（ping / echo / shutdown）
│       │   └── config.py   # 服务端配置（环境变量）
│       ├── client/         # 客户端 (CLI)
│       │   ├── cli.py      # 命令行入口与交互循环
│       │   └── connection.py  # TCP 客户端连接管理
│       ├── protocol/       # 共享协议定义（client/core 唯一通信接口）
│       │   ├── jsonrpc.py  # JSON-RPC 2.0 消息构造/解析/校验
│       │   └── methods.py  # 方法名常量与参数/返回类型定义
│       └── shared/         # 共享工具
│           └── logger.py   # 统一日志配置
├── tests/                  # 单元 + 集成 + 端到端测试
│   ├── test_jsonrpc.py     # 协议层测试
│   ├── test_server.py      # 服务端测试
│   ├── test_client.py      # 客户端测试
│   └── test_e2e.py         # 端到端集成测试
└── docs/
    └── architecture.md     # 架构文档
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
- ⬜ Phase 2：集成 LLM — Core 调用 LLM API 实现真实对话
- ⬜ Phase 3：Tool Use — 注册和执行工具（文件读写、命令执行）
- ⬜ Phase 4：Memory — 会话历史管理与压缩
- ⬜ Phase 5：Planning — 多步任务规划与执行
- ⬜ Phase 6：TUI/Web — 扩展客户端形态
