## Purpose

定义对话从「同步请求—响应」到「受理应答 + 终态通知」的异步完成契约：服务端在接受对话后立即确认，随后以通知方式投递完成、失败与周期心跳；客户端据此在无网络 deadline 的前提下判断对话终态与服务端存活。

## ADDED Requirements

### Requirement: 对话受理应答

服务端在受理并成功创建 Run 后 SHALL 立即向对话请求返回受理应答，应答 MUST 包含 `run_id` 与心跳间隔毫秒数 `heartbeat_interval_ms`；对话的最终结果 MUST NOT 通过该请求的响应返回。

#### Scenario: 受理成功返回 run_id

- **WHEN** 客户端发起一个参数合法的对话请求
- **THEN** 服务端在 Run 创建后立即返回包含 `run_id` 与 `heartbeat_interval_ms` 的受理应答，且不等待对话执行完成

#### Scenario: 非法参数仍以错误响应拒绝

- **WHEN** 对话请求缺少 `message`
- **THEN** 服务端返回 `-32602`（Invalid params）错误响应，且不创建 Run

#### Scenario: 会话忙仍以错误响应拒绝

- **WHEN** 目标会话已有活跃 Run，客户端又发起对话
- **THEN** 服务端返回 `-32004`（Session busy）错误响应，且不影响已有 Run

### Requirement: 对话完成通知

对话正常结束时，服务端 SHALL 推送 `chat.completed` 通知，携带 `run_id`、最终文本、停止原因、token 用量、耗时与模型；同一 Run 的完成通知 MUST 至多推送一次。

#### Scenario: 正常完成推送摘要

- **WHEN** 一次对话在步数上限内得到最终答复
- **THEN** 服务端推送一条 `chat.completed` 通知，包含该次对话的文本、停止原因、用量与耗时

#### Scenario: 达到步数上限仍推送完成

- **WHEN** 对话达到最大步数
- **THEN** 服务端仍推送 `chat.completed`，其停止原因为 `max_steps`

### Requirement: 对话失败通知

对话执行过程中发生错误时，服务端 SHALL 推送 `chat.failed` 通知，携带 `run_id` 与错误对象（至少含错误码与信息）；失败通知 MUST 与完成通知互斥，同一 Run 的失败通知 MUST 至多推送一次。

#### Scenario: LLM 错误推送失败通知

- **WHEN** 对话执行期间 LLM 调用返回认证失败或超时
- **THEN** 服务端推送 `chat.failed`，其错误码对应 LLM 错误码

#### Scenario: 内部异常推送失败通知

- **WHEN** 对话执行期间发生未预期的内部异常
- **THEN** 服务端推送 `chat.failed`，其错误码为 `-32603`（Internal error）

### Requirement: 服务端心跳

Run 执行期间服务端 SHALL 周期性地向关注该会话的客户端推送 `chat.heartbeat` 通知，心跳携带 `run_id`；心跳间隔 SHALL 可配置，并 MUST 在该 Run 进入终态后停止。

#### Scenario: 执行期间周期性心跳

- **WHEN** 一个 Run 持续执行且期间没有其他通知产生
- **THEN** 客户端仍能在心跳间隔内收到 `chat.heartbeat`

#### Scenario: 终态后停止心跳

- **WHEN** Run 已推送完成或失败通知
- **THEN** 服务端不再为该 Run 推送心跳

### Requirement: 终态与心跳的会话可见性

完成、失败与心跳通知 SHALL 对订阅该会话的所有连接可见；未指定会话的对话 SHALL 仅投递给发起连接。

#### Scenario: 共享会话内广播完成

- **WHEN** 同一命名会话存在多个订阅连接，其中一个发起对话
- **THEN** 该会话内所有订阅连接均能收到 `chat.completed` / `chat.failed` / `chat.heartbeat`

#### Scenario: 临时会话单播

- **WHEN** 客户端未携带 session_id 发起对话
- **THEN** 完成、失败与心跳通知仅投递给该发起连接

### Requirement: 客户端无 deadline 等待

客户端在收到对话受理应答后 MUST NOT 对对话完成施加固定的总时长限制；只要在心跳间隔的合理倍数内收到该 Run 的任何心跳或进度通知，客户端 MUST 持续等待终态通知。

#### Scenario: 长对话不因总时长超时

- **WHEN** 一次对话持续超过客户端控制类请求的超时时长，但期间持续收到流式、工具或心跳通知
- **THEN** 客户端继续等待，直到收到 `chat.completed` 或 `chat.failed`

### Requirement: 心跳看门狗

客户端 SHALL 以心跳间隔的固定倍数为阈值监测入站活动；当超过阈值未收到任何入站消息时，客户端 MUST 判定连接疑似中断、结束当前等待并向用户提示，同时 MUST NOT 因单次请求而主动断开底层连接。

#### Scenario: 心跳中断触发提示

- **WHEN** 客户端正在等待对话终态，且超过阈值未收到任何入站消息
- **THEN** 客户端提示连接疑似中断并结束本次等待，连接对象不因该次超时被直接关闭

#### Scenario: 正常心跳不触发提示

- **WHEN** 客户端在阈值内持续收到心跳或进度通知
- **THEN** 客户端不提示中断，继续等待终态
