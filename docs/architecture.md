# 架构文档

## 架构概述

AwesomeClaude 采用 **Client-Server 架构**：`client/`（命令行 CLI）与 `core/`（常驻守护进程）通过 TCP Socket 传输 JSON-RPC 2.0 消息通信，两者只能依赖 `protocol/` 包中定义的共享协议（消息编解码、方法名与参数/返回类型、错误码），不能互相直接 import。客户端负责命令解析、消息发送与响应渲染；核心服务端负责 TCP 监听、请求路由、业务处理器分发（Phase 1：ping/echo/shutdown，Phase 2：集成 Anthropic LLM 实现流式 chat），并通过 `task/` 跟踪任务生命周期、`shared/logging/` 输出结构化日志。所有网络 I/O 基于 asyncio，支持多客户端并发与 SIGTERM/SIGINT 优雅退出。

## 目标模块结构

```
src/awesome_claude/
├── protocol/            # 共享协议（client/core 唯一通信接口）
│   ├── jsonrpc.py       # JSON-RPC 2.0 消息构造/解析/校验
│   ├── methods.py       # 方法名常量与参数/返回类型定义
│   └── errors.py        # 协议层自定义异常层级
├── core/                # 守护进程 (Server)
│   ├── server/          # TCP 传输层与会话管理
│   │   ├── tcp.py       # asyncio TCP 服务器主循环
│   │   └── session.py   # 连接会话状态跟踪
│   ├── router/          # 请求路由
│   │   ├── dispatcher.py  # 方法分发器
│   │   └── context.py     # 请求上下文
│   ├── handlers/        # 业务处理器
│   │   ├── base.py      # 处理器基类
│   │   ├── ping.py      # 健康检查
│   │   ├── echo.py      # 回显
│   │   ├── chat.py      # LLM 对话（Phase 2）
│   │   └── shutdown.py  # 服务端关闭
│   ├── llm/             # LLM 集成
│   │   ├── client.py    # Anthropic SDK 封装（流式）
│   │   ├── events.py    # 流式事件定义
│   │   └── exceptions.py
│   ├── task/            # 任务生命周期
│   │   ├── manager.py   # 任务管理器
│   │   └── stages.py    # 任务阶段状态机
│   ├── app.py           # 应用装配
│   └── config.py        # 服务端配置
├── client/              # 客户端 (CLI)
│   ├── cli/             # 交互式 REPL
│   │   ├── app.py       # 入口与主循环
│   │   ├── commands.py  # 命令定义
│   │   └── renderer.py  # 响应/流式渲染
│   └── transport/       # 客户端传输层
│       ├── connection.py  # TCP 连接管理
│       └── receiver.py    # 消息接收解析
└── shared/              # 共享工具
    ├── types.py         # 共享类型定义
    ├── config.py        # 环境变量加载
    └── logging/         # 结构化日志
        ├── app_logger.py    # structlog 配置
        └── task_tracker.py  # 任务日志追踪
```

## 数据流向

1. **请求**：CLI 命令 → `client/transport` 构造 JSON-RPC 请求（id 自增）→ `\n` 分帧 TCP 发送 → `core/server/tcp` 按行读取 → `protocol/jsonrpc` 解码校验 → `core/router/dispatcher` 分发到对应 handler。
2. **响应**：handler 返回结果 → dispatcher 包装为 response/error → TCP 回传 → 客户端渲染展示（notification 请求不回响应）。
3. **流式 chat（Phase 2）**：handler 从 `core/llm/client` 获取 Anthropic 流式事件，边生成边经 `core/task` 更新任务阶段，并由 `shared/logging/task_tracker` 记录结构化日志。
4. **关闭**：`/quit` → shutdown 通知 → 服务端停止监听、取消连接、优雅退出（SIGTERM/SIGINT 同路径）。
