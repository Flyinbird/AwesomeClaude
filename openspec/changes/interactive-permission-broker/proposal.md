## Why

当前工具权限体系已支持 `ALLOW` / `DENY` / `ASK` 三态判定，但当判定结果为 `ASK` 时缺少交互式审批通道，只能按拒绝处理（`NonInteractiveBroker`）。这导致即使管理员希望某些敏感操作（如写文件、执行命令）经人工确认后再执行，也无从实现。引入交互式审批后，客户端可在工具执行前向用户展示动作描述并等待响应，既保留安全边界，又避免一刀切拒绝。

## What Changes

- 在 `core/permissions/broker.py` 中新增 `InteractiveBroker`，替代 `NonInteractiveBroker` 作为默认审批通道。
- 新增 Server → Client JSON-RPC notification：`chat.permission_requested`，携带 `request_id`、`tool_name`、`action`、`resources`、`run_id`。
- 新增 Client → Server JSON-RPC method：`permission.respond`，用于回复审批决定（`allow` / `deny`）。
- CLI 客户端支持极简审批交互：收到通知后打印动作描述，等待用户输入 `y` / `n`，超时视为拒绝。
- 审批超时时间可配置（`.env` 新增 `AWESOME_CLAUDE_PERMISSION_TIMEOUT`，默认 60 秒）。
- `core/permissions/` 下新增 `README.md`，说明现有各工具的权限规则、资源标识与配置方式。
- 新增单元测试覆盖 `InteractiveBroker` 的 ask / respond / timeout / cancel 场景。

## Capabilities

### New Capabilities
<!-- 无全新领域能力，属于现有权限体系的扩展 -->

### Modified Capabilities
- `openspec/specs/tool-permissions/spec.md`：补充交互式审批通道的行为要求。原 "无审批通道的询问按拒绝处理" 扩展为 "当存在交互式审批通道时，询问 MUST 向客户端请求审批；客户端未在时限内响应时 MUST 按拒绝处理"。

## Impact

- **Server 端**：`core/permissions/broker.py`、`core/handlers/chat.py`、`core/app.py`、`core/config.py`。
- **协议层**：`protocol/methods.py` 新增常量与 TypedDict；`core/router/dispatcher.py` 注册新 handler；`core/handlers/permission.py` 新建。
- **Client 端**：`client/cli/app.py`、`client/cli/renderer.py` 新增审批通知处理与渲染。
- **配置**：`.env.example` 新增 `AWESOME_CLAUDE_PERMISSION_TIMEOUT`。
- **文档**：`core/permissions/README.md` 新建。
- **测试**：`tests/unit/test_permissions_broker.py`、`tests/unit/test_handlers_permission.py` 新建。
