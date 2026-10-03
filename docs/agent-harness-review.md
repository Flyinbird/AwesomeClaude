# AwesomeClaude Agent Harness 评审：技术架构总结与改进方向

> 评审对象：`/Users/chris/awesome-claude`（Python 3.12 / asyncio / uv，Client-Server 架构的类 Claude Code Agent 框架）
> 评审时间：2026-09-20
> 方法：通读 `README.md` / `AGENTS.md` / `docs/*`，逐模块精读 `src/awesome_claude/core/**`，并用代码事实交叉核对文档描述；结论区分「已验证（代码事实）」与「工程判断」。
> 与既有材料的关系：仓库根目录的 `arc_analysis.md` / `arc_analysis_simplified.md` 是更早一轮的同类评审。本文不是它的翻版，而是**重新核实后收敛到 `docs/` 的版本**，并在 §2.4 给出「上轮结论复核表」（哪些已修、哪些仍在）。建议后续以本文为基线，把根目录两份草稿归档。

---

## 一、技术架构总结

### 1.1 一句话架构

一个**双进程的 Agent 运行时**：`client`（CLI）只负责输入、命令解析与流式渲染；`core` 常驻守护进程承载连接、会话、Agent 编排、工具执行、LLM 调用与执行轨迹；两者之间**唯一的契约是 `protocol/` 包**（TCP Socket + 行式 JSON-RPC 2.0，`\n` 分帧），`core/` 与 `client/` 禁止互相 import。

### 1.2 分层与模块职责

```
client (CLI)  ──JSON-RPC 2.0 over TCP──  core (Daemon)
   cli: REPL / commands / renderer            server: tcp / session(连接级)
   transport: connection / receiver           router: dispatcher / context
                                              handlers: ping echo shutdown chat session.*
                                              agent: loop / prompt / events / result
                                              tools: base / context / registry / builtin
                                              task: task / graph(TaskGraph)
                                              session: registry / run / channel
                                              llm: base(LLMProvider) / anthropic_client
                                              observability: trace_recorder
shared: types / logging(app_logger, trace_store)
```

| 层 | 关键类型 | 职责与要点 |
| --- | --- | --- |
| 协议层 | `jsonrpc` / `methods` / `errors` | 消息构造解析校验、方法名与 TypedDict、错误码（标准码 + `-32001/2/3` LLM + `-32004 SESSION_BUSY`） |
| 连接层 | `TCPServer` / `ClientSession` | 逐行读取 → 分发 → 串行化回写；控制类请求内联处理，仅 `chat` 任务化为 Run，保证读循环在对话期间仍可感知断连 |
| 路由层 | `Dispatcher` / `HandlerContext` | 方法→handler 显式注册；每连接构造一次 `HandlerContext`，chat 用 `dataclasses.replace` 注入当次 `Run` |
| 编排层 | `AgentLoop` | 多轮「LLM → 工具 → 回填」循环；`on_event`（LLM 流式）/`on_step`（step/tool 结构事件）双回调透出，自身**零网络依赖**，可被测试替身替换 |
| 提示层 | `build_system_prompt` / `PromptContext` / `PROMPT_VERSION` | 按「身份准则 + 环境（工作区/OS/时间/模型/限额）+ 工具细则 + 大文件分块策略 + 输出风格」拼装；版本号写入 `context_built` 轨迹 |
| 工具层 | `Tool` / `ToolContext` / `ToolScope` / `ToolRegistry` | `ToolContext` 是进程级环境（沙箱根 + 读写限额），`ToolScope` 是 per-Run 运行态（`run_id` + `task_graph`）；注册表统一转 schema、捕获异常转错误 `ToolResult`（不抛穿） |
| 计划层 | `TaskGraph` / `Task` | per-Run 任务 DAG：三态 `pending/in_progress/completed`，变更前校验「依赖存在、无环、至多一个 in_progress」，`reopen_task` 记 `attempts/last_error`，`suspend_task` 让位不计次 |
| 会话层 | `SessionRegistry` / `Session` / `Run` / `SessionChannel` | 会话 = 订阅连接集合 + 历史 + 至多一个在途 Run；`Run` 状态机 `RUNNING → {COMPLETED, FAILED, INTERRUPTED, CANCELLED}`，终态只写一次；零订阅取消、临时会话即毁 |
| 模型层 | `LLMProvider` / `AnthropicClient` | 供应商无关协议 + Anthropic SDK 实现；流式解析 text/thinking/tool_use，异常归一为 `LLMError` 子类层级 |
| 观测层 | `TraceRecorder` / `TraceStore` | Run 作用域轨迹，`step_index` 区分轮次，逐行写 `logs/runs/{date}/{run_id}.jsonl` |

