# 架构文档

## 架构图

```
                          TCP Socket / JSON-RPC 2.0
                     （每消息一行 JSON，\n 分帧）

+---------------------+      请求 / 通知           +---------------------+
|   Client (CLI)      |  --------------------->   |   Core (Daemon)     |
|                     |                            |                     |
|  client/cli.py      |                            |  core/server.py     |
|  · REPL 命令解析    |                            |  · TCP 主循环       |
|  · 响应展示         |                            |  · 多客户端并发     |
|  client/connection.py|                           |  · 优雅退出         |
|  · TCP 连接管理     |                            |  core/router.py     |
|  · 请求 id 自增     |                            |  · 方法分发         |
|                     |      响应（成功/错误）      |  core/handler.py    |
|                     |  <---------------------   |  · ping/echo/shutdown|
|                     |                            |  core/config.py     |
|                     |                            |  · 环境变量配置     |
+----------+----------+                            +----------+----------+
           |                                                  |
           |    protocol/jsonrpc.py      · 消息构造/解析/校验   |
           |    protocol/methods.py      · 方法名/参数/返回类型  |
           +------------------+          +---------------------+
                              |          |
                              +----------+
                     shared/logger.py · 统一日志
```

## 各模块职责

| 模块 | 职责 |
| --- | --- |
| `protocol/jsonrpc.py` | JSON-RPC 2.0 消息层：request / response / error / notification 的构造、解析、校验；字节编解码（UTF-8 + `\n` 分帧）；标准错误码常量 |
| `protocol/methods.py` | Phase 1 方法名常量与参数/返回类型的 TypedDict 定义，供 client 与 core 共用保证类型安全 |
| `core/server.py` | TCP Server 主循环：`asyncio.start_server` 监听，每连接独立 coroutine，按行读取并分发，SIGTERM/SIGINT 优雅退出 |
| `core/router.py` | 方法注册表与分发器：根据 `method` 字段路由到 handler，未注册方法返回 METHOD_NOT_FOUND，handler 异常返回 INTERNAL_ERROR |
| `core/handler.py` | 业务处理器：`handle_ping` / `handle_echo` / `handle_shutdown` |
| `core/config.py` | 服务端配置（host / port / log_level），从 `AWESOME_CLAUDE_*` 环境变量读取 |
| `client/cli.py` | 交互式 REPL：`/ping`、`/echo`、`/quit`、其他文本回显 |
| `client/connection.py` | 客户端连接：连接管理、请求 id 自增、串行收发、断连异常处理 |
| `shared/logger.py` | 统一日志配置（前缀 `awesome_claude.*`） |

## 数据流向

### 请求链路（Client → Core）

```
cli.py 读取命令
  → connection.send_request(method, params)
  → protocol.jsonrpc.build_request() 构造请求（id 自增）
  → encode_message() 序列化为 JSON 字节 + \n
  → TCP 发送
  → core/server.py readline() 读取一行
  → decode_message() 反序列化 → parse_message() 校验并识别类型
  → router.route() 按 method 分发到 handler
  → handler 执行并返回结果
```

### 响应链路（Core → Client）

```
handler 返回结果
  → router 包装为 build_response() / build_error()
  → encode_message() 序列化
  → TCP 发送（notification 请求不发送响应）
  → client connection readline() 读取
  → decode_message() 反序列化
  → cli.py format_response() 展示
```

### 关闭链路

```
cli 输入 /quit → connection.send_notification(shutdown)
  → core 收到 notification → handler.handle_shutdown()
  → server.shutdown() 设置停止事件
  → server.stop()：取消连接 handler、关闭所有连接与监听 socket
  → run() 返回，进程退出（SIGTERM/SIGINT 同路径）
```

## 架构约束

- `core/` 与 `client/` 只能通过 `protocol/` 中定义的接口通信，不能直接互相 import
- 所有网络 I/O 均使用 asyncio，不阻塞事件循环
- 消息严格遵循 JSON-RPC 2.0 规范（request / response / notification / error，标准错误码 -32700 ~ -32000）
- 配置从环境变量读取，不硬编码
- 守护进程支持优雅退出（SIGTERM / SIGINT）
