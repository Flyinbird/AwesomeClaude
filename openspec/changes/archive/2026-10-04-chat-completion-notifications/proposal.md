## Why

客户端把 `chat` 当普通请求，用固定 60s 硬超时等待最终响应；服务端即使在持续推送 `chat.stream` / `chat.tool_*` 通知，也不会重置该定时器。于是只要一次对话超过 60s，客户端就会误判超时、当作连接断开并退出，进而触发服务端取消一个本可正常完成的 Run，导致长任务大量失败。

## What Changes

- **BREAKING** `chat` 请求改为「受理语义」：服务端在 Run 创建成功后立即回 ack（含 `run_id`、`heartbeat_interval_ms`），最终结果不再通过该请求的响应返回。
- 新增终态通知：`chat.completed`（携带完整摘要与用量）与 `chat.failed`（携带错误码与信息），替代原先的最终响应。
- 新增服务端心跳通知 `chat.heartbeat`：Run 执行期间周期性推送，作为「服务端仍存活」的证明。
- 服务端新增 `heartbeat_interval` 配置（env `AWESOME_CLAUDE_HEARTBEAT_INTERVAL`，默认 15s）。
- 客户端不再对对话等待施加网络 deadline：拿到 ack 后等待终态通知；新增心跳看门狗（阈值 3× 心跳间隔），仅在长时间无任何入站数据时提示连接疑似中断，且不再因一次请求而直接断开连接。
- 立即校验错误（缺少 `message`）与会话忙 `-32004` 仍以 `chat` 请求的错误响应返回。

## Capabilities

### New Capabilities
- `chat-completion`: 对话的异步完成契约——受理 ack、完成/失败终态通知、服务端心跳与客户端看门狗语义。

### Modified Capabilities
<!-- 无：现有 connection-lifecycle / session-run-lifecycle 的需求语义不变 -->

## Impact

- 协议：`protocol/methods.py`（新增通知常量与类型）、`docs/protocol.md`
- 服务端：`core/config.py`、`core/server/session.py`（ack、终态通知、心跳任务）
- 客户端：`client/transport/receiver.py`、`client/transport/connection.py`、`client/cli/app.py`
- 测试：`tests/unit/test_e2e.py`、`tests/unit/test_connection_lifecycle.py`、`tests/e2e/test_chat.py`、`test_session.py`、`test_run_lifecycle.py`、`test_task_planning.py`，以及 `tests/conftest.py` 辅助
- 文档：`AGENTS.md` 环境变量表