### 1.3 一次 chat 的完整链路（已验证）

```
用户输入
 → client: send_request("chat", {message, session_id?})          # id=N
 → core/server/session._start_chat
     1) 建 Run + recorder.run_created()
     2) 立即回受理 ack {run_id, accepted, heartbeat_interval_ms}   # 请求与结果解耦
     3) 起心跳任务，周期推 chat.heartbeat
     4) 异步派发 handle_chat:
          a. ContextBuilt（含 system prompt 与 messages 快照，长文本已裁剪）
          b. TaskGraph(run_id, on_change) + ToolScope(run_id, graph)
          c. agent_loop.run(message, system, on_event, on_step, scope)   ← 注意：未传 history
             └─ 每轮：StepStarted → LLM_REQUEST_SENT → LLM_STREAMING
                      → TextDelta → chat.stream（逐块）
                      → ToolStarted/ToolFinished → chat.tool_* + 轨迹
                      → LLM_RESPONSE_DONE → 下一轮
          d. chat.stream{is_final:true}；session 记录本轮 history（供回放）
          e. recorder.run_completed() / run_interrupted() / run_failed()
     5) 停心跳后广播终态 chat.completed / chat.failed（带 usage、duration_ms、stop_reason）
 → client 渲染摘要；期间依赖 3× 心跳间隔做看门狗
```

阶段枚举（`TraceStage`）：`run_created / context_built / step_started / llm_request_sent / llm_streaming / llm_response_done / tool_started / tool_completed / tool_failed / task_* / run_completed / run_failed / run_interrupted / run_cancelled`。

### 1.4 关键设计取舍

| 设计 | 收益 | 代价（当前代价） |
| --- | --- | --- |
| 请求/结果解耦（受理 ack + 通知终态） | 长任务不被 RPC 超时绑架；多客户端可同时观看同一 Run | 客户端必须自带看门狗；错误路径分散在两个通道 |
| Run 由会话拥有、终态幂等 | 生命周期清晰、断连/关服可确定收尾 | 每会话单 Run → 无法并发/中途插话（无 steer/queue） |
| 回调透出（`on_event`/`on_step`） | `AgentLoop` 可单测、可替换、不绑传输 | 上层 handler 变胖（`chat.py` 450 行，混了轨迹、通知、任务、错误映射） |
| 工具错误转 `ToolResult(is_error=True)` | 失败可被模型自我修复，Run 不中断 | 错误只有自由文本，无类别/可重试语义 |
| 工具入参截断 → 跳过并回填提示 | 避免「截断成空参数」的静默误执行 | 属于**事后补救**：没有事前预算，也没把 `input_json_delta` 用于流式校验 |
| System Prompt 由环境动态拼装 | 模型知道沙箱根、限额、工具用法，行为显著收敛 | 工具描述与 `Tool.input_schema` **两处维护**，易漂移 |
| 沙箱只做路径收敛（realpath ∈ workspace_root） | 实现简洁、行为可预测 | 无拒绝名单（`.env` 可读）、无审批、无执行工具时的资源/网络策略 |

### 1.5 当前能力边界

**已有**：流式对话、多轮工具编排、System Prompt、文件四件套（`read_file/write_file/edit_file/list_dir`）+ `get_time` + 计划工具六件套、任务 DAG、多客户端会话共享与历史回放、Run 生命周期与取消、step 化执行轨迹、结构化日志。

**尚无**：跨轮记忆回填、上下文预算与压缩、搜索类工具（`glob`/`grep`）、执行类工具（`bash`）、权限/审批层、中途取消与续跑（`chat.cancel`/`resume`）、子 Agent、Hooks/插件（MCP）、成本核算与指标、离线评测闭环、多模态。

---

## 二、Agent Harness 工程视角的评估

一个 harness 的竞争力，最终由六个子系统决定：**上下文工程、记忆、循环控制（控制流与预算）、工具面与工具契约、安全与权限、可观测性与评测**。下面先给已经站得住的部分，再逐项说差距。

