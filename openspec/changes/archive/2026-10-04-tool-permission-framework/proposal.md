## Why

工具系统当前把「该不该执行某个工具」完全交给模型判断，没有任何统一的权限把关：一旦加入命令执行这类高影响能力，模型被间接提示注入或判断失误就会直接产生不可逆后果。随着工具种类增加，权限控制必须对所有工具通用，而不是每条危险命令各做一套。

## What Changes

- 新增通用工具权限框架：在工具执行的唯一入口处统一把关，判定 `allow` / `deny` / `ask`。
- 工具通过声明式 `PermissionSpec` 参与权限体系（默认姿态 + 动作描述 + 资源标识），框架本身不认识具体工具。
- 权限策略由进程级规则表（每工具默认值 + 资源模式规则）与全局默认值构成，支持通过环境变量调整全局默认。
- 位于 Run 作用域的 `ToolScope` 承载 per-Run 权限管理者，作为工具与外部交互的通道。
- 权限判定结果记入 Run 轨迹（请求 / 放行 / 拒绝三个阶段）。
- ASK 的交互审批回路以接口占位并 fail-closed（无交互时拒绝），实际交互（协议方法 + 客户端确认）留待后续变更。
- **BREAKING**: 无（现有工具默认 `allow`，未注入权限管理者时行为不变）。

## Capabilities

### New Capabilities

- `tool-permissions`: 定义工具执行的统一权限把关——判定结果模型、工具参与方式、策略规则、判定闸口、Run 作用域通道与权限轨迹事件。

### Modified Capabilities

<!-- 无。新增轨迹阶段为附加行为，不改变 run-trace 既有需求。 -->

## Impact

- 新增：`src/awesome_claude/core/permissions/`（类型、策略、broker、manager）。
- 修改：`core/tools/base.py`（`Tool.permission`）、`core/tools/context.py`（`ToolScope.permissions`）、`core/tools/registry.py`（执行闸口）、`core/handlers/chat.py`（注入 per-Run 管理者）、`core/app.py`（构造进程级策略）、`shared/types.py`（`TraceStage` 权限阶段）。
- 配置：新增 `AWESOME_CLAUDE_PERMISSION_DEFAULT` 环境变量。
- 不影响：现有工具默认行为、现有测试、协议方法集（本次不新增协议方法）。
