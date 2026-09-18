## 1. Shared 词汇与类型

- [x] 1.1 在 `shared/types.py` 将 `TaskStage` 更名为 `TraceStage`，并把 Run 级取值改为 `run_created / run_completed / run_failed / run_interrupted / run_cancelled`（其余取值不变）；验证 `uv run mypy src/` 通过且 `tests/unit/test_task.py` 中阶段断言同步更新。
- [x] 1.2 将 `TaskEvent` 更名为 `TraceEvent`；验证 mypy 通过且 JSONL 事件字段名不变（`run_id / stage / timestamp / duration_ms / data / step_index`）。
- [x] 1.3 在 `shared/logging/task_tracker.py` 将 `TaskTracker` 更名为 `TraceStore`，默认轨迹目录改为 `logs/runs`；验证 `tests/unit/test_logging.py` 更新后通过，且生成文件路径为 `logs/runs/{date}/{run_id}.jsonl`。
- [x] 1.4 新增 `core/observability/trace_recorder.py` 的 `TraceRecorder`：构造时绑定 `run_id` 与单调起点，提供 `run_created / record / run_completed / run_failed / run_interrupted / run_cancelled`，内部委托 `TraceStore`；验证新增单元测试覆盖"调用点不再传 run_id / start_time"与耗时相对起点。
- [x] 1.5 删除 `core/task/stages.py` 与 `core/task/manager.py`，清空 `core/task/`；验证 `rg "core\.task|TaskManager|TaskStage|TaskTracker|TaskEvent"` 在 `src/` 无残留且 mypy 通过。

## 2. Core 接线

- [x] 2.1 在 `core/server/session.py` 的 Run 创建处构造 `TraceRecorder`，用它替换原来的 `task_manager.create_task(...)` 记录 `run_created`，并经请求上下文注入 chat handler；验证 `tests/e2e/test_run_lifecycle.py` 通过且轨迹含 `run_created`。
- [x] 2.2 改造 `core/handlers/chat.py`：改用注入的记录器，移除所有 `run_id` / `start_time` 手动传递，通知 payload 字段改为 `run_id`；验证 `tests/unit/test_chat_handler.py` 更新后通过。
- [x] 2.3 改造 `core/session/registry.py` 取消路径与 `state()`：取消记录改用记录器且阶段为 `run_cancelled`，返回字段 `active_tasks -> active_runs`；验证 `tests/unit/test_session.py` 通过。
- [x] 2.4 在 `core/router/context.py` 将 `HandlerContext.task_manager` 替换为记录器字段并同步 `core/app.py` 装配；验证 mypy 通过、`tests/unit/test_server.py` 通过。

## 3. 协议与客户端

- [x] 3.1 在 `protocol/methods.py` 将 `ChatResponse.task_id` 与各 `chat.*` 通知的 `task_id` 更名 `run_id`，`session.attach` 返回的 `active_tasks` 更名 `active_runs`；验证 mypy 通过且协议相关单测更新后通过。
- [x] 3.2 更新 `client/cli/app.py` 与 `client/cli/renderer.py` 的字段读取（`run_id`）及 `_active_task_id` 命名；验证 CLI 单测通过、摘要显示 Run 标识。
- [x] 3.3 更新 `core/session/channel.py` 的 `record_turn` 与历史条目为 `run_id`；验证 `tests/e2e/test_session.py` 通过且回放历史含 `run_id`。
- [x] 3.4 更新 `docs/protocol.md` 中的所有 `task_id` / `active_tasks` 示例与说明；验证文档与 `protocol/methods.py` 字段一致。

## 4. 文档与回归

- [x] 4.1 更新 `AGENTS.md`、`docs/architecture.md`、`openspec/specs/overview.md` 的任务追踪/日志措辞（`TaskManager -> TraceRecorder`、`logs/runs`、Run 术语）；验证 `rg "TaskManager|logs/tasks|task_id"` 在文档中无遗漏（历史 change 归档除外）。
- [x] 4.2 更新 `tests/conftest.py` 与全部 e2e/unit 的构造与断言命名；验证 `uv run pytest` 全绿。
- [x] 4.3 终检：`uv run mypy src/`、`uv run ruff check src/ tests/`、`uv run pytest` 全部通过，且 `openspec validate rename-run-vocabulary --strict` 通过。