### 2.1 已经站得住、应当作为基线的部分

1. **控制循环与 I/O 彻底解耦**（`AgentLoop` 零网络依赖）——这是很多自研 harness 一开始就做错的地方（把 WebSocket 写进 loop），后期极难测试与替换。
2. **失败不抛穿**：`ToolRegistry.execute` 把「未知工具 / 任意异常」都转成 `is_error` 结果回填模型，让模型有机会自我纠正，而不是让整个 Run 崩掉。
3. **生命周期建模完整**：`Run` 单一终态 + 幂等 `finish`、零订阅取消、取消不写历史（避免半截对话污染回放）。这类「脏路径显式建模」在 agent 场景比 happy path 重要得多。
4. **双回调事件模型**（token 级 `on_event` + 语义级 `on_step`）——让 UI/轨迹/指标可以各自订阅，天然是「可观测性靠事件而非日志文本」的正确形态。
5. **step 化轨迹 + `step_index`**：`logs/runs/{date}/{run_id}.jsonl` 已经能回答「这一轮模型看到了什么、调了什么、拿回什么」，这是做评测和成本归因的稀缺底座。
6. **工具执行上下文分层**（`ToolContext` 环境 / `ToolScope` 运行态）：环境（沙箱、限额）与运行态（任务图、run_id）分离，后续加并发 Run、加权限主体时不用推倒重来。
7. **System Prompt 已是「构造物」而非字面量**：`PROMPT_VERSION` 入库，为 prompt 回归实验留了钩子。

### 2.2 不足与改进方向

按优先级 P0（正确性/成本，收益最高）→ P1（能力跃升）→ P2（平台化）标注。

#### A. 上下文工程（当前最大短板）

- **A1（P0）会话历史未回填，多轮对话实际是「无记忆」的。**
  事实：`Session.history` 由 `SessionChannel.record_turn` 写入，只用于 `session.attach` 回放；`handle_chat` 调用 `agent_loop.run(message, system=..., on_event=..., on_step=..., scope=...)` **没有传 `history=`**，而 `AgentLoop.run` 里 `messages = list(history) if history else []` 恰好支持这个参数。也就是说：同一个会话里第二轮 LLM 根本看不到第一轮内容，而客户端 UI 会显示连续对话——**这是「看起来对、实际错」的静默缺陷**。
  影响：所有需要追问、修订、多轮协作的任务全部退化为单轮；用户被迫把上下文重复粘贴进每次输入。
  改进：`handle_chat` 从 `SessionRegistry` 取 `history` 并投影为 Anthropic 消息格式传入 `history=`；同时给历史加**上限与裁剪策略**（见 A2），并补一条 e2e 断言「第二轮能引用第一轮内容」。

- **A2（P0）没有上下文预算，只有单工具限额。**
  事实：单次 `read_file` 上限 30000 字节、`write_file` 100000 字节，但**没有任何机制限制单次 Run 的 messages 总量**。一次 `read_file` 就能塞进约 8k token，多轮读取后历史里会永久堆积这些块；也没有对 `tool_result` 做 head+tail 截断（`_clip_text` 只作用于**轨迹日志**，不进模型上下文）。
  影响：长任务必然撞上下文上限 → 以 API 4xx 结束（且没有「prompt too long」的精准映射，容易被归到 `-32003 LLM_ERROR`），成本不可控。
  改进：引入 `ContextBudget`（按 token 估算）：① 每个 `tool_result` 入库前做「head+tail+省略标记+可重新读取提示」的截断；② 超预算时按 **裁剪无用小结果 → offload 大结果到文件并留引用 → 摘要旧轮次** 三档降级；③ 把 `prompt too long` 映射为专用错误码并触发一次自动压缩重试。

- **A3（P1）没有项目级指令注入（CLAUDE.md/AGENTS.md）。**
  事实：`PromptContext` 只注入工作区路径、限额、模型、工具清单；仓库里的 `AGENTS.md`（项目规范、架构约束、命令）从未进入模型上下文。
  影响：模型无法遵循项目约定（命名、错误码、禁止 print、结构演进要求），每次都靠人肉在 prompt 里重复。
  改进：`PromptContext` 增 `instructions` 字段，按「仓库根 `AGENTS.md` → 子目录 `AGENTS.md`」分层加载（带缓存与大小上限），并在 `context_built` 记录其 hash 以便复现。

