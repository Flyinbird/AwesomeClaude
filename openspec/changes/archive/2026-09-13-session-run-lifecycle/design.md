## Context

当前 `ClientSession.handle_connection` 以单条 `await` 链顺序处理消息：`readline -> dispatch -> handler`。`chat` handler 会一路 await 到 `AgentLoop` 与 LLM 流结束，期间连接读循环完全停滞。这带来三个后果：

- 无法在对话执行期间读取后续消息，因此无法响应取消，也无法及时感知客户端断开（EOF 要等 LLM 返回后才被读到）。
- `channel.detach()` 位于连接处理器的 `finally`，被推迟到对话结束后才执行，导致已断开连接的 sink 长期滞留在 `Session.sinks`。
- 客户端 60s 超时而 LLM 最长 600s（重试下更久），断开与清理严重脱节。

多客户端共享会话已是既有能力（`SessionRegistry` + `SessionChannel`），因此任何"断开即放弃"的清理策略都必须考虑同会话其他订阅者。动机与范围见 `proposal.md`；行为契约见 `specs/`。

## Goals / Non-Goals

**Goals:**

- 让连接读循环与请求执行解耦，使断开可被及时检测。
- 引入可寻址、可取消、终态唯一的执行实体（Run），归属会话。
- 在零订阅与服务端退出两种场景下可靠取消并记录终态。
- 保证多客户端共享会话下的并发语义明确、历史一致。

**Non-Goals:**

- 不新增显式取消方法（`chat.cancel`）或通知。
- 不做服务端超时看门狗（属主线 A）。
- 不做会话 TTL 回收。
- 不做工具执行的真正取消。
- 不改动客户端协议交互方式。

## Decisions

### D1. 分发模型：控制类内联 + 对话任务化

读循环对控制类请求（ping / echo / session.attach / session.detach / shutdown）内联 await；仅 `chat` 作为独立任务异步执行。

**理由**：`session.attach` 与紧随其后的 `chat` 之间存在顺序依赖（chat 读取 `channel.session_id`）。若无差别地把每个请求都任务化，二者可能乱序，破坏语义。

**备选**：统一每请求一个任务（顺序竞态）；读循环 + 单 worker 队列（仍无法在对话期间响应 ping/取消）。两者均被否决。

### D2. Run 归属会话，而非连接

一次对话执行成为 `Session` 拥有的 Run。

**理由**：多客户端共享会话是一等功能，会话本身就是"订阅集合"，其订阅数天然是 Run 生命周期的判据。无需引入全局注册表。

**备选**：连接拥有（共享会话下会误杀仍在被观察的对话）；全局 `RunRegistry`（多一层间接，收益不足）。

### D3. 取消触发：零订阅

当会话订阅集合变为空时取消活跃 Run。

**理由**：对话应在"还有人在看"时继续。这同时修正了"断开即放弃"在共享会话下的错误，又治好了单客户端断开后的资源泄漏。

**备选**：断开即取消（共享会话下错误）；宽限期后取消（推迟，见 Open Questions）。

### D4. 每会话单活跃 Run，并发拒绝

同一会话同一时刻至多一个活跃 Run；新对话返回 `-32004 SESSION_BUSY`。

**理由**：并发 Run 会从同一份历史出发，导致历史追加顺序与提问顺序不一致，且后发 Run 看不到先发 Run 的轮次，破坏"对话"语义。CLI 单客户端发一条等一条，不触发该分支。

**备选**：排队（需引入待执行队列及其取消语义，超出本次范围）；并发（语义错误）。

### D5. 临时会话：无 `session_id` 时生成，空即销毁

未携带 `session_id` 的对话由服务端生成临时会话承载，无订阅者时销毁；命名会话无订阅时保留历史供回放。

**理由**：让 Run 归属模型统一（总是"会话拥有"），避免为无会话路径维护第二套生命周期；命名会话保留是回放语义的既有要求。

**备选**：无 `session_id` 走连接拥有（两套逻辑，易分叉）。

### D6. 取消编排：非取消上下文发起，`gather` 落地后写终态

取消流程由未被取消的上下文发起（连接 EOF 处理器 / 服务端关闭流程）：先 `cancel()`，再 `await gather(task, return_exceptions=True)` 等待任务真正结束，随后在安全上下文中写入终态并清理会话引用。

