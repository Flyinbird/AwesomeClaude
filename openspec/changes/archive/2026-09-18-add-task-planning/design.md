## Context

见 `proposal.md`。相关现状：

- `AgentLoop` 是共享单例，`run()` 每次接收 per-call 状态（messages / history / 回调），工具执行走 `ToolRegistry.execute(name, args)`，handler 签名 `(args, ctx)`。
- `ToolContext` 是进程级不可变配置（沙箱根、读写限额），启动时构造一次。
- 一次对话执行由会话拥有的 `Run` 承载，`run_id` 唯一（依赖 `rename-run-vocabulary` 先行完成）。
- 轨迹事件流为一份 Run 的 JSONL，含 Run 级与 step / tool / llm 级阶段。

## Goals / Non-Goals

**Goals:**

- 在 Run 内维护一份可被模型动态演进、带依赖 DAG 的任务清单。
- 状态转换由模型意图驱动，运行时校验并拒绝非法变更（不终止 Run）。
- 计划进度可推送至客户端并记入轨迹。
- 用最小的新通道把 per-Run 运行态送到需要它的工具。

**Non-Goals:**

- 不并行执行分支任务（顺序执行为前提）。
- 不引入子 agent / 嵌套 AgentLoop。
- 不引入失败终态，不做任务级 token 预算。
- 不改动进程级 `ToolContext` 的语义。

## Decisions

### D1: 任务领域落 `core/task/`，per-Run 内存

`Task`（id / goal / status / deps / attempts / last_error / result）与 `TaskGraph` 组成领域模型，随 Run 生灭，不持久化（轨迹负责留痕）。

- 备选（否决）：持久化到磁盘——当前无跨 Run 生命周期诉求。

### D2: 三态 + 元数据承载失败

状态仅 `pending / in_progress / completed`；`attempts` 与 `last_error` 是元数据而非状态。失败经 `reopen_task` 回 `pending` 并累计 `attempts`；让位经 `suspend_task` 回 `pending` 且不计 `attempts`。

- 理由：保留"无失败状态"的三态语义，同时让模型能据 `attempts` / `last_error` 决定是否换策略。
- 备选（否决）：新增 `failed` 状态——会引入可达性/后继策略的额外复杂度。

### D3: 变更面与校验

允许 `add_tasks`（批量追加）、`update_task_deps`（仅 `pending`）、以及四个状态转换。每次变更后校验：无环、依赖存在、`completed` 任务依赖不可变。环检测用 DFS/拓扑，成本可忽略。

- 理由：追加新任务天然无环（边由旧指向新）；只有给既有 `pending` 任务补前置才可能引入环，故仅在 `update_task_deps` 需要环校验。

### D4: 非法转换 = 错误工具结果，不终止 Run

`start_task` 在依赖未满足、或已存在 `in_progress` 时返回 `is_error=True` 的 `ToolResult`，复用 `ToolRegistry` 既有错误语义。

- 理由：与现有工具失败处理一致，模型可据此自我纠正；不引入新的 Run 终止路径。

### D5: per-Run 运行态经 `ToolScope` 显式传参（B1）

新增 `ToolScope`（`run_id` / `task_graph` / 进度通知能力）。`AgentLoop.run(..., scope)` -> `ToolRegistry.execute(name, args, scope)` -> `handler(args, ctx, scope)`。`ToolContext` 保持进程级不变。

- 理由：环境是进程级、运行态是 per-Run，两者用参数解耦；与 `run()` 已有的 per-call 参数风格一致。
- 备选（否决）：把 `ToolContext` 变成 per-Run（W1）——把进程级配置塞进每次对话重建的对象，生命周期混淆。

### D6: 计划工具用细粒度意图集

`add_tasks` / `update_task_deps` / `start_task` / `complete_task` / `reopen_task` / `suspend_task`。每个工具对应一种明确意图与一类校验，事件可 1:1 映射。

- 备选（考虑）：单个 `update_plan` 全量提交（类似整表重写）——schema 更小，但每次需回显全表、且与"运行时维护图"的模型不符。

### D7: 变更经观察者统一派发

`TaskGraph` 变更通过注入的观察者回调派发两类副作用：写入轨迹事件、推送进度通知。工具只变更领域对象，不直接做 IO。

- 理由：职责单一；轨迹与通知消费者解耦于图实现。

### D8: 轨迹与通知

新增 `TraceStage` 取值 `task_added / task_started / task_completed / task_reopened / task_suspended`；step / tool / llm 事件携带 `task_id`。新增计划进度通知，payload 为任务快照（id / goal / status / deps / attempts）。命名会话 `broadcast`，临时会话单播 `run.initiator.sink`（与既有通知路径一致）。

## Risks / Trade-offs

- [计划进入上下文带来 token 开销] → 快照只含 id / goal / status / deps，不含 result 全文；result 仅在轨迹保留。
- [模型过度/不足拆分] → 通过工具描述与 `update_task_deps` 允许纠偏；不额外建机制。
- [临时会话通知漏投] → 复用既有 `RunNotifier` 语义（命名广播 / 临时单播），并以 e2e 覆盖。
- [计划工具成为共享注册表中的有状态特例] → 工具仅通过 `ToolScope` 访问 per-Run 图，注册表与工具实例保持无状态。

## Migration Plan

1. 前置：`rename-run-vocabulary` 完成。
2. 新增 `core/task/` 领域与单测。
3. 引入 `ToolScope` 并贯通 `AgentLoop.run -> execute -> handler`，保持既有工具兼容。
4. 实现计划工具与图校验。
5. 接入轨迹观察者与进度通知；客户端渲染。
6. 校验：`uv run mypy src/`、`uv run ruff check src/ tests/`、`uv run pytest`。

回滚：不新增持久化，回退提交即可。

## Open Questions

- 进度通知的方法名与 payload 字段命名（`chat.plan_updated` 等）——不影响行为契约，实现时定。
- 计划快照是否需要包含依赖关系以便客户端画出 DAG，还是仅展示状态列表——实现时按渲染需要定。