#### B. 循环控制（预算与止损）

- **B1（P0）`max_steps` 不可配置、缺少其他预算维度。**
  事实：`core/app.py` 用 `AgentLoop(llm_client, tool_registry)` 构造，走默认 `max_steps=25 / finalize=True`；`.env` 里没有对应项；也没有墙钟预算、token 预算或成本上限。
  影响：不同模型/任务需要不同步数，改行为必须改代码；一次失控 Run 可以烧掉可观的 token 而不触发任何熔断。
  改进：`ServerConfig` 增 `agent_max_steps` / `agent_max_tokens` / `agent_timeout`，透传到 `AgentLoop`；超预算时走已有的 `finalize` 收尾路径并记 `run_interrupted`。

- **B2（P1）缺少「无进展」检测。**
  事实：循环只在「模型不再请求工具」或「撞 max_steps」时退出。若模型反复调用同一工具同一参数（例如反复 `list_dir`），会一路烧到第 25 步。
  影响：token 浪费、延迟差、失败体验差。
  改进：在 `AgentLoop` 内维护 `(tool_name, canonical(args))` 指纹计数，重复达阈值即回填「已执行过同样的调用，结果如下」并按需附上语义提示（而不是再执行一遍）；同时对连续 `is_error` 结果做同类合并。

- **B3（P1）工具串行执行。**
  事实：`for tool_use in outcome.tool_uses:` 逐个 `await`，而 Anthropic 支持一次返回多个 tool_use（常见的 2~4 个独立读取）。
  影响：多工具任务延迟线性叠加。
  改进：在「同一步内互不依赖」的前提下 `asyncio.gather` 并发执行（保持 tool_result 顺序与 `tool_use_id` 对齐），并用 `ToolScope` 之外的无状态保证做正确性约束。

#### C. 工具面与工具契约

- **C1（P0）缺搜索与执行，工具面不成闭环。**
  事实：注册表当前只有 `get_time` + 文件四件套 + 计划六件套（`core/app.py` 装配点）。没有 `glob`/`grep`，也没有任何命令执行工具。
  影响：模型「知道要改什么」但「找不到、验证不了」。定位代码只能靠 `list_dir` 逐层猜 + 整文件读，token 消耗大；改完无法跑测试/格式化，无法自我验证，正确率上限被锁死。
  改进：按顺序补 ① `glob`/`grep`（带行号、结果条数上限、跳过 `.git`/`.venv`/`node_modules`）；② `bash`（先只读白名单 → 再审批制）。提示词里同步加「改完必须验证（跑测试/lint）」的准则。

- **C2（P1）工具契约偏弱：无错误分类、无幂等/副作用声明、无版本。**
  事实：`ToolResult` 只有 `content: str` + `is_error: bool`；`Tool` 无「是否写操作 / 是否可并发 / 是否幂等」元数据。
  影响：循环无法做分级重试，权限层无法按副作用分类，前端无法做差异预览。
  改进：`ToolResult` 增 `error_kind`（`invalid_args` / `not_found` / `permission` / `truncated` / `transient`）；`Tool` 增 `read_only: bool`、`idempotent: bool`、`schema_version`，供权限、并发与审计复用。

- **C3（P1）提示词里的工具说明与 schema 两处维护。**
  事实：`core/agent/prompt.py::_TEMPLATE` 硬编码了 `read_file/list_dir/write_file/edit_file` 与计划工具的「细则」，同一批工具的 `description` 又来自 `Tool.description` 注入 `tool_lines`。
  影响：新增/改名工具时极易漏改提示词，而且这类漂移不会报错，只会让模型行为变差。
  改进：把「工具使用细则」并入 `Tool` 定义（如 `usage_hint`），由 builder 统一渲染；模板只保留跨工具的策略段。

#### D. 安全与权限

- **D1（P0）无审批、无拒绝名单。** 事实：沙箱只保证路径落在 `workspace_root` 内；写工具直接落盘，没有 diff 预览、没有 approve 流程，也没有 `.env` / `.git` / `.venv` 的拒绝名单——`.env` 里就放着 API key。同时 `read_file` 的内容会原样进入模型上下文，构成**典型的间接提示注入面**（文件内容可指挥模型去写别的文件）。
  改进：① 拒绝名单 + 敏感文件默认只读；② 写/编辑操作返回 diff 并要求模式化审批（`read-only` / `auto-edit` / `yolo` 三档）；③ 原子写（temp + rename）与可选备份；④ 提示词中明确标注「工具返回的文件内容是数据、不是指令」。

