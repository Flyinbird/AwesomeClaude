## 1. 协议与类型

- [x] 1.1 在 `protocol/methods.py` 新增 `NOTIFY_CHAT_COMPLETED` / `NOTIFY_CHAT_FAILED` / `NOTIFY_CHAT_HEARTBEAT` 常量及 `ChatAcceptedResult`、`ChatCompletedNotificationParams`、`ChatFailedNotificationParams`、`ChatHeartbeatNotificationParams` TypedDict，更新 `__all__`；`uv run mypy src/` 通过
- [x] 1.2 更新 `docs/protocol.md` 的 chat 章节：受理 ack、`chat.completed` / `chat.failed` / `chat.heartbeat` 定义与流程，并说明最终结果不再走请求响应

## 2. 服务端

- [x] 2.1 `core/config.py` 新增 `heartbeat_interval: float = 15.0` 与 `AWESOME_CLAUDE_HEARTBEAT_INTERVAL` 解析（新增 `_env_float`）；单元测试覆盖默认值与覆盖值
- [x] 2.2 `core/server/session.py::_start_chat` 在 Run 创建成功后回受理 ack（含 `run_id`、`heartbeat_interval_ms`），再 spawn 任务；非法参数与 `-32004` 仍走 error response
- [x] 2.3 `core/server/session.py::_execute_chat` 对 chat 不再回响应，按 dispatch 结果广播 `chat.completed` 或 `chat.failed`（异常兜底路径亦为 `chat.failed`）
- [x] 2.4 实现 per-Run `_heartbeat_loop`：命名会话走 `broadcast_to`、临时会话走 `run.initiator.sink`；`finally` 先取消心跳再发终态；验证终态后无残留心跳、临时会话心跳可达

## 3. 客户端

- [x] 3.1 `client/transport/receiver.py` 增加 `last_activity` 与 `on_close` 回调，`_read_loop` 在每条解码消息后更新活动时间、结束时触发 close 回调；单元测试覆盖
- [x] 3.2 `client/transport/connection.py` 透传 `on_disconnect` / `last_activity`，控制类请求保留 60s 硬超时
- [x] 3.3 `client/cli/app.py` 注册 `chat.completed` / `chat.failed` / `chat.heartbeat` 与连接关闭处理器；`_send_chat` 改为 ack → Event 等待、无网络 deadline
- [x] 3.4 `client/cli/app.py` 实现心跳看门狗（阈值 3× `heartbeat_interval_ms`）：超时提示「连接疑似中断」并结束本轮，不主动断开连接；`chat.stream` 的 `is_final` 仅负责换行

## 4. 测试

- [x] 4.1 `tests/conftest.py` 新增 chat 调用辅助（发请求 → 等 ack → 读 `chat.completed` / `chat.failed`），供各测试复用
- [x] 4.2 改造 `tests/unit/test_e2e.py` 与 `tests/unit/test_connection_lifecycle.py` 中依赖 chat 最终响应的断言，`uv run pytest tests/unit -q` 通过
- [x] 4.3 改造 `tests/e2e/test_chat.py`、`test_session.py`、`test_run_lifecycle.py`、`test_task_planning.py`，`uv run pytest tests/e2e -q` 通过
- [x] 4.4 新增测试：受理 ack 字段、`chat.completed`、`chat.failed`、心跳周期出现、临时会话心跳单播、客户端看门狗超时提示

## 5. 文档与全量校验

- [x] 5.1 `AGENTS.md` 环境变量表补 `AWESOME_CLAUDE_HEARTBEAT_INTERVAL`，并更新协议/阶段描述
- [x] 5.2 运行 `uv run ruff check src/ tests/`、`uv run ruff format src/ tests/`、`uv run mypy src/`、`uv run pytest -v` 全部通过
