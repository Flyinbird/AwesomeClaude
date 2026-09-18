## Why

代码与协议把"一次对话执行"普遍称作 task，但真正的执行实体是 `Run`；`core/task/` 里只有无状态的日志门面，名不副实。这种词汇错位有两个直接代价：`Task` 一词被占用，无法留给未来的计划领域对象；协议字段 `task_id` 实际表达的是 `run_id`，语义不透明。

## What Changes

- **BREAKING**：协议中的对话标识 `task_id` 统一正名为 `run_id`，覆盖 `ChatResponse`、`chat.stream` / `chat.tool_started` / `chat.tool_finished` / `chat.interrupted` / `chat.user_message` 通知，以及会话历史条目。
- **BREAKING**：`session.attach` / 会话状态返回的 `active_tasks` 正名为 `active_runs`。
- 日志词汇正名：`TaskStage -> TraceStage`、`TaskEvent -> TraceEvent`、`TaskTracker -> TraceStore`、`TaskManager -> TraceRecorder`。
- `TraceRecorder` 绑定 Run（`run_id` + `start_time`），成为 run-scoped 记录器；调用点不再手动传递 `run_id` / `start_time`。
- 日志门面迁出 `core/task/`：`TraceRecorder` 落 `core/observability/`，底层 JSONL 写入器 `TraceStore` 留在 `shared/logging/`。
- 轨迹文件目录 `logs/tasks/{date}/{id}.jsonl` 改为 `logs/runs/{date}/{id}.jsonl`。
- Trace 阶段正名：Run 级阶段改为 `run_created` / `run_completed` / `run_failed` / `run_interrupted` / `run_cancelled`；`step_*` / `llm_*` / `tool_*` 阶段保留。
- 删除 `core/task/stages.py` 的纯 re-export；清空 `core/task/`（其领域内容由后续 change 引入）。
- 同步更新 `AGENTS.md`、`docs/architecture.md`、`docs/protocol.md`、`openspec/specs/overview.md` 的相关词汇。

## Capabilities

### New Capabilities

- `run-trace`: 定义 Run 标识命名与执行轨迹事件流的契约——Run 的唯一标识、Trace 事件的阶段集合与分组字段、以及轨迹的 JSONL 存储位置。

### Modified Capabilities

- `session-run-lifecycle`: 会话订阅/状态返回的在途标识由"活跃任务"正名为"活跃 Run"，与 Run 术语对齐。

## Impact

- Protocol：`protocol/methods.py`（`ChatResponse`、通知类型、`session.attach` 返回）、`docs/protocol.md`。
- Core：`shared/types.py`、`shared/logging/task_tracker.py`、`core/router/context.py`、`core/handlers/chat.py`、`core/server/session.py`、`core/session/registry.py`、`core/session/channel.py`、`core/app.py`。
- Client：`client/cli/app.py`、`client/cli/renderer.py`。
- 文档：`AGENTS.md`、`docs/architecture.md`、`openspec/specs/overview.md`。
- Tests：`tests/` 全量命名更新（e2e / unit / conftest）。
- 行为保持不变，仅标识与词汇正名；`Task` 一词被释放，供后续计划能力使用。
