## Why

当前服务端把「连接存活」与「请求执行」绑在同一条 `await` 链上：处理 `chat` 期间连接读循环被阻塞，既无法感知客户端断开，也无法取消在途任务。客户端 60s 超时断开后，服务端最长可能继续跑 600s+ 的 LLM 调用，导致已断开连接的会话残留（dead session）、`channel.detach` 迟迟不执行、后续广播持续写向死 socket。多客户端共享会话下，"断开即放弃"还会让仍在线的订阅者看到被截断的流。

## What Changes

- 解耦连接读循环与请求执行：控制类请求（ping / echo / session.attach / session.detach / shutdown）内联处理，`chat` 作为可追踪的长任务异步执行。
- 引入 **Run**：一次 `chat` 执行成为由会话拥有的实体，具备唯一终态（COMPLETED / FAILED / INTERRUPTED / CANCELLED）。
- **每会话至多一个活跃 Run**；并发 `chat` 直接拒绝，新增应用错误码 `SESSION_BUSY`（`-32004`）。
- 零订阅（最后一个连接 detach）时取消该会话的活跃 Run；无 `session_id` 的 `chat` 使用服务端生成的临时会话，空即销毁。
- 新增任务终态 `TASK_CANCELLED`；`session.attach` 的 `active_tasks` 语义修正为「在途 Run」。
- 断开与服务端退出时，**从非取消上下文**取消在途 Run，落地终态后再清理连接。
- 连接发送串行化，统一处理并发写入与关闭。

## Capabilities

### New Capabilities

- `connection-lifecycle`: 连接读写与请求执行解耦；断开及时检测与资源回收；在途任务取消的编排上下文；并发发送串行化；服务端优雅退出时取消全部在途任务。
- `session-run-lifecycle`: Run 的归属与终态模型；每会话单活跃 Run 与会话忙拒绝；零订阅取消；临时会话生命周期；`TASK_CANCELLED` 与 `active_tasks` 语义。

### Modified Capabilities

<!-- 现有 openspec/specs/ 下尚无能力级 spec，本次全部为新增能力。 -->

## Impact

- **代码**：`core/server/session.py`（读循环、取消编排）、`core/server/tcp.py`（退出传播）、`core/session/registry.py`（Session 活跃 Run、临时会话、active_tasks）、`core/session/channel.py`（detach 触发）、`core/handlers/chat.py`（Run 包装与终态）、`core/router/`（长任务分类）、`shared/types.py`（`TASK_CANCELLED`）、`protocol/errors.py`（`SESSION_BUSY`），以及相应新增的 Run 承载模块。
- **行为**：断开后清理从「LLM 结束后」提前到「断连后立即」；同会话并发对话由「隐式竞态」变为「显式拒绝」。
- **协议**：不新增方法或通知，仅新增错误码 `-32004 SESSION_BUSY`。
- **测试**：新增连接生命周期与 Run 生命周期单测，并补充多客户端断连/取消的 e2e 用例。
- **非目标**：显式 `chat.cancel` 方法、服务端超时看门狗、会话 TTL 回收、工具执行的真正取消。
