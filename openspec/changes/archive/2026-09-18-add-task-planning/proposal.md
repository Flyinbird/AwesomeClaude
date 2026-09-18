## Why

当前 Agent 只能隐式地一步一步调用工具，无法显式表达"完成一个复杂任务需要哪些步骤、步骤之间有何依赖"。用户希望运行时维护一份带依赖（DAG）的任务清单：模型可自主拆分、执行中动态补充，客户端能实时看到进度与状态。

## What Changes

- 新增 per-Run 的 Task 领域模型与 `TaskGraph`（依赖 DAG），随 Run 生灭、内存维护。
- Task 状态为三态 `pending / in_progress / completed`，**无 failed 状态**；失败通过回到 `pending` 表达。
- 模型经工具声明与推进任务：`add_tasks`、`update_deps`、`start_task`、`complete_task`、`suspend_task`、`reopen_task`。
- DAG 约束：边无环、`deps` 必须指向存在任务、已 `completed` 的任务不可再改 `deps`、`deps` 未满足时 `start_task` 被拒并返回错误工具结果。
- 状态不变量：同一时刻至多一个 Task 处于 `in_progress`。
- 失败重试经 `reopen_task`（`in_progress -> pending`，累计 `attempts` 并记录 `last_error`）；被前置任务挤开经 `suspend_task`（`in_progress -> pending`，不计 `attempts`）。
- 新增 per-Run 的 `ToolScope`（`run_id` / `task_graph` / 进度通知能力），作为显式参数经工具执行链传入；进程级 `ToolContext` 保持不变。
- 轨迹新增 Task 级事件（`task_added / task_started / task_completed / task_reopened / task_suspended`），step 事件携带 `task_id` 分组（追加到既有 Trace 事件流，不改动其通用契约）。
- 新增计划进度通知，向会话推送任务清单与状态快照，客户端据此渲染。

## Capabilities

### New Capabilities

- `task-planning`: 定义一次 Run 内模型可自主拆分、动态演进的任务清单及其依赖 DAG——任务状态机、图约束、状态转换、失败语义、轨迹记录与进度通知。

### Modified Capabilities

<!-- 无：run-trace 的通用事件契约不变，task 级事件作为新增要求由 task-planning 规定。 -->

## Impact

- Core 领域：新增 `core/task/`（`Task`、`TaskGraph`），承载任务模型与图校验。
- 工具系统：`core/tools/base.py`（handler 签名加 scope）、`core/tools/registry.py`（`execute` 加 scope）、`core/tools/context.py`（新增 `ToolScope`）、内置计划工具。
- Agent 循环：`core/agent/loop.py` 将 per-Run scope 透传到工具执行。
- 装配与协议：`core/app.py`、`core/handlers/chat.py`（进度通知接线）、`protocol/methods.py`（新通知方法）。
- 客户端：`client/cli/renderer.py` 渲染任务清单。
- 文档与测试：`AGENTS.md`、`docs/architecture.md`、`docs/protocol.md`、`tests/`。
- 依赖：需要 `rename-run-vocabulary` 先完成（`task_id` 归还给计划项）。
- 行为兼容：无 session 的既有对话在无任务计划时行为不变；仅新增能力。
