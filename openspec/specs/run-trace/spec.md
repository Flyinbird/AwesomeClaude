# run-trace Specification

## Purpose

定义一次对话执行（Run）的统一标识命名，以及其执行轨迹事件流的对外契约——Run 的标识如何命名、轨迹事件的阶段与分组、以及轨迹如何持久化。

## Requirements

### Requirement: 对话执行标识统一为 run_id

所有对外接口 MUST 以 `run_id` 表示一次对话执行；`task_id` MUST NOT 再用于表示对话执行标识。

#### Scenario: 对话响应携带 run_id

- **WHEN** 客户端发起一次对话并收到最终响应
- **THEN** 响应中的执行标识字段名为 `run_id`

#### Scenario: 流式与工具通知携带 run_id

- **WHEN** 服务端推送 `chat.stream` / `chat.tool_started` / `chat.tool_finished` / `chat.interrupted` / `chat.user_message` 通知
- **THEN** 通知参数中的执行标识字段名为 `run_id`

#### Scenario: 会话状态返回活跃 Run

- **WHEN** 客户端订阅会话或查询会话状态
- **THEN** 返回的活跃执行标识列表字段名为 `active_runs`，其元素为在途 Run 的 `run_id`

### Requirement: 会话历史条目关联 run_id

会话历史中的每一轮用户与助手消息 MUST 关联其所属 Run 的 `run_id`。

#### Scenario: 历史回放携带 run_id

- **WHEN** 客户端订阅一个已有历史的会话
- **THEN** 回放的历史条目中包含对应的 `run_id`

### Requirement: 执行轨迹按 Run 独立持久化

一次对话执行的轨迹事件 MUST 持久化为以 `run_id` 命名的独立 JSONL 文件，位于按日期分目录的轨迹根之下。

#### Scenario: 轨迹文件以 run_id 命名

- **WHEN** 一个 Run 产生任意阶段事件
- **THEN** 事件写入 `{日志根}/{日期}/{run_id}.jsonl`

### Requirement: Trace 事件结构与阶段

每条轨迹事件 MUST 包含 `run_id`、阶段、时间戳、相对 Run 起点的耗时与上下文数据；Run 级阶段 MUST 覆盖创建、完成、失败、中断与取消，且多轮循环中的 step、LLM 与工具阶段 SHALL 保留并以轮次区分。

#### Scenario: Run 级阶段命名

- **WHEN** 一个 Run 被创建、完成、失败、中断或取消
- **THEN** 分别记录 `run_created` / `run_completed` / `run_failed` / `run_interrupted` / `run_cancelled` 阶段

#### Scenario: 多轮阶段带轮次

- **WHEN** 一个 Run 内发生多轮 LLM 调用与工具执行
- **THEN** 相应 step / LLM / 工具阶段事件携带轮次序号以区分轮次

### Requirement: 轨迹记录以 Run 为作用域

同一 Run 的所有阶段事件 MUST 归入该 Run 的同一份轨迹，且阶段耗时 MUST 相对该 Run 的单调时钟起点计算。

#### Scenario: 同一 Run 的事件汇聚

- **WHEN** 一个 Run 的多个处理阶段分别记录事件
- **THEN** 它们全部写入该 Run 的同一份轨迹，且耗时相对该 Run 的起点
