## Context

当前权限体系由 `PermissionPolicy`（进程级规则求值）、`PermissionManager`（Run 作用域编排）与 `PermissionBroker`（审批通道协议）三层构成。`PermissionBroker` 是一个 Protocol，当前仅有 `NonInteractiveBroker` 实现——对所有询问一律返回拒绝。`ToolRegistry.execute()` 在存在 `scope.permissions` 时调用 `authorize()`，后者在 `ASK` 时委托 broker。

服务端通过 TCP + JSON-RPC 2.0 与 CLI 客户端通信，已具备 notification 扇出能力（`SessionChannel.broadcast` / `broadcast_to`）和 accept-then-notify 的 chat 模型。客户端在等待 chat 终态期间主循环挂在 `completion_event.wait()` 上，stdin 处于空闲状态，可直接用于审批输入。

参见 `proposal.md` 了解动机与范围。

## Goals / Non-Goals

**Goals:**
- 将 `ASK` 从默认拒绝升级为可向客户端请求交互式审批。
- 审批通知广播给会话内所有订阅客户端，先响应者获胜。
- 审批超时后 fail-closed（按拒绝处理），超时时间可配置。
- Run 取消/失败时清理 pending 审批，防止内存泄漏和无限阻塞。
- CLI 提供极简审批交互（print + y/n 输入）。
- 补充 `core/permissions/README.md` 文档。

**Non-Goals:**
- 不实现审批记忆（如 "always allow this tool"），MVP 仅支持单次审批。
- 不实现 TUI 或 Web 审批界面，CLI 仅文本交互。
- 不新增 JSON-RPC 错误码表示审批超时，超时以普通 tool_result 错误文本回填。
- 不限制审批通知仅发送给 chat 发起者（先响应者获胜是设计意图）。

## Decisions

### 1. `InteractiveBroker` 实现为内存中的 Future 表

**方案**：`InteractiveBroker` 内部维护 `dict[str, asyncio.Future]`，key 为 `request_id`。`ask()` 创建 Future 并注册，同时通过注入的 `SessionChannel` 广播通知；`respond()` 按 `request_id` 查找 Future 并设置结果；超时由 `asyncio.wait_for` 控制。

**替代方案**：使用外部消息队列（如 Redis Stream）或 asyncio Queue。

**选择理由**：审批请求与响应发生在同一个 Python 进程的同一次事件循环内，无需跨进程持久化。Future 表是最轻量、延迟最低的方案，且天然支持 `cancel()` 清理。

### 2. `request_id` 使用短 UUID（8 位 hex）

**方案**：`uuid.uuid4().hex[:8]`，绑定到 `run_id` 上下文。

**替代方案**：全局递增整数、完整 UUID。

**选择理由**：短 ID 在 CLI 终端中更易读，便于用户确认。8 位 hex 在单次 Run 内冲突概率极低（16^8 = 4.3e9），即使跨 Run 重复也因 Future 表按 `request_id` 独立查找而不影响正确性。

### 3. 审批通知广播给会话内所有订阅者

**方案**：`SessionChannel.broadcast_to(session_id, ...)` 扇出。

**替代方案**：仅发送给 chat 发起连接。

**选择理由**：项目已支持多客户端共享会话（多窗口 / 多终端），广播保证发起者断连后其他客户端仍能响应。先响应者获胜的语义简单且符合 fail-soft 原则。

### 4. CLI 审批交互在 notification handler 内直接阻塞输入

**方案**：`_handle_permission_requested` 中直接 `await asyncio.to_thread(input, "...")`，然后发送 `permission.respond`。

**替代方案**：在 `_wait_for_completion()` 中用 `asyncio.wait` 同时监听 `completion_event` 和审批事件队列。

**选择理由**：AgentLoop 在等待审批时不会推进，Run 不可能完成，因此不存在与正常 `input("> ")` 的竞态。直接阻塞输入是最简单的极简实现。需要处理的边界情况是：Run 在等待审批期间被外部取消，此时 `input()` 仍在阻塞，用户输入后服务端会返回 "请求已过期"——这是可接受的降级行为。

### 5. Run 取消时的清理由 `InteractiveBroker.cancel_for_run()` 提供

**方案**：`InteractiveBroker` 暴露 `cancel_for_run(run_id: str)` 方法，由 `chat.py` handler 的 `finally` 块或 `ClientSession._cancel_run_if_unobserved` 调用。

**替代方案**：在 `SessionRegistry.cancel_run()` 中直接耦合 broker。

**选择理由**：避免 `SessionRegistry` 反向依赖权限模块，保持层级清晰。`chat.py` 已持有 `permission_manager` 引用，在 `finally` 中调用最自然。

## Risks / Trade-offs

| 风险 | 缓解措施 |
|------|----------|
| 多个 pending 审批（同一轮次多个工具都 ASK）会导致顺序阻塞，用户体验差 | AgentLoop 中工具是顺序执行的，同一时刻最多一个 pending，这是当前架构的固有限制；MVP 接受此限制 |
| 审批期间服务端无心跳，客户端看门狗可能误判断连 | 心跳由 `ClientSession._execute_chat` 独立 task 推送，与 AgentLoop 阻塞无关；心跳继续 |
| 用户审批输入期间按 Ctrl+C 导致 CLI 崩溃 | CLI 的 `input()` 在 `asyncio.to_thread` 中，需在外层捕获 `KeyboardInterrupt` 并转为 deny 响应 |
| `InteractiveBroker` 的 Future 表在极端并发下可能内存泄漏 | 超时机制保证 Future 最多存活 `permission_timeout` 秒；Run 取消时主动清理 |
| 多客户端同时响应导致后到达的响应被静默忽略 | 设计意图（先响应者获胜），客户端会收到 "请求已过期" 错误提示 |

## Migration Plan

1. **部署**：无数据迁移，纯代码变更。更新 `.env` 后重启 server 即可。
2. **回滚**：如发现问题，将 `core/app.py` 中 `InteractiveBroker` 改回 `NonInteractiveBroker`，server 重启后所有 ASK 恢复为默认拒绝。
3. **兼容性**：新增 notification 和 method 对旧客户端无影响（旧客户端忽略未知通知）。但旧客户端无法响应审批，因此若使用旧客户端连接，ASK 将因无人响应而超时拒绝——这与旧行为一致（默认拒绝），无破坏性变更。

## Open Questions

无。
