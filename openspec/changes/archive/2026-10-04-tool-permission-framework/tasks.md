## 1. 权限领域模型

- [x] 1.1 新增 `src/awesome_claude/core/permissions/types.py`：`PermissionDecision`（StrEnum: allow/deny/ask）、`PermissionRequest`（tool/args/run_id/action/resources）、`PermissionOutcome`（decision/reason）、`PermissionSpec`（default/describe/resources）。验证：`uv run mypy src/` 通过，且该模块不 import `core.tools`（无循环依赖）。
- [x] 1.2 新增 `src/awesome_claude/core/permissions/__init__.py` 导出上述类型与 `PermissionPolicy` / `PermissionBroker` / `PermissionManager`。验证：`uv run python -c "from awesome_claude.core.permissions import PermissionManager"` 成功。
- [x] 1.3 修改 `core/tools/base.py`：`Tool` 增加 `permission: PermissionSpec | None = None` 字段。验证：`uv run mypy src/` 通过，现有 `tests/unit/test_tools.py` 全绿。
- [x] 1.4 修改 `core/tools/context.py`：`ToolScope` 增加 `permissions: PermissionManager | None = None`（`TYPE_CHECKING` 导入）。验证：`uv run mypy src/` 通过。

## 2. 权限策略

- [x] 2.1 实现 `core/permissions/policy.py` 的 `PermissionPolicy`：持有全局默认、每工具默认与 `type:value` 资源模式规则；`evaluate(request, spec) -> PermissionDecision` 按「资源规则 → 工具默认 → 全局默认」求值，资源规则内拒绝优先。验证：`tests/unit/test_permissions.py` 覆盖四态（允许/拒绝/询问/无命中落全局默认）。
- [x] 2.2 为资源规则增加 `type:value` 前缀/glob 匹配与测试。验证：命令前缀、路径前缀、拒绝优先的用例通过。

## 3. 审批通道

- [x] 3.1 实现 `core/permissions/broker.py`：`PermissionBroker` 协议与 `NonInteractiveBroker`（询问即拒绝，fail-closed）。验证：单测断言 `ask` 在无交互时返回拒绝。

## 4. 权限管理者

- [x] 4.1 实现 `core/permissions/manager.py` 的 `PermissionManager`：构造入参为 policy / broker / 可选轨迹记录器；`authorize(request) -> PermissionOutcome` 编排「查策略 → 询问态委托 broker」，并在判定时记录请求与结果阶段。验证：单测覆盖 allow / deny / ask→deny，且断言写入了对应轨迹阶段。
- [x] 4.2 确保无轨迹记录器时 `authorize` 仍可工作。验证：单测以 `recorder=None` 调用不报错。

## 5. 执行闸口

- [x] 5.1 修改 `core/tools/registry.py` 的 `execute`：当 `scope.permissions` 存在时，用工具的 `permission` 规格构造 `PermissionRequest` 并 `authorize`；拒绝结果直接返回 `ToolResult(is_error=True)` 且不调用 handler；无管理者时完全透传。验证：`tests/unit/test_tools.py` 新增「被拦截」与「透传」两用例通过。
- [x] 5.2 扩展 `tests/unit/test_agent_loop.py`：模拟工具被拒，断言模型收到 `is_error=True` 的 tool_result 且循环继续。验证：该测试通过。

## 6. 轨迹与配置

- [x] 6.1 修改 `shared/types.py`：`TraceStage` 增加 `PERMISSION_REQUESTED` / `PERMISSION_GRANTED` / `PERMISSION_DENIED`。验证：`tests/unit/test_types.py`（或新增断言）确认枚举值。
- [x] 6.2 修改 `core/config.py`：`ServerConfig` 增加 `permission_default: PermissionDecision`，`load_server_config` 读取 `AWESOME_CLAUDE_PERMISSION_DEFAULT`（默认 allow，非法值抛 `ValueError`）。验证：`tests/unit/test_config.py` 覆盖默认、显式值与非法值。
- [x] 6.3 更新 `.env.example` 与 `AGENTS.md` 环境变量表，登记 `AWESOME_CLAUDE_PERMISSION_DEFAULT`。验证：文件内可见该条目。

## 7. 装配与注入

- [x] 7.1 修改 `core/router/context.py`：`HandlerContext` 增加 `permission_policy: PermissionPolicy | None = None`。验证：`uv run mypy src/` 通过。
- [x] 7.2 修改 `core/app.py`：用 `config.permission_default` 构造进程级 `PermissionPolicy`（含各内置工具的默认姿态：read/list allow，write/edit allow，get_time/plan allow），注入 `build_context_factory` 与 `HandlerContext`。验证：`tests/unit/test_server.py` 或 e2e 冒烟通过。
- [x] 7.3 修改 `core/handlers/chat.py`：构造 `ToolScope` 时创建 per-Run `PermissionManager`（注入 handler 的 `permission_policy`、`NonInteractiveBroker`、`run.recorder`），赋给 `scope.permissions`。验证：`tests/unit/test_chat_handler.py` 或 `tests/e2e/test_chat.py` 通过；无 policy 时 `scope.permissions` 为 None。
- [x] 7.4 为内置工具声明 `PermissionSpec`（`core/tools/builtin/fs.py`、`time.py`、`plan.py`），默认姿态与资源标识见 design 决策 4/5。验证：`tests/unit/test_fs_tools.py`、`test_plan_tools.py` 全绿且单测断言 resources 形如 `path:<绝对路径>`。

## 8. 端到端与质量门

- [x] 8.1 新增/扩展 e2e：一个被策略拒绝的工具调用不终止 Run，最终仍返回完成响应且轨迹含 permission_denied。验证：`uv run pytest tests/e2e -q` 通过。
- [x] 8.2 全量验证：`uv run pytest -q`、`uv run mypy src/`、`uv run ruff check src/ tests/` 全部通过。
