## Context

见 `proposal.md - Why`。当前 `chat` 走 `ClientConnection.send_request` → `MessageReceiver.wait_for_response`，后者用 `asyncio.wait_for(future, timeout=60.0)` 施加**总时长**超时；通知由读循环另行分发，不参与该定时器。服务端 `ClientSession._start_chat` 把 chat 作为异步 Run 执行，读循环保持解耦，最终由 `_execute_chat` 回写响应。约束：`core/` 与 `client/` 只能经 `protocol/` 通信；网络 I/O 全异步；超时/时长一律用 `time.monotonic()`；配置经环境变量。

## Goals / Non-Goals

**Goals:**
- 对话终态与服务端存活感知不再依赖固定总时长。
- 保留即时校验（非法参数、会话忙）的同步错误语义。
- 保持多客户端会话内广播与临时会话单播的既有可见性。

**Non-Goals:**
- 不引入客户端自动重连或断线续传。
- 不做对话历史压缩/记忆（Phase 4）。
- 不改动 `chat.stream` / `chat.tool_*` / `chat.plan_updated` / `chat.interrupted` 的既有语义。
- 不改变 ping/echo/session.*/shutdown 的请求-响应模式。

## Decisions

### 1. chat 保留为 JSON-RPC request，回「受理 ack」而非 notification
服务端在 Run 创建成功后立即用该请求的 id 回响应 `{run_id, accepted, heartbeat_interval_ms}`；终态另发通知。
- 原因：JSON-RPC 规定一个请求只能有一个响应。保留 request 让缺参、`-32004` 等即时错误仍能以标准 error response 返回，避免把它们也改成通知。
- 备选：把 chat 改成 notification 并让客户端自带关联 id——需要自造 id 与错误投递通道，改动更大且丢失标准错误语义，故不采用。

### 2. 终态投递下沉到 ClientSession 层，handler 返回值语义不变
`handle_chat` 仍返回结果 dict；`_execute_chat` 判断结果中是否有 `error`，分别广播 `chat.completed` / `chat.failed`。
- 原因：投递是传输关注点，集中在 session 层可保证「同一 request id 绝不二次响应」，也便于覆盖异常兜底路径。
- 备选：由 `handle_chat` 直接发终态通知——会把传输细节混入业务 handler，且 `_execute_chat` 仍需抑制响应，职责更乱。

### 3. 心跳为 per-Run 后台任务，按会话可见性投递
`_execute_chat` 启动一个 `_heartbeat_loop`，每 `heartbeat_interval` 推送一次；命名会话用 `SessionChannel.broadcast_to`，临时会话直接走 `run.initiator.sink`；在 `finally` 中先取消心跳再发终态。
- 原因：临时会话的 sink 未 attach 到会话集合，`broadcast_to` 无法触达，必须走 initiator sink；先取消后发终态可避免心跳与终态竞态。
- 备选：全局心跳服务遍历活跃 Run——同样受临时会话 sink 不可达问题影响，且需额外的全局生命周期，故不采用。

### 4. 心跳间隔配置化，并随 ack 下发
`ServerConfig.heartbeat_interval`（env `AWESOME_CLAUDE_HEARTBEAT_INTERVAL`，默认 15s）；ack 携带其毫秒值，客户端据此推算看门狗阈值（3×）。
- 原因：避免客户端硬编码与服务端不一致；单一事实来源在服务端。
- 备选：客户端独立配置——两端配置易漂移，不采用。

### 5. 客户端等待模型：ack → Event，无网络 deadline；看门狗负责存活
`_send_chat` 拿到 ack 后注册/复用 completion Event 并阻塞，由 `chat.completed` / `chat.failed` 处理器 set；watchdog 周期检查入站活动时间戳。
- 原因：终态到达由通知驱动，天然无总时长限制；看门狗只在真正静默时告警，避免静默的长 LLM 调用被误判。
- 备选：给等待设更长硬超时——只是把问题推远，仍会在超长任务上复现。

### 6. 超时不再等价于断连
客户端看门狗触发时仅提示并结束当前等待，不主动关闭连接；控制类请求继续沿用 `receiver` 的 60s 硬超时（ack 即时返回，不受影响）。
- 原因：一次请求级故障不应升级为连接级故障（旧行为会进而取消服务端健康的 Run）。

## Risks / Trade-offs

- [同一 request id 二次响应] → 由 session 层统一投递终态，且明确 chat 走 notification；测试覆盖成功、失败、异常兜底三条路径。
- [心跳与终态竞态] → `finally` 中先 cancel 心跳任务并 await 其结束，再广播终态。
- [临时会话心跳不可达] → 明确使用 `run.initiator.sink` 单播，并在测试中覆盖无 session_id 场景。
- [看门狗误报] → 阈值取 3× 心跳间隔，且以「任何入站消息」为活动信号；正常心跳下不触发。
- [测试改造面大] → 在 `tests/conftest.py` 增加统一的 chat 调用辅助（发请求→等 ack→读终态通知），降低各测试重写成本。
- [心跳在极快 Run 中有噪声] → 默认 15s，短任务通常不会产生心跳；接受该低频噪声。

## Migration Plan

破坏性协议变更，但当前只有自带 CLI 消费，无外部兼容负担。服务端与客户端必须同版本升级；升级顺序无强依赖（旧客户端无法解析新 chat 响应，但本项目按整体交付）。