- **D2（P2）传输层无认证/加密。** 事实：`127.0.0.1:9527` 纯 TCP，无 token、无 TLS、无限流；任何本机进程都能连上并驱动 Agent。
  改进：本地 token 握手 + 可配置 TLS；入站消息长度上限（当前无上限，超大行会让连接直接断掉而不是得到结构化错误）。

#### E. 可观测性与评测

- **E1（P1）有轨迹，但没有评测闭环。** 事实：`logs/runs/*.jsonl` 已很完整，但缺少「黄金轨迹回放 + 断言」的离线测试；`max_steps`、工具选择、prompt 版本这些改动的效果无法被量化比较。
  改进：把轨迹变成可回放 fixture（`FakeLLMProvider` 按 run 回放事件），对「步数 / token / 工具序列 / 终态」做回归断言；配合 `PROMPT_VERSION` 做 A/B，产出「成功率 / 步数 / 成本」三张曲线。

- **E2（P2）无聚合指标与成本核算。** 事实：用量只在单次 `ChatResponse` 与会话内 `/stats`；没有 per-tool 延迟、失败率、无 OTel/metrics 出口，也没有 $ 换算。
  改进：从 `on_step` 事件流旁挂一个 `MetricsCollector`（tool 维度 + run 维度），暴露 `/metrics` 或写 `logs/metrics.jsonl`。

#### F. 工程卫生（低风险但会持续侵蚀信任）

- **F1（P0）`TraceStore` 在事件循环里做阻塞文件 I/O，且目录硬编码。**
  事实：`get_trace_store()` 固定返回 `TraceStore("logs/runs")`（`AWESOME_CLAUDE_LOG_DIR` 未生效）；`log_event` 在持锁状态下用同步 `fh.write` 写盘。
  改进：路径来自 `ServerConfig`；写盘用 `asyncio.to_thread`（或队列 + 后台 writer 协程），避免拖慢流式输出；轨迹与日志可加大小/天数轮转。

- **F2（P1）文档与实现漂移。** 事实：「架构文档未纳入 plan/task」，`max_steps` 语义只在 docstring 里，README 的 Phase 3 表述与现状（prompt/计划工具已落地）不一致。
  改进：把阶段名、日志路径、方法名等常量从代码导出到文档；每个变更在 `openspec` 中加「文档同步」检查项。

- **F3（P2）遗留占位模块。** 事实：`shared/logger.py`、`shared/config.py` 已无引用却仍在树里；根目录另有 `arc_analysis.md` / `arc_analysis_simplified.md` / `user.txt` 等评审草稿与临时数据。
  改进：删除未引用模块；评审文档收敛到 `docs/`。

### 2.4 上轮评审结论复核（相对 `arc_analysis.md`）

| 上轮结论 | 现状 | 依据 |
| --- | --- | --- |
| 无 System Prompt | ✅ 已修 | `core/agent/prompt.py` + `PROMPT_VERSION` 入轨迹 |
| `max_steps` 死参数（写死 100） | ⚠️ 部分修 | 默认值 25 已生效并在 docstring 声明，但仍**不可配置**（见 B1） |
| 会话历史不回填 | ❌ 仍存在 | `handle_chat` 未传 `history=`（见 A1） |
| 无上下文压缩 | ❌ 仍存在 | 无 budget/裁剪（见 A2） |
| 无 glob/grep/shell | ❌ 仍存在 | `app.py` 装配的仍是 time + fs + plan（见 C1） |
| TraceStore 阻塞写 + 目录硬编码 | ❌ 仍存在 | `trace_store.py` 持锁同步写、`get_trace_store()` 写死 `logs/runs`（见 F1） |
| 沙箱可读 `.env` | ❌ 仍存在 | 仅做路径收敛，无拒绝名单（见 D1） |
| 无取消/审批/续跑 | ⚠️ 部分修 | Run 取消已闭环（零订阅/关服），但**无客户端主动取消/插话/续跑**（见 §2.2 B1、D1） |

---

## 三、改进路线图

### P0（正确性/成本，建议 1~2 周内）

