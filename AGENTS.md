# AwesomeClaude — 项目规范

## 项目概述
AwesomeClaude 是一个类似 Claude Code 的 AI Agent 框架，采用 Client-Server 架构。
Core 作为常驻守护进程（daemon），通过 TCP Socket + JSON-RPC 2.0 协议接收 Client 命令并执行响应。
支持对话、文件读写、命令执行，未来将扩展 Tool Use、Memory Compression、Planning 等能力。

## 技术栈
- 语言：Python 3.12+
- 包管理：uv
- 通信协议：TCP Socket + JSON-RPC 2.0
- 异步框架：asyncio（标准库）
- 测试：pytest + pytest-asyncio
- 类型检查：mypy（strict mode）
- Lint：ruff
- 格式化：ruff format

## 项目结构
awesome-claude/
├── pyproject.toml
├── uv.lock
├── AGENTS.md
├── README.md
├── src/
│ └── awesome_claude/
│ ├── init.py
│ ├── core/ # 守护进程 (Server)
│ │ ├── init.py
│ │ ├── server.py # TCP Server 主循环
│ │ ├── router.py # JSON-RPC 请求路由与方法注册
│ │ ├── handler.py # 具体业务处理器
│ │ └── config.py # 服务端配置（host, port, 日志等）
│ ├── client/ # 客户端 (CLI)
│ │ ├── init.py
│ │ ├── cli.py # 命令行入口与交互循环
│ │ └── connection.py # TCP 客户端连接管理
│ ├── protocol/ # 共享协议定义
│ │ ├── init.py
│ │ ├── jsonrpc.py # JSON-RPC 2.0 消息构造/解析/校验
│ │ └── methods.py # 方法名常量与参数/返回类型定义
│ └── shared/ # 共享工具
│ ├── init.py
│ └── logger.py # 统一日志配置
├── tests/
│ ├── init.py
│ ├── test_jsonrpc.py
│ ├── test_server.py
│ └── test_client.py
└── docs/
└── architecture.md # 架构文档


## 编码规范
- 所有函数必须有完整的 type hints（包括返回值）
- 所有 public 函数/类必须有 docstring（Google 风格）
- 异步函数优先，不阻塞事件循环
- 错误处理使用自定义异常层级，不吞没异常
- 所有 JSON-RPC 错误必须返回标准 error code（-32700 到 -32000）
- 日志使用 structlog 或标准 logging，禁止 print 调试
- 每个模块顶部写明该模块的职责（一句话）

## 架构约束
- core/ 和 client/ 只能通过 protocol/ 中定义的接口通信，不能直接互相 import
- 所有网络 I/O 必须是异步的（asyncio）
- JSON-RPC 2.0 严格遵循规范：request / response / notification / error
- 配置项从环境变量或配置文件读取，不硬编码
- 守护进程必须支持优雅退出（SIGTERM / SIGINT）

## 阶段规划
- Phase 1：最小骨架 — 跑通 client → core → response 完整链路（echo 模式）
- Phase 2：集成 LLM — Core 调用 LLM API 实现真实对话
- Phase 3：Tool Use — 注册和执行工具（文件读写、命令执行）
- Phase 4：Memory — 会话历史管理与压缩
- Phase 5：Planning — 多步任务规划与执行
- Phase 6：TUI/Web — 扩展客户端形态

## 常用命令
```bash
# 安装依赖
uv sync

# 运行 server
uv run python -m awesome_claude.core.server

# 运行 client
uv run python -m awesome_claude.client.cli

# 运行测试
uv run pytest -v

# 类型检查
uv run mypy src/

# Lint & Format
uv run ruff check src/ tests/
uv run ruff format src/ tests/