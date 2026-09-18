## Purpose

定义一次对话执行（Run）内、由模型自主拆分并可动态演进的任务清单及其依赖关系（DAG）：任务的状态机、图约束、状态转换、失败语义，以及任务事件的轨迹记录与进度通知。

## ADDED Requirements

### Requirement: 运行期任务清单与依赖图

一次 Run SHALL 可维护一份任务清单；每个任务包含目标、状态与依赖项，依赖项构成有向无环图。未声明任务时，对话行为 MUST 与无此能力时一致。

#### Scenario: 模型声明任务清单

- **WHEN** 模型在对话中声明若干任务及其依赖
- **THEN** 运行时为本次 Run 建立任务清单与依赖图，并可供后续推进

#### Scenario: 未声明任务时行为不变

- **WHEN** 模型整个对话都未声明任何任务
- **THEN** 对话照常完成，且不产生任务事件

### Requirement: 任务三态且无失败状态

每个任务的状态 MUST 为 `pending`、`in_progress`、`completed` 之一；系统 MUST NOT 定义失败状态，失败 SHALL 通过任务回到 `pending` 表达。

#### Scenario: 失败回到 pending

- **WHEN** 模型将某进行中的任务标记为失败
- **THEN** 该任务状态回到 `pending`，并累计一次尝试次数、记录最近错误

### Requirement: 至多一个任务进行中

同一时刻同一 Run 内 MUST 至多有一个任务处于 `in_progress`。

#### Scenario: 已有进行中任务时不能启动另一任务

- **WHEN** 已有一个任务处于 `in_progress`，模型请求启动另一个任务
- **THEN** 该请求被拒绝并返回错误工具结果，已有任务不受影响

#### Scenario: 让位后可启动另一任务

- **WHEN** 进行中的任务被让位回 `pending`
- **THEN** 模型可以启动另一个依赖已满足的任务

### Requirement: 依赖顺序约束

任务 MUST NOT 在其依赖未全部完成时启动；违反时请求 MUST 被拒绝并返回错误工具结果，且 Run MUST NOT 因此终止。

#### Scenario: 依赖未满足时启动被拒

- **WHEN** 模型请求启动一个存在未完成依赖的任务
- **THEN** 请求被拒绝并返回错误工具结果

#### Scenario: 依赖满足后可以启动

- **WHEN** 某任务的全部依赖均已 `completed`
- **THEN** 模型可以启动该任务

### Requirement: 任务图可动态演进且始终有效

模型 SHALL 能在执行过程中追加任务，并可为其尚未启动的任务补充前置依赖。图在每次变更后 MUST 保持有效：无环、依赖指向存在的任务、且已 `completed` 任务的依赖不可再变更。

#### Scenario: 执行中追加任务

- **WHEN** 模型在已有任务执行期间追加新任务
- **THEN** 新任务进入清单且不影响已进行任务的推进

#### Scenario: 为待启动任务补前置

- **WHEN** 模型为一个 `pending` 任务补充新的前置依赖
- **THEN** 该任务在新增依赖完成前不可启动

#### Scenario: 会形成环的变更被拒

- **WHEN** 模型提交会使依赖图形成环的变更
- **THEN** 变更被拒绝并返回错误工具结果，图保持原状

#### Scenario: 引用不存在的依赖被拒

- **WHEN** 模型提交指向不存在任务的依赖
- **THEN** 变更被拒绝并返回错误工具结果

#### Scenario: 已完成任务的依赖不可变

- **WHEN** 模型请求修改一个已 `completed` 任务的依赖
- **THEN** 请求被拒绝

### Requirement: 失败重试与让位语义相互区分

任务因失败回到 `pending` 时 MUST 累计尝试次数并记录最近错误；任务为给前置任务让位而回到 `pending` 时 MUST NOT 累计尝试次数。

#### Scenario: 失败重试累计尝试次数

- **WHEN** 模型因失败将任务重新置为 `pending`
- **THEN** 该任务尝试次数加一，且最近错误被记录

#### Scenario: 让位不计尝试次数

- **WHEN** 任务为让位给前置任务而回到 `pending`
- **THEN** 该任务尝试次数不变

### Requirement: 任务事件记入执行轨迹

任务的每次状态转换 MUST 记录为所属 Run 轨迹中的事件；轮次与工具执行事件 MUST 关联其所属任务。

#### Scenario: 状态转换记录事件

- **WHEN** 任务被添加、启动、完成、重试或让位
- **THEN** 对应记录 `task_added` / `task_started` / `task_completed` / `task_reopened` / `task_suspended` 事件

#### Scenario: 轮次归属任务

- **WHEN** 某任务执行期间发生 LLM 调用或工具执行
- **THEN** 相应事件携带该任务的标识

### Requirement: 计划进度通知

任务清单发生变更时，运行时 SHALL 向该 Run 所属会话推送任务清单与状态快照；命名会话 MUST 广播给会话内订阅者，临时会话 MUST 单播回发起连接。

#### Scenario: 计划变更推送给订阅者

- **WHEN** 命名会话中的一个 Run 的任务清单发生变更
- **THEN** 会话内所有订阅者收到包含各任务状态的最新计划快照

#### Scenario: 临时会话单播

- **WHEN** 未指定会话的 Run 的任务清单发生变更
- **THEN** 发起连接收到计划快照

### Requirement: 对话结束时保留未完成任务状态

Run 进入终态时，其任务清单中未完成的任务 MUST 保留其最后状态，系统 MUST NOT 为其引入新的终态。

#### Scenario: 结束时仍有未完成任务

- **WHEN** Run 结束而任务清单中仍有 `pending` 或 `in_progress` 任务
- **THEN** 这些任务保持原有状态，且不产生失败终态
