## Context

见 `proposal.md`。当前实现中：

- 协议字段 `task_id` 实际承载的是一次对话执行（Run）的标识；`core/server/session.py` 生成它后原样作为 `run_id` 传入 `Run`。
- `core/task/manager.py` 的 `TaskManager` 无状态，逐个方法把 `run_id` / `start_time` 透传给 `shared/logging/task_tracker.py` 的 `TaskTracker`；`core/task/stages.py` 只是 `TaskStage` 的 re-export。
- `TaskStage` 枚举中大部分取值（step / llm / tool）是轮次级阶段，只有首尾几个是 Run 级，命名与内容不符。
- 轨迹落盘为 `logs/tasks/{date}/{task_id}.jsonl`。
- 现有 OpenSpec capability：`session-run-lifecycle` 已有 "Run" 术语，但其 "活跃任务语义" 需求仍用"任务"表述。

## Goals / Non-Goals

**Goals:**

- 把"一次对话执行"的标识在协议与代码中统一为 `run_id`，释放 `Task` 一词。
- 把记录执行轨迹的一组类型正名为 Trace 词汇，使枚举含义与取值一致。
- 让轨迹记录器绑定 Run，消除 `run_id` / `start_time` 在调用点的重复传递。
- 保持一切运行时行为不变，只改标识与组织。

**Non-Goals:**

- 不引入 Task 计划领域对象与 DAG（由后续 `add-task-planning` change 负责）。
- 不改动工具的进程级执行环境与 per-Run 运行态通道。
- 不迁移历史轨迹文件。

## Decisions

### D1: 协议标识 `task_id` 正名为 `run_id`（BREAKING）

`ChatResponse`、五个 `chat.*` 通知、会话历史条目、`session.attach` / 会话状态返回的活跃列表字段一并更名；活跃列表字段 `active_tasks -> active_runs`。

- 理由：`task_id` 语义上就是 Run 标识；正名后 `task_id` 空出给计划项。
- 备选（否决）：保留 `task_id`，计划项另用 `plan_task_id` 之类名字——治标不治本，继续保留误称。
- 兼容性：当前只有 CLI 一个客户端、未发版，直接改名，不提供别名。

### D2: 日志词汇改为 Trace 系列

`TaskStage -> TraceStage`、`TaskEvent -> TraceEvent`、`TaskTracker -> TraceStore`、`TaskManager -> TraceRecorder`。

- 理由：这组类型记录的是一份 Run 执行轨迹，且未来会同时包含 Run / Task / Step 三级事件；`Trace` 是对事件流的中性统称。
- 备选（否决）：`RunStage / RunEvent`——层级上不准确（流里混有 task / step 事件）。

### D3: `TraceRecorder` 绑定 Run，落 `core/observability/`；`TraceStore` 留 `shared/logging/`

- 理由：记录器需要 `run_id` + `start_time`，在 Run 创建处构造一次，调用点只传 `stage` / `data` / `step_index`；这消除了它"无状态门面"的观感。底层 JSONL 写入器是通用工具，留在 `shared/logging/`。
- 备选（否决）：记录器继续留 `core/task/`——与 Task 领域无关，占位错误。

### D4: 轨迹目录 `logs/tasks/` 改为 `logs/runs/`

- 理由：目录名与 Run 术语对齐。历史文件是运行时产物且被 git 忽略，不做迁移。

### D5: 删除 `core/task/stages.py`，清空 `core/task/`

- 理由：re-export 无价值；日志门面迁出后 `core/task/` 不再有内容。该目录由 `add-task-planning` 重新以 Task 领域填充。

### D6: 仅 Run 级阶段改名，轮次级阶段保留

- `TASK_CREATED / TASK_COMPLETED / TASK_FAILED / TASK_INTERRUPTED / TASK_CANCELLED` 分别改为 `run_created / run_completed / run_failed / run_interrupted / run_cancelled`。
- `CONTEXT_BUILT / STEP_STARTED / LLM_* / TOOL_*` 保持不变。

## Risks / Trade-offs

- [BREAKING 协议改名漏改某处] → 以 mypy strict + 全量 `rg task_id` 清理 + e2e 覆盖兜底；`protocol/methods.py` 与 `docs/protocol.md` 同步。
- [记录器改为 run-scoped 时漏传上下文] → 记录器构造点唯一（Run 创建处），编译期类型约束 `run_id` 必填。
- [目录清空后 `add-task-planning` 前存在短暂空包] → 同一迭代内顺序执行两个 change，或在删除时移除该包避免留下空目录。

## Migration Plan

1. 先改 `shared/`：`TraceStage` / `TraceEvent` / `TraceStore`，新增 `core/observability/TraceRecorder`。
2. 改 Core：`Run` 创建处构造记录器；`handlers/chat.py`、`session/registry.py`、`router/context.py` 改用记录器与新字段名。
3. 改协议与客户端：`protocol/methods.py`、`client/cli/*`。
4. 同步文档与测试；删除 `core/task/`。
5. 校验：`uv run mypy src/`、`uv run ruff check src/ tests/`、`uv run pytest`。

回滚：纯重命名变更，回退提交即可，无需数据迁移。