**理由**：被取消的协程在 catch `CancelledError` 后继续 await 清理并不可靠（重复 cancel、事件循环关闭会二次打断）。把"落地"放在外部可保证终态一定被记录。

**备选**：handler 内 `except CancelledError` + `asyncio.shield` 清理（脆弱，且服务端退出时仍可能丢终态）。

### D7. 终态新增 `TASK_CANCELLED`

取消与"达到步数上限（`TASK_INTERRUPTED`）"语义不同，新增独立终态。终态只写一次，重复取消为 no-op。

**备选**：复用 `TASK_INTERRUPTED`（无法区分外部中止与步数耗尽）。

### D8. 连接发送串行化

为每个连接引入发送锁与 closed 标志，统一串行化写入并安全丢弃关闭后的发送。

**理由**：解耦后同一连接存在多个并发发送者（控制响应、Run 最终响应、会话广播）。消息分帧本身因 `write` 同步而不易撕裂，但统一锁让关闭时的错误处理有单一出口，属防御性设计。

**备选**：不加锁（可运行，但错误路径分散）。

### D9. 长任务分类先硬编码为 `chat`

**理由**：保持本次范围紧凑。等 Phase 5 Planning 出现第二个长任务时再抽象为 handler 元数据。

**备选**：立即引入 `long_running` 处理器元数据（为时过早）。

### 目标形态

```
Session
  sinks / history
  active_run: Run | None          <-- 单活跃不变量
  ephemeral: bool

Run
  task_id / session_id
  state: RUNNING -> {COMPLETED, FAILED, INTERRUPTED, CANCELLED}
  task: asyncio.Task
  initiator: (request_id, sink)   <-- 回最终 response 用

连接断开 (EOF)
  detach(sink) -> sinks 为空?
        是 -> cancel_run: task.cancel() -> await gather -> 记录 CANCELLED -> 清 active_run
        否 -> 保持运行
```

## Risks / Trade-offs

- **工具线程不可取消** → `asyncio.to_thread` 停不掉，取消时文件操作可能已执行完但结果被丢弃。缓解：文件四件套多为单次写入，可接受；在上马命令执行前必须重新设计取消语义。
- **取消丢失在途最终答复** → 已流式输出的增量保留在客户端，最终答复与历史不落地。缓解：这是取消的预期代价，且不污染历史。
- **广播的队头阻塞** → 单个慢 sink 会拖慢同会话其他 sink 的扇出。缓解：现有 `broadcast` 已按 sink 隔离异常；并行扇出留作后续优化。
- **退出路径顺序敏感** → 若在连接任务被取消后、于其 `finally` 内 await 清理，可能被二次打断。缓解：服务端关闭流程在取消连接任务之前/独立地取消 Run（D6）。
- **控制响应与对话响应乱序** → 对话期间的控制响应会先于对话响应返回。缓解：JSON-RPC 以 `id` 关联，客户端接收器已支持。
- **既有测试假设顺序分发** → 部分单元测试可能需要调整。缓解：见迁移计划。

## Migration Plan

1. 引入 Run 及其状态机与终态记录（含 `TASK_CANCELLED`）。
2. 扩展 `Session`：活跃 Run、临时会话标记、`active_tasks` 改为在途 Run。
3. 重构 `ClientSession` 读循环：控制内联、对话任务化、断连取消、发送锁。
4. 将 `chat` handler 包装为 Run；取消路径不写 `record_turn`。
5. 在服务端关闭流程中取消全部在途 Run 并落地终态。
6. 更新/新增单元测试与多客户端 e2e（断连取消、共享会话继续、并发拒绝）。
7. 回滚策略：协议改动仅为新增错误码，属附加项；整体回滚只需还原上述模块。

## Open Questions

- 零订阅取消是否应引入宽限期以容忍短暂重连？（当前为立即取消，可后续叠加而不改本设计骨架。）
- 命名会话是否需要 TTL 回收以控制内存？（与本次 Run 生命周期正交。）
- 未来是否暴露显式 `chat.cancel` 与 `chat.cancelled` 通知？（本次已为其预留取消原语。）
