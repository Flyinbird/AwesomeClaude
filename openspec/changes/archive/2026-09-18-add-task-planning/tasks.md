## 1. Task 领域

- [x] 1.1 新增 `core/task/task.py`：`TaskStatus` 枚举（`pending / in_progress / completed`）与 `Task` 数据类（`id / goal / status / deps / attempts / last_error / result`）；验证单元测试断言三态且不存在失败态。
- [x] 1.2 新增 `core/task/graph.py` 的 `TaskGraph`：`add_tasks` / `update_task_deps` / `start_task` / `complete_task` / `reopen_task` / `suspend_task` / `ready`；验证单元测试覆盖状态转换与尝试次数（reopen 累加、suspend 不累加）。
- [x] 1.3 在 `TaskGraph` 实现图校验：依赖存在、无环、`completed` 任务依赖不可变、至多一个 `in_progress`；验证单元测试分别覆盖环、悬空依赖、改已完成任务依赖、双 `in_progress` 四类拒绝。
- [x] 1.4 为 `TaskGraph` 增加 `on_change` 观察者回调（派发变更事件）；验证单元测试断言每次合法变更产生一次回调、非法变更不回调。

## 2. ToolScope 通道（B1）

- [x] 2.1 在 `core/tools/context.py` 新增 per-Run 的 `ToolScope`（`run_id` / `task_graph` / 通知能力），`ToolContext` 保持不变；验证 mypy 通过且 `ToolContext` 无新字段。
- [x] 2.2 将 `ToolRegistry.execute` 增加 `scope` 参数、`ToolHandler` 签名改为 `(args, ctx, scope)`，并更新 `time` / `fs` 内置工具忽略 scope；验证既有 `tests/unit` 工具测试更新后通过。
- [x] 2.3 在 `AgentLoop.run(...)` 增加 `scope` 参数并透传到工具执行；验证 `tests/e2e/test_chat.py` 通过且既有工具调用行为不变。
- [x] 2.4 在 Run 创建 / chat handler 处构造 `ToolScope`（绑定 `run_id`、`task_graph`、进度通知）并传入 `agent_loop.run`；验证 e2e 中计划工具能读写本次 Run 的图。

## 3. 计划工具与校验

- [x] 3.1 新增 `core/tools/builtin/plan.py` 六个工具：`add_tasks` / `update_task_deps` / `start_task` / `complete_task` / `reopen_task` / `suspend_task`，均从 scope 取图；验证各工具的单元测试覆盖成功路径。
- [x] 3.2 在 `core/app.py` 注册计划工具并核对暴露给 LLM 的 schema；验证单测断言工具名与 schema 存在。
- [x] 3.3 验证非法转换（依赖未满足 start、双 in_progress、成环、悬空依赖、改已完成依赖）返回 `is_error=True` 的 `ToolResult` 且 Run 继续；验证单测/ e2e 断言 Run 未失败。

## 4. 轨迹与通知

- [x] 4.1 在 `TraceStage` 增加 `task_added / task_started / task_completed / task_reopened / task_suspended`，并在记录器补充对应方法；验证轨迹 JSONL 含这些阶段且带 `run_id`。
- [x] 4.2 让 step / tool / llm 事件携带 `task_id` 分组（当前进行中任务），chat handler 记录时填充；验证轨迹单元测试断言分组字段。
- [x] 4.3 将 `TaskGraph` 的 `on_change` 观察者接到进度通知：命名会话 `broadcast`、临时会话单播 `run.initiator.sink`；验证 e2e 中命名会话订阅者与临时会话发起连接分别收到计划快照。
- [x] 4.4 在 `protocol/methods.py` 增加进度通知方法常量与参数类型，并同步 `docs/protocol.md`；验证协议单测与文档一致。

## 5. 客户端与文档

- [x] 5.1 在 `client/cli/renderer.py` 渲染计划快照（任务与状态），`client/cli/app.py` 处理该通知；验证 CLI 单测渲染输出包含任务状态。
- [x] 5.2 更新 `AGENTS.md`、`docs/architecture.md` 说明 Task 领域、`TaskGraph`、`ToolScope` 与新通知；验证 `rg "ToolScope|TaskGraph"` 在文档中存在且结构描述与实现一致。

## 6. 回归

- [x] 6.1 运行 `uv run pytest` 全绿，且 e2e 覆盖"拆分任务 → 执行 → 完成"完整链路。
- [x] 6.2 终检：`uv run mypy src/`、`uv run ruff check src/ tests/`、`openspec validate add-task-planning --strict` 全部通过。
