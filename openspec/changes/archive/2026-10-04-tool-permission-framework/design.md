## Context

见 `proposal.md - Why`。当前所有工具执行都汇聚到 `ToolRegistry.execute(name, args, scope)`（`core/agent/loop.py:179` 是唯一调用点），这里是天然的单一闸口。工具执行上下文分两层：`ToolContext` 是进程级环境（启动构造一次），`ToolScope` 是 per-Run 运行态（`run_id` + `task_graph`），由 `chat` handler 在 `core/handlers/chat.py:242` 构造。`Run` 已挂载 `TraceRecorder`，`chat` handler 已具备向会话广播通知的 `emit` 通道。本次变更不引入协议方法或沙箱能力。

## Goals / Non-Goals

**Goals:**

- 在工具执行唯一入口建立通用闸口，覆盖所有现有与未来工具。
- 让工具以声明式规格参与权限判定，框架不感知具体工具。
- 提供进程级策略（全局默认 / 每工具默认 / 资源规则）与判定优先级。
- 把判定绑定到 Run，并输出可测的轨迹事件。
- 为后续交互审批预留 broker 接口，本阶段以 fail-closed 占位。

**Non-Goals:**

- 不实现交互审批回路（协议方法 `permission.request` / `permission.respond`、客户端确认）。
- 不实现命令执行工具及其沙箱（seatbelt / rlimit）。
- 不做持久化规则文件、声明式 JSON 配置、`session` 级授权。
- 不改变现有工具默认行为。

## Decisions

### 决策 1：闸口放在 `ToolRegistry.execute`，而非 AgentLoop

工具执行只有 `ToolRegistry.execute` 一个汇聚点，闸口放这里即可覆盖全部工具；放在 AgentLoop 则需在编排循环里感知工具差异。判定前置到 `execute` 开头，命中拒绝直接返回错误 `ToolResult`，不触碰 handler。
备选：在 AgentLoop 逐工具判定（重复且易漏）；在各工具 handler 内部自判（无法统一、无法通用）。

### 决策 2：拆分 policy / broker / manager 三个角色

- `PermissionPolicy`：进程级、无 I/O 的纯规则求值，可独立单测。
- `PermissionBroker`：唯一接触外部审批的部分；本阶段提供 `NonInteractiveBroker`（询问即拒绝）。
- `PermissionManager`：per-Run 编排门面。判定流程：先查 policy，询问态再委托 broker，最后写轨迹。

拆分理由是关注点分离：策略是纯逻辑、审批是有 I/O 的边界、编排是组合。headless 与测试可注入不同 broker，而无需改动策略。

### 决策 3：通道随 `ToolScope` 传递

`ToolScope` 增加可选字段承载 per-Run 的 `PermissionManager`。`ToolRegistry` 保持进程级单例，执行时从传入的 `scope` 读取管理者。这样判定天然绑定 Run，且并发 Run 互不干扰。
备选：给 `execute` 增加显式参数（改动签名、牵连调用点）；全局单例（无法 per-Run，泄漏风险）。

### 决策 4：工具以 `PermissionSpec` 声明参与

`Tool` 增加可选 `permission: PermissionSpec | None`，含 `default`（默认姿态）、`describe(args)`（面向人的动作描述）、`resources(args)`（资源标识元组）。未声明的工具按全局默认处理。框架只消费规格，不懂具体工具。
备选：在策略里集中维护「工具名 → 姿态」映射（新增工具要改策略，不通用）；工具通过回调自行判定（把编排逻辑散进工具）。

### 决策 5：资源标识用 `type:value` 约定

`resources` 返回形如 `cmd:<命令前缀>`、`path:<绝对路径>`、`host:<域名>` 的字符串，规则以类型前缀 + 值匹配（前缀/glob）。同一规则引擎即可覆盖命令、文件、网络等异构资源，无需按工具分治。

### 决策 6：拒绝以错误工具结果表达

拒绝返回 `ToolResult(content=..., is_error=True)` 由 AgentLoop 回填模型，而非抛协议错误。理由：模型可据错误改道，Run 不中断；与既有工具失败（未注册、异常）语义一致。

### 决策 7：判定优先级与策略来源

求值顺序 `资源规则 → 工具默认 → 全局默认`，首个命中定案；资源规则内拒绝优先于允许。策略在代码内定义（每工具默认值），全局默认可由环境变量 `AWESOME_CLAUDE_PERMISSION_DEFAULT` 覆盖。本阶段不引入声明式规则文件。
备选：一步到位的 JSON 规则文件（超出本阶段范围、增加解析与校验面）。

### 决策 8：轨迹阶段

`TraceStage` 增加 `permission_requested` / `permission_granted` / `permission_denied`，随判定在 `manager` 内记录，携带工具名、资源、结果与 step。仅在发生判定时记录，无判定则无事件。

## Risks / Trade-offs

- [约 8 个文件被触及，含共享枚举] → 变更加法式：新增字段均带默认值，未注入管理者时完全透传；现有测试应全绿。
- [策略写在代码里，调整需改代码] → 本阶段接受，全局默认已可经环境变量调整；规则文件后置。
- [ASK 仅占位，尚无交互] → 明确 Non-Goal，并以 fail-closed 保证不会「无声放行」；交互回路作为后续变更。
- [`scope.permissions` 为可选，存在被漏传的路径] → 闸口在 `execute` 统一读取，测试覆盖「有管理者被拦截」与「无管理者透传」两态。

## Migration Plan

- 纯增量，无数据迁移。现有工具默认姿态为允许且未注册规格者走全局默认，行为不变。
- 如需回滚：移除各注入点（`chat.py` / `app.py`）即恢复原行为，新增模块无副作用。

## Open Questions

- 交互审批的多订阅者语义（发起方 / 任一订阅者 / 全体，以及发起方断连与超时处理）留待后续交互审批变更定义，不影响本阶段的策略与闸口设计。
- 授权作用域（`once` / `run` / `session` / 持久化）在引入交互审批时再定。
