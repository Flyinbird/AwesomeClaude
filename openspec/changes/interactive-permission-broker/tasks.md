## 1. 配置与协议层

- [ ] 1.1 `protocol/methods.py` 中新增 `METHOD_PERMISSION_RESPOND`、`NOTIFY_CHAT_PERMISSION_REQUESTED` 及对应 TypedDict（`PermissionRequestNotificationParams`、`PermissionRespondParams`、`PermissionRespondResult`），并更新 `__all__`；验证 `uv run mypy src/` 无新增类型错误
- [ ] 1.2 `core/config.py` 的 `ServerConfig` 新增 `permission_timeout: int = 60`，并从环境变量 `AWESOME_CLAUDE_PERMISSION_TIMEOUT` 读取；验证 `load_server_config()` 能正确解析默认值与覆盖值
- [ ] 1.3 `.env.example` 新增 `AWESOME_CLAUDE_PERMISSION_TIMEOUT=60` 说明行

## 2. Server 核心：交互式审批通道

- [ ] 2.1 `core/permissions/broker.py` 实现 `InteractiveBroker`：维护 `pending: dict[str, _PendingItem]`，`ask()` 生成短 `request_id`、广播通知、创建 Future 并等待超时；`respond(request_id, decision)` 查找并设置结果；`cancel_for_run(run_id)` 清理并拒绝该 Run 下所有 pending；验证通过 `tests/unit/test_permissions_broker.py` 中 ask/respond/timeout/cancel 四个场景
- [ ] 2.2 `core/handlers/permission.py` 新建 `handle_permission_respond` handler，接收 `request_id` 与 `decision`，委托 `InteractiveBroker.respond()`；若 request_id 不存在返回错误响应；验证通过 `tests/unit/test_handlers_permission.py`
- [ ] 2.3 `core/router/dispatcher.py` 的 `create_dispatcher()` 注册 `METHOD_PERMISSION_RESPOND`；验证通过运行 `uv run pytest tests/unit/test_dispatcher.py`（如有）或检查注册字典非空
- [ ] 2.4 `core/handlers/chat.py` 替换 `NonInteractiveBroker()` 为 `InteractiveBroker(context.sessions, recorder, timeout=config.permission_timeout)`，并在 `finally` 块中调用 `permission_manager._broker.cancel_for_run(run_id)` 清理 pending；验证通过代码审查确认无遗漏引用
- [ ] 2.5 `core/app.py` 更新 `build_context_factory` 签名以传递 `permission_timeout`，并在 `run_server` 中构造 `InteractiveBroker` 时传入；验证通过启动 server `uv run python -m awesome_claude.core.app` 不报错

## 3. Client 审批交互

- [ ] 3.1 `client/cli/renderer.py` 新增 `render_permission_requested(tool_name, action, request_id)` 方法，打印格式化审批提示；验证通过代码审查
- [ ] 3.2 `client/cli/app.py` 注册 `NOTIFY_CHAT_PERMISSION_REQUESTED` notification handler `_handle_permission_requested`：打印提示、等待用户输入 `y/n`、发送 `permission.respond`；无效输入时重新提示；捕获 `KeyboardInterrupt` 转为 deny；验证通过手工端到端测试：触发 ASK 工具后 CLI 正确显示提示并等待输入

## 4. 权限规则文档

- [ ] 4.1 `core/permissions/README.md` 新建，包含：权限体系概述（三态、策略优先级、审批通道）、各内置工具权限规则表（工具名、默认姿态、资源标识示例、动作描述）、配置说明（`.env` 变量、`PermissionRule` 写法示例）；验证通过文档审查确认信息准确

## 5. 测试

- [ ] 5.1 `tests/unit/test_permissions_broker.py` 新建，覆盖：正常 ask/respond 允许、ask/respond 拒绝、超时按拒绝处理、cancel_for_run 清理 pending、重复响应被忽略；验证通过 `uv run pytest tests/unit/test_permissions_broker.py -v`
- [ ] 5.2 `tests/unit/test_handlers_permission.py` 新建，覆盖：有效响应返回成功、无效 request_id 返回错误、已超时请求返回错误；验证通过 `uv run pytest tests/unit/test_handlers_permission.py -v`
- [ ] 5.3 运行完整测试套件 `uv run pytest -v`，确认无回归失败
- [ ] 5.4 运行类型检查 `uv run mypy src/`，确认无新增类型错误
- [ ] 5.5 运行 lint `uv run ruff check src/ tests/`，确认无新增 lint 错误

## 6. 集成验证

- [ ] 6.1 端到端手工验证：配置某工具默认姿态为 ASK，启动 server + client，触发该工具调用，确认 CLI 显示审批提示，输入 `y` 后工具正常执行并收到结果；输入 `n` 后工具被拒绝并以错误结果回填；不输入等待 60 秒后自动拒绝；验证通过观察终端输出与 server 日志
