# 工具权限体系

本目录包含 AwesomeClaude 的工具执行权限控制实现。所有工具调用在运行前都经过统一的权限判定，判定结果分为三种：

- **ALLOW**（允许）：直接执行工具。
- **DENY**（拒绝）：不执行工具，以错误结果回填给模型。
- **ASK**（询问）：通过交互式审批通道向客户端请求确认；客户端未在时限内响应时按 **DENY** 处理。

## 目录

- `types.py` — 权限领域类型（`PermissionDecision`、`PermissionRequest`、`PermissionSpec`、`PermissionOutcome`）。
- `policy.py` — 进程级权限策略（全局默认、工具默认、资源规则匹配）。
- `broker.py` — 审批通道协议与实现（`NonInteractiveBroker`、`InteractiveBroker`）。
- `manager.py` — Run 作用域权限管理者，编排策略→审批→轨迹记录。

## 配置方式

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `AWESOME_CLAUDE_PERMISSION_DEFAULT` | `allow` | 工具权限全局默认姿态：`allow` / `deny` / `ask` |
| `AWESOME_CLAUDE_PERMISSION_TIMEOUT` | `60` | 交互式审批超时秒数 |

### 资源规则（代码级配置）

`PermissionPolicy` 支持按资源模式规则覆盖默认姿态。规则形式为 `type:glob`，例如：

```python
from awesome_claude.core.permissions.policy import PermissionPolicy, PermissionRule
from awesome_claude.core.permissions.types import PermissionDecision

policy = PermissionPolicy(
    global_default=PermissionDecision.ALLOW,
    tool_defaults={"write_file": PermissionDecision.ASK},
    rules=[
        PermissionRule(pattern="path:/etc/*", decision=PermissionDecision.DENY),
        PermissionRule(pattern="path:/tmp/*", decision=PermissionDecision.ALLOW),
    ],
)
```

判定优先级（从高到低）：
1. 命中的资源规则（`DENY` > `ASK` > `ALLOW`）
2. 工具默认姿态（`tool_defaults` 或工具的 `PermissionSpec.default`）
3. 全局默认姿态（`global_default`）

## 各内置工具的权限规则

### 文件系统工具（`fs.py`）

所有文件系统工具共享相同的资源标识函数 `_path_resource`，产出 `path:<规范绝对路径>` 格式的资源标识。

| 工具名 | 默认姿态 | 动作描述示例 | 资源标识示例 |
|--------|----------|--------------|--------------|
| `read_file` | ALLOW | `读取文件: /path/to/file` | `path:/workspace/file.txt` |
| `write_file` | ASK | `写入文件: /path/to/file` | `path:/workspace/file.txt` |
| `edit_file` | ASK | `编辑文件: /path/to/file` | `path:/workspace/file.txt` |
| `list_dir` | ALLOW | `列出目录: /path/to/dir` | `path:/workspace/dir` |

> `write_file` 与 `edit_file` 默认姿态为 `ASK`：修改文件属于敏感操作，默认需用户交互审批后才执行。可通过资源规则覆盖（如对 `/tmp/*` 放行）。

**路径解析说明**：权限框架在判定前会调用 `Path.resolve()`（含符号链接解析）将相对路径转为绝对路径。若路径超出 `workspace_root`，在权限判定阶段会以原始路径值降级处理（不报错），实际执行阶段会抛出 `PathOutsideRootError`。

### 时间工具（`time.py`）

| 工具名 | 默认姿态 | 动作描述 | 资源标识 |
|--------|----------|----------|----------|
| `get_time` | ALLOW | （无，工具未声明 `describe`） | （无，工具未声明 `resources`） |

`get_time` 不操作任何资源，无资源标识，仅受全局默认和工具默认影响。

### 任务计划工具（`plan.py`）

| 工具名 | 默认姿态 | 动作描述 | 资源标识 |
|--------|----------|----------|----------|
| `add_tasks` | ALLOW | （无） | （无） |
| `update_task_deps` | ALLOW | （无） | （无） |
| `start_task` | ALLOW | （无） | （无） |
| `complete_task` | ALLOW | （无） | （无） |
| `reopen_task` | ALLOW | （无） | （无） |
| `suspend_task` | ALLOW | （无） | （无） |

所有计划工具共享同一个 `_PLAN_PERMISSION`，默认姿态为 `ALLOW`，未声明动作描述与资源标识。它们操作的是内存中的 `TaskGraph`，不触及文件系统或外部资源。

## 新增工具的权限接入指南

为新增工具接入权限体系，需要在 `Tool.permission` 字段声明 `PermissionSpec`：

```python
from awesome_claude.core.permissions.types import PermissionDecision, PermissionSpec
from awesome_claude.core.tools.base import Tool


def my_describe(args: dict, ctx: ToolContext) -> str:
    return f"执行某某操作: {args.get('target')}"


def my_resources(args: dict, ctx: ToolContext) -> tuple[str, ...]:
    return (f"custom:{args.get('target')}",)


tool = Tool(
    name="my_tool",
    description="...",
    input_schema={...},
    handler=my_handler,
    permission=PermissionSpec(
        default=PermissionDecision.ASK,  # 建议敏感操作设为 ASK
        describe=my_describe,
        resources=my_resources,
    ),
)
```

**必须同步更新本文档**：在「各内置工具的权限规则」章节新增一行说明，包含工具名、默认姿态、动作描述与资源标识格式。

## 交互式审批流程

1. `PermissionPolicy.evaluate()` 返回 `ASK`。
2. `PermissionManager` 委托 `InteractiveBroker.ask()`。
3. `InteractiveBroker` 生成短 `request_id`，通过 `SessionRegistry` 向该 Run 所属会话的所有订阅客户端广播 `chat.permission_requested` 通知。
4. CLI 客户端收到通知后打印提示，等待用户输入 `y` / `n`。
5. 客户端发送 `permission.respond`（`request_id` + `decision`）。
6. `InteractiveBroker.respond()` 唤醒对应 `Future`。
7. 若超时（默认 60 秒）或 Run 被取消，pending 请求按 `DENY` 处理。

## 注意事项

- 审批通知广播给**会话内所有订阅客户端**，先响应者获胜。
- 同一时刻一个 Run 内最多只有一个 pending 审批（AgentLoop 顺序执行工具）。
- Run 被取消/失败/完成时，其下所有 pending 审批会被自动清理并拒绝，防止内存泄漏。