| # | 事项 | 对应 | 验收标准 |
| --- | --- | --- | --- |
| 1 | 会话历史回填 `agent_loop.run(history=...)` | A1 | 同会话第二轮能引用第一轮内容（e2e 断言），轨迹 `context_built.message_count` 随之增长 |
| 2 | 工具结果入库截断 + 单 Run 上下文预算 | A2 | 连续大文件读取不再撞 `prompt too long`；超预算触发压缩而非报错 |
| 3 | `max_steps` / token / 墙钟预算可配 | B1 | env 可调；超限走 finalize 并记 `run_interrupted` |
| 4 | 工具错误分类 + 重复调用检测 | B2/C2 | 重复 `(tool, args)` 不再真正执行；错误可被分级重试 |
| 5 | 沙箱拒绝名单 + 写操作 diff/审批开关 | D1 | 读 `.env` 被拒并回填明确错误；写操作可要求审批 |
| 6 | TraceStore 非阻塞写 + 目录来自 config | F1 | 事件循环无同步写；`AWESOME_CLAUDE_LOG_DIR` 生效 |
| 7 | `glob` / `grep` 工具 + 「改完要验证」提示词准则 | C1 | 定位代码的平均读文件数下降；任务能自我验证 |

### P1（能力跃升，1 个月左右）

8. `bash`/`exec` 受限执行（只读白名单起步，逐步放开）——闭环「读→改→验」。 C1
9. 项目级指令注入（`AGENTS.md` 分层加载，hash 入轨迹）。 A3
10. 工具并行执行 + 工具契约元数据（`read_only`/`idempotent`/`error_kind`）。 B3/C2
11. `chat.cancel` / `chat.steer` / `chat.resume` + attach 回放在途 Run。 B1
12. 轨迹回放评测（黄金轨迹断言）+ `PROMPT_VERSION` A/B。 E1
13. 工具细则单一来源（`usage_hint` 渲染）。 C3
14. 周期记忆摘要（跨 Run 的项目/会话长期记忆，配合 A2 的摘要产物）。 A1

### P2（平台化，1 个季度左右）

15. 子 Agent 委派（独立上下文与预算的 `task` 工具），支撑长任务并行分解。
16. Hooks（pre/post tool use）+ 插件/工具提供者协议（MCP 接入，工具动态装载而非 `app.py` 硬编码）。
17. 持久化（SQLite：会话/历史/轨迹）+ 跨会话检索式记忆。
18. Metrics/OTel 出口 + `$` 成本核算 + `trace <run_id>` 渲染命令。
19. 传输层认证/TLS/限流；多模态（图片读取）。
20. 清理漂移与遗留：文档随代码生成、删除未引用模块、评审草稿收敛到 `docs/`（F2/F3）。

---

## 四、总评

**这个 harness 的「骨架」质量明显高于同类自研项目**：协议契约单一、Run 终态必须唯一、脏路径（断连/关服/取消）被显式建模、控制循环与 I/O 解耦、轨迹按 step 结构化——这些恰恰是后期最难补的部分，应当原样保留为基线。

真正的差距集中在「Agent 的脑子与预算」上，且有一个明确的因果链：**没有历史回填 → 多轮退化为单轮**；**没有上下文预算 → 长任务撞限并烧钱**；**没有搜索/执行工具 → 改不了也验不了，正确率封顶**；**没有审批与拒绝名单 → 能力越强越危险**；**没有评测闭环 → 上述改动都无法被量化**。因此 P0 的 7 项不是「锦上添花」，而是把当前「管道干净的运行时」变成「会好好干活的 Agent」的最小必要集合；其中第 1、2、4、5 项的投入产出比最高。

---

## 附录：如何验证本文结论

```bash
# 1. 历史未回填：同一会话连问两轮，看第二轮轨迹的 messages 数是否仍为 1
uv run python -m awesome_claude.core.app &
uv run python -m awesome_claude.client.cli --session demo
cat logs/runs/$(date +%F)/*.jsonl | grep context_built

# 2. 无上下文预算：让 Agent 连续读多个大文件，观察是否出现 prompt too long
# 3. max_steps 不可配：grep AgentLoop( src/awesome_claude/core/app.py
uv run mypy src/ && uv run pytest -q
```

> 维护约定：本文与 `docs/architecture.md` 同步演进；每次影响 Agent 行为（prompt、循环、工具契约）的变更，请更新 §2.2 对应条目与 §2.4 复核表。

