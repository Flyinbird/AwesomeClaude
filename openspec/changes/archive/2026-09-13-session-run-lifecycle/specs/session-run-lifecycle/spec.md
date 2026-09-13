## Purpose

定义一次对话执行（Run）如何归属会话、如何进入唯一终态，以及多客户端共享会话下的并发约束、临时会话与取消清理规则。

## ADDED Requirements

### Requirement: 对话执行为会话拥有的 Run

每次对话执行 SHALL 成为由目标会话拥有的 Run；未指定会话的对话 SHALL 使用服务端生成的临时会话承载。

#### Scenario: 指定会话的对话归属该会话

- **WHEN** 客户端以 session_id 发起对话
- **THEN** 该次执行归属该会话，可被同会话其他订阅者观察到

#### Scenario: 未指定会话时使用临时会话

- **WHEN** 客户端未携带 session_id 发起对话
- **THEN** 服务端生成一个临时会话承载该次执行

### Requirement: 每会话单活跃 Run

一个会话在同一时刻 SHALL 至多拥有一个活跃 Run；期间到达的新对话请求 MUST 被拒绝，并返回应用错误码 `-32004`（会话忙）。

#### Scenario: 并发对话被拒绝

- **WHEN** 会话已有活跃 Run，另一客户端向同一会话发起对话
- **THEN** 该请求被拒绝并返回 `-32004`，已有 Run 不受影响

#### Scenario: 前一 Run 结束后可再次对话

- **WHEN** 会话的活跃 Run 进入终态
- **THEN** 该会话可再次受理新的对话

### Requirement: Run 唯一终态

每个 Run SHALL 恰好进入一个终态：完成、失败、中断（达到步数上限）或取消；终态 SHALL 只被记录一次，重复取消 MUST 为无操作。

#### Scenario: 正常完成

- **WHEN** Run 在步数上限内得到最终答复
- **THEN** Run 记录为完成

#### Scenario: 达到步数上限

- **WHEN** Run 达到最大步数
- **THEN** Run 记录为中断，而非取消

#### Scenario: 被取消

- **WHEN** Run 在执行中被取消
- **THEN** Run 记录为取消，且不重复记录终态

### Requirement: 零订阅触发取消

当会话的订阅连接集合变为空时，该会话的活跃 Run MUST 被取消。

#### Scenario: 最后订阅者断开

- **WHEN** 会话的最后一个订阅连接断开
- **THEN** 该会话的活跃 Run 被取消

#### Scenario: 仍有订阅者时不取消

- **WHEN** 会话仍有其他订阅连接在线
- **THEN** 断开其中一个订阅者不取消活跃 Run，其余订阅者继续收到流式输出

### Requirement: 临时会话在空置时销毁

临时会话在无订阅连接时 MUST 被销毁；命名会话在无订阅时 SHALL 保留历史以供回放。

#### Scenario: 临时会话销毁

- **WHEN** 承载临时会话的对话结束或连接断开，且无其他订阅者
- **THEN** 该临时会话被移除

#### Scenario: 命名会话保留

- **WHEN** 命名会话的所有订阅者断开
- **THEN** 会话及其历史被保留，后续订阅者可回放

### Requirement: 活跃任务语义

会话订阅结果中的活跃任务列表 SHALL 仅包含当前在途的 Run，已完成或已取消的任务 MUST NOT 出现其中。

#### Scenario: 订阅时返回在途 Run

- **WHEN** 客户端订阅一个正在执行对话的会话
- **THEN** 返回的活跃任务列表包含该在途 Run 的标识

### Requirement: 取消不写入会话历史

被取消的 Run MUST NOT 向会话历史追加用户或助手消息。

#### Scenario: 取消后历史无半截轮次

- **WHEN** 一个 Run 在执行中被取消
- **THEN** 会话历史不新增该轮的记录
