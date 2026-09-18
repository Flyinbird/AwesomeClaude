## RENAMED Requirements

- FROM: `### Requirement: 活跃任务语义`
- TO: `### Requirement: 活跃 Run 语义`

## MODIFIED Requirements

### Requirement: 活跃 Run 语义

会话订阅与状态结果中的活跃执行列表 SHALL 仅包含当前在途的 Run，已完成或已取消的 Run MUST NOT 出现其中。

#### Scenario: 订阅时返回在途 Run

- **WHEN** 客户端订阅一个正在执行对话的会话
- **THEN** 返回的活跃 Run 列表包含该在途 Run 的标识

#### Scenario: 终态 Run 不出现在活跃列表

- **WHEN** 会话的 Run 已完成或已取消
- **THEN** 活跃 Run 列表中不包含该 Run
