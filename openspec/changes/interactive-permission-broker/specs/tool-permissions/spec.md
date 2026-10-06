## MODIFIED Requirements

### Requirement: 权限判定结果三态

权限判定结果 MUST 为允许、拒绝、询问三者之一。结果为拒绝时工具 MUST NOT 执行；结果为询问时，若当前存在交互式审批通道，MUST 向客户端请求审批并在收到响应后按响应结果执行；若客户端未在时限内响应或无交互式审批通道，MUST 按拒绝处理（fail-closed）。

#### Scenario: 拒绝阻止执行

- **WHEN** 某次工具调用经判定结果为拒绝
- **THEN** 工具处理器不被调用

#### Scenario: 无审批通道的询问按拒绝处理

- **WHEN** 判定结果为询问且当前未提供交互审批通道
- **THEN** 该次调用按拒绝处理，工具不执行

#### Scenario: 交互式审批通道存在时向客户端请求审批

- **WHEN** 判定结果为询问且当前配置了交互式审批通道
- **THEN** 服务端 MUST 向会话内所有订阅客户端推送审批请求通知，并等待响应

#### Scenario: 客户端允许后工具执行

- **WHEN** 判定结果为询问且客户端在时限内响应允许
- **THEN** 工具处理器被调用并返回正常结果

#### Scenario: 客户端拒绝后工具不执行

- **WHEN** 判定结果为询问且客户端在时限内响应拒绝
- **THEN** 工具处理器不被调用，该次调用按拒绝处理

#### Scenario: 审批超时按拒绝处理

- **WHEN** 判定结果为询问且客户端在配置的超时时间内未响应
- **THEN** 该次调用按拒绝处理，工具不执行，并以 "审批超时" 作为拒绝原因回填工具结果

## ADDED Requirements

### Requirement: 审批请求通知的格式与投递

当判定结果为询问且存在交互式审批通道时，服务端 MUST 向客户端发送 `chat.permission_requested` 通知。通知 MUST 包含唯一请求标识、工具名、面向人的动作描述、资源标识列表、所属 Run 标识与 step 序号。通知 MUST 投递到会话内所有订阅客户端，先响应者获胜。

#### Scenario: 审批通知包含必要信息

- **WHEN** 某次工具调用触发询问审批
- **THEN** `chat.permission_requested` 通知包含 `request_id`、`tool_name`、`action`、`resources`、`run_id`、`step_index`

#### Scenario: 多客户端订阅时广播审批通知

- **WHEN** 某命名会话有多个客户端订阅且某次调用触发询问审批
- **THEN** 该通知被扇出到会话内所有订阅客户端

#### Scenario: 先响应者获胜

- **WHEN** 多个客户端对同一审批请求发送响应
- **THEN** 首个有效响应被采纳，后续响应被忽略

### Requirement: 审批响应接口

客户端 MUST 通过 `permission.respond` 方法回复审批决定。响应 MUST 包含与通知一致的 `request_id` 与决定（`allow` 或 `deny`）。服务端收到响应后 MUST 立即解除对应审批请求的阻塞状态。

#### Scenario: 有效响应解除阻塞

- **WHEN** 客户端发送包含正确 `request_id` 与 `allow` 决定的 `permission.respond` 请求
- **THEN** 对应工具调用的审批阻塞被解除，判定结果为允许

#### Scenario: 无效 request_id 返回错误

- **WHEN** 客户端发送的 `permission.respond` 包含不存在或已超时的 `request_id`
- **THEN** 服务端返回错误响应，提示请求已过期或不存在

### Requirement: 审批超时配置

交互式审批的超时时间 MUST 可通过环境变量 `AWESOME_CLAUDE_PERMISSION_TIMEOUT` 配置，单位为秒，默认值为 60。超时后 pending 的审批请求 MUST 按拒绝处理，且服务端 MUST 清理该请求的内部状态以防止内存泄漏。

#### Scenario: 默认超时 60 秒

- **WHEN** 未配置 `AWESOME_CLAUDE_PERMISSION_TIMEOUT`
- **THEN** 审批超时时间为 60 秒

#### Scenario: 环境变量覆盖超时

- **WHEN** 环境变量 `AWESOME_CLAUDE_PERMISSION_TIMEOUT` 设置为 120
- **THEN** 审批超时时间为 120 秒

### Requirement: Run 取消时清理 pending 审批

当 Run 被取消、失败或完成时，其下所有尚未响应的 pending 审批请求 MUST 被立即清理并按拒绝处理，以防止资源泄漏和无限阻塞。

#### Scenario: Run 取消清理 pending

- **WHEN** 某 Run 处于等待审批状态且该 Run 被取消
- **THEN** 所有 pending 审批请求立即按拒绝处理，对应工具调用返回拒绝结果
