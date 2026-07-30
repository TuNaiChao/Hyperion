# deer-flow 运行时上下文管理机制调研报告

> 调研对象：`deer-flow/backend/packages/harness/deerflow/`（harness 包，import 前缀 `deerflow.*`）
> 调研目标：为 Hyperion 自建 runtime harness 提供对标依据。
> 引用格式：`(文件:行号)`，仅报源码核实的事实，不臆测。
> 路径前缀 `DF = deer-flow/backend/packages/harness/deerflow/`。

---

## 1. 中间件机制（Middleware）

### 1.1 基类与签名

deer-flow **不自带 Middleware 基类**，完全复用 LangChain 1.x 的 `langchain.agents.middleware.AgentMiddleware`（即 LangGraph 官方 agents 模块）。所有中间件都继承该类并按需 override 钩子。Generic 参数是 state schema：

- `(DF/agents/middlewares/token_budget_middleware.py:39,62)` `from langchain.agents.middleware import AgentMiddleware` + `class TokenBudgetMiddleware(AgentMiddleware[AgentState])`
- `(DF/agents/middlewares/sandbox_audit_middleware.py:11,198)` `class SandboxAuditMiddleware(AgentMiddleware[ThreadState])`
- `(DF/agents/middlewares/dynamic_context_middleware.py:39,128)` `class DynamicContextMiddleware(AgentMiddleware)`

可 override 的钩子（源码中实际出现的）：

| 钩子 | 同步/异步 | 作用 | 示例位置 |
|---|---|---|---|
| `before_agent(state, runtime)` / `abefore_agent` | 同步+异步 | run 开始前一次性准备（路径、sandbox、计数初值） | `token_budget_middleware.py:120,142`; `thread_data_middleware.py:82` |
| `after_agent(state, runtime)` / `aafter_agent` | 同步+异步 | run 结束清理 | `token_budget_middleware.py:146,152` |
| `before_model(state, runtime)` / `abefore_model` | 同步+异步 | **每次调用 LLM 前**：可改写 messages、注入提示、压缩历史 | `summarization_middleware.py:452,455`; `durable_context_middleware.py:210` |
| `after_model(state, runtime)` / `aafter_model` | 同步+异步 | **每次 LLM 返回后**：检查 usage、改写 AIMessage、剥离 tool_calls | `token_budget_middleware.py:275,279` |
| `wrap_model_call(request, handler)` / `awrap_model_call` | 同步+异步 | **包住整个模型调用**：可改写 `request.messages`、注入隐藏 HumanMessage；返回 `ModelCallResult` | `token_budget_middleware.py:303,311` |
| `wrap_tool_call(request, handler)` | 同步+异步 | 包住单次工具调用 | `tool_output_budget_middleware.py`（见下） |

钩子的两个核心数据结构（来自 `langchain.agents.middleware.types`，`(DF/agents/middlewares/token_budget_middleware.py:40)`）：
- `ModelRequest` —— 包含 `messages` 列表 + `runtime`；可 `request.override(messages=...)` 改写后转发
- `ModelResponse` / `ModelCallResult` —— handler 出参

`Runtime` 对象（`langgraph.runtime.Runtime`）携带 `runtime.context` 字典（运行时上下文：thread_id、user_id、run_id、app_config、authz 等）和 `runtime.state`。

每个中间件可声明 `state_schema`（`(DF/agents/middlewares/thread_data_middleware.py:37)` `state_schema = ThreadDataMiddlewareState`），让本中间件需要的额外字段加入 graph state schema；`normalize_middleware_state_schemas()` 在 factory 里把它们统一合并到 ThreadState（`(DF/agents/factory.py:157)`）。

### 1.2 中间件链组装

**两套并行组装路径**（差异：lead-agent 用全部 14–34 个；subagent 用精简版）：

**(A) SDK 路径** —— `create_deerflow_agent()` `(DF/agents/factory.py:64)`
- 入参 `middleware`（full-takeover）/ `features: RuntimeFeatures`（声明式 flag）/ `extra_middleware`（带 `@Next` / `@Prev` 锚点的插入）。`middleware` 与 `features`/`extra_middleware` 互斥（`(DF/agents/factory.py:120-135)`）。
- 内部 `_assemble_from_features(feat, ...)` `(DF/agents/factory.py:178-349)` 按 **固定顺序** append 14 个中间件（顺序表见 docstring `(DF/agents/factory.py:188-211)`）。
- `_insert_extra(chain, extras)` `(DF/agents/factory.py:357-430)` 实现 `@Next(AnchorClass)` / `@Prev(AnchorClass)` 类级装饰器锚点插入（基于 `_next_anchor` / `_prev_anchor` 类属性），保证 `ClarificationMiddleware` 永远在末尾 `(DF/agents/factory.py:345-347)`。
- 最终 `(DF/agents/factory.py:162-170)` `create_agent(model=..., tools=..., middleware=effective_middleware, system_prompt=..., state_schema=effective_state, checkpointer=...)`。

**(B) Lead-Agent 应用路径** —— `make_lead_agent(config)` `(DF/agents/lead_agent/agent.py:498)` → `_make_lead_agent` `(DF/agents/lead_agent/agent.py:526)` → `build_middlewares(...)` `(DF/agents/lead_agent/agent.py:272)`
- 共享基座 `build_lead_runtime_middlewares(...)` 在 `(DF/agents/middlewares/tool_error_handling_middleware.py)` 中（docstring 第 1-13 项：InputSanitization → ToolOutputBudget → ToolResultSanitization → ThreadData → Uploads → Sandbox → DanglingToolCall → LLMErrorHandling → Authorization/Guardrail → SandboxAudit → ReadBeforeWrite → ToolProgress → ToolErrorHandling）。
- 然后 lead-only 在 `build_middlewares` 里 **append**：DynamicContext → SkillActivation → SkillToolPolicy → DurableContext → Summarization → Todo → TokenUsage → Title → Memory → ViewImage → McpRouting → DeferredToolFilter → SystemMessageCoalescing → SubagentLimit → LoopDetection → TokenBudget → Custom → Configured extensions → TerminalResponse → ModelLengthFinishReason → SafetyFinishReason → Clarification（顺序见 `AGENTS.md` 中间件章节）。
- 中间件列表最终经 `normalize_middleware_state_schemas(middlewares, mode)` 处理后传给 `create_agent`。

**关键观察：顺序敏感**。LangGraph 反向 dispatch `after_model`，所以注册顺序决定 wrap 嵌套层次；docstring 在多处显式 pin 顺序（如 SkillActivation 必须在 SkillToolPolicy 前、DurableContext 必须在 Summarization 前）。

---

## 2. 上下文管理（Condenser 思想）

### 2.1 SummarizationMiddleware —— LLM 摘要 + 保留尾部窗口

**(DF/agents/middlewares/summarization_middleware.py:97)** `class DeerFlowSummarizationMiddleware(SummarizationMiddleware)`（继承 langchain 上游 `SummarizationMiddleware`，扩展 hooks + 多模型 fallback）。

**触发条件**（`_prepare_compaction` `(DF/agents/middlewares/summarization_middleware.py:458-481)`）：
1. `_messages_for_trigger_count(messages, previous_summary)` `(DF/agents/middlewares/summarization_middleware.py:337-340)` —— 若已有 summary，把它当成一条名为 `"summary"` 的 HumanMessage 拼到末尾参与计数（防 summary 自身不计入导致死循环）。
2. `total_tokens = self.token_counter(trigger_messages)` —— 上游 langchain `SummarizationMiddleware` 的 token_counter（通常 tiktoken）。
3. `_should_summarize(trigger_messages, total_tokens)` —— 上游方法，按配置的 `trigger`（`(tokens, N)` / `(messages, N)` / `(fraction, f)` 三种）判定 `(DF/agents/middlewares/summarization_middleware.py:470)`。

**压缩策略**（`compact_state` `(DF/agents/middlewares/summarization_middleware.py:483-518)` / `_amaybe_summarize` `(DF/agents/middlewares/summarization_middleware.py:559)`）：
1. `_determine_cutoff_index(messages)` —— 上游：保留 `keep` 策略（如 `("messages", 10)` 保留最后 10 条；`("tokens", 2000)` 保留最后 2000 tokens）。
2. `_partition_messages(messages, cutoff_index)` —— 拆成 `messages_to_summarize` + `preserved_messages`。
3. `_preserve_dynamic_context_reminders(...)` `(DF/agents/middlewares/summarization_middleware.py:571-623)` —— 把隐藏的 dynamic_context reminder（携带日期/memory）和 ID-swap 三联消息救回 preserved。
4. `_summarize_with(messages_to_summarize, previous_summary)` `(DF/agents/middlewares/summarization_middleware.py:247-271)` —— **LLM 摘要**（不是滑窗挑选关键消息），调用 `_model_for(name).ainvoke(prompt)`，prompt 是 `<existing_summary>...<new_messages>...` 结构（`(DF/agents/middlewares/summarization_middleware.py:415-435)`），对 previous_summary 和 new_messages 各自做 token 预算裁切（`trim_tokens_to_summarize` 配置项）。
5. **多模型 fallback**（`(DF/agents/middlewares/summarization_middleware.py:164-186, 266-271)`）—— 候选顺序：[配置的 summary model, run 自己的 model, 默认 model, None]；任一构造失败或返回空都降级到下一个，最坏情况跳过本轮压缩（不抛异常）。
6. **状态写回**（`(DF/agents/middlewares/summarization_middleware.py:547-569)`）：
   ```python
   {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *preserved], "summary_text": summary}
   ```
   `REMOVE_ALL_MESSAGES` 是 LangGraph 的特殊 id，表示「清空 messages 通道，然后追加 preserved」。`summary_text` 是独立 channel（不在 messages 里），由 `DurableContextMiddleware` 在下一次 `before_model` 投影到模型请求。

**钩子触发**：`before_model` → `_maybe_summarize`（每轮 LLM 前自动跑；不达阈值返回 None）。

**额外**：`compact_state(force=True)` 也被 `runtime/context_compaction.py:99` 的 `compact_thread_context()` 复用为「手动 `/compact` 路径」——通过 `CheckpointStateAccessor.aupdate` 配合 `Overwrite` reducer 替换 messages + 写新 checkpoint。

### 2.2 TokenBudgetMiddleware —— 累计 token 预算 + 软警告 + 硬停

**(DF/agents/middlewares/token_budget_middleware.py:62)** `class TokenBudgetMiddleware(AgentMiddleware[AgentState])`。

**机制**（`_apply` `(DF/agents/middlewares/token_budget_middleware.py:187-272)`）：
- 在 `before_agent` `(DF/agents/middlewares/token_budget_middleware.py:120-139)` 把已有 AIMessage 的 `usage_metadata` 全部记入 `_seen_messages[run_id]`，**不计入本 run 预算**（只数本轮新增）。
- 在 `after_model` `(DF/agents/middlewares/token_budget_middleware.py:275)` 累加每条 AIMessage 的 `input_tokens/output_tokens` diff（处理 TokenUsageMiddleware 追溯补记子 agent token 的情况）。
- 三档阈值（`(DF/agents/middlewares/token_budget_middleware.py:228-245)`）：`max_tokens`、`max_input_tokens`、`max_output_tokens`，取用得率最高的那档。
- **warn 阈值**（默认 0.7）：`_pending_warnings[run_id].append(...)`，由 `wrap_model_call` 在下次调用时注入 `HumanMessage(name="budget_warning")` 到 request 末尾 `(DF/agents/middlewares/token_budget_middleware.py:291-300, 303-308)`。
- **hard_stop 阈值**（默认 1.0）：`_build_hard_stop_update` `(DF/agents/middlewares/token_budget_middleware.py:170-185)` —— **剥离 AIMessage 的 `tool_calls`、改 `finish_reason=stop`、追加停止说明文本**，让 agent loop 自然终止产出最终答复（**不抛异常**）。同时记 `_stop_reason[run_id] = "token_capped"` 供 `consume_stop_reason(run_id)` 读取。

**线程安全**：所有 run 状态用 `BoundedDict(1000)`（`(DF/agents/middlewares/token_budget_middleware.py:71-78)`，防 abandoned run 泄漏）+ `threading.Lock`。

**subagent 默认值**：`max_tokens` 与 `summarization.enabled` 耦合——开摘要默认 1M tokens，关摘要默认 2M（docstring + AGENTS.md）。

### 2.3 ToolOutputBudgetMiddleware + tool_output_synopsis —— 防单工具撑爆上下文

**两个文件分工**：

**`tool_output_synopsis.py:63`** `build_tool_output_synopsis(content, tool_name)` —— **纯函数、不调 LLM**，按内容类型（json/csv/tsv/yaml/xml/code/text）产出 `ToolOutputSynopsis(kind, title, summary, structure, notable_items, sample)`：
- 入口先 `_MAX_SYNOPSIS_INPUT_BYTES = 5_000_000` 守门 `(DF/agents/middlewares/tool_output_synopsis.py:39, 77-87)`，超限只回 head+tail sample（防 YAML 炸弹 / XML 实体展开 DoS）。
- 用 defusedxml 安全解析；CSV/TSV 抽样 50 行；JSON 抽 schema（深度限制 2/4）；code 抽 import + class/def 符号。

**`tool_output_budget_middleware.py`** —— `AgentMiddleware` 实现 `wrap_tool_call`：
- `_budget_content` `(DF/agents/middlewares/tool_output_budget_middleware.py:333-...)` —— 工具结果超过 `externalize_min_chars`（默认 30K，可按 tool_name override）：
  - **优先外化到磁盘**：`_externalize_to_sandbox` 或 `_externalize`（host）写到 `/mnt/user-data/outputs/.tool-outputs/<tool>-<id>.log`，返回虚拟路径；模型可见的内容替换成 **typed synopsis + 文件引用**。
  - **磁盘不可用时降级**：head + `... [truncated N chars] ...` + tail（行边界对齐：`_snap_to_line_boundary` `(DF/agents/middlewares/tool_output_budget_middleware.py:75-91)`）。
- 通过 `ToolCallRequest.runtime.state["sandbox"]` 拿 sandbox_id，**绝不**调用 `provider.acquire`（避免每次工具调用触发远端 IO）。

### 2.4 是否有 OpenHands 式「全量历史 vs 实际窗口」分层？

**有，但形态不同**。deer-flow 的「分层」是：

| 层 | 存储 | 进入模型的方式 |
|---|---|---|
| **L1 全量历史** | LangGraph checkpointer 的 `messages` 通道（full 或 delta 模式） | 每次 `before_model` 从 state 直接读 |
| **L2 压缩后历史** | `messages` 通道（被 summarization 用 `REMOVE_ALL_MESSAGES` 替换为 preserved tail） | 同上 |
| **L3 持久摘要** | **独立 channel** `summary_text`（LastValue，非 messages） | `DurableContextMiddleware.abefore_model` 注入为隐藏 `HumanMessage(<durable_context_data>)` `(DF/agents/middlewares/durable_context_middleware.py:69-79, 210)` |
| **L4 任务委派账本** | **独立 channel** `delegations`（自定义 reducer `merge_delegations`） | 同样在 DurableContext 注入 |
| **L5 已加载技能引用** | **独立 channel** `skill_context`（`merge_skill_context`，仅存 name/path/description，**不存 SKILL.md body**） | 同样在 DurableContext 注入 |
| **L6 动态上下文** | 不持久化 | `DynamicContextMiddleware` 在每次 run 注入 `<system-reminder>` 到首条 HumanMessage（当前日期、memory） |
| **L7 工具外化产物** | 沙箱 outputs 目录文件 | synopsis + 文件路径引用回模型，模型按需 `read_file` 取回 |

**没有像 OpenHands 那样「一份 ConversationMemory + 一份 View Window」的显式双份管理**——历史只有一份（在 checkpointer），但被压缩时通过 `REMOVE_ALL_MESSAGES + preserved tail` 原地替换，摘要则剥到独立 channel 永久保留。可理解为「单份历史 + 多个旁路 channel」。

---

## 3. 子 Agent 调度（subagents/executor.py）

**(DF/subagents/executor.py:395)** `class SubagentExecutor`。

### 3.1 隔离机制（"isolated loop"）

**进程级单例持久事件循环**（`(DF/subagents/executor.py:265-349)`）：
- `_get_isolated_subagent_loop()` 懒启动一个专用 daemon 线程跑 `asyncio.new_event_loop()`，全局共享。避免每次子 agent 创建/关闭临时 loop（共享 httpx 等异步客户端绑定到 loop）。
- `_submit_to_isolated_loop_in_context(context, coro_factory)` `(DF/subagents/executor.py:352-362)` —— 用 `copy_context()` 复制父 ContextVar 状态后 `asyncio.run_coroutine_threadsafe`。
- `_shutdown_isolated_subagent_loop()` `(DF/subagents/executor.py:284-315)` `atexit` 注册清理。

**上下文隔离**（`_aexecute` `(DF/subagents/executor.py:723-807)`）：
- 子 agent **不传 checkpointer**（`checkpointer=False`，AGENTS.md 显式声明）——一次性执行、不恢复。
- **不传 checkpoint 坐标**（`thread_id`/`checkpoint_ns`/`checkpoint_id`/`checkpoint_map`）`(DF/subagents/executor.py:768-772)`：注释说明 LangGraph 1.2.6+ 会因显式 thread_id 开新 root lineage，必须让子图继承父 ContextVar 才能保留非根 subgraph 命名空间（防 child AI/tool 帧污染 parent `messages` 流，#4399）。
- 父 `thread_id` 通过 `runtime.context["thread_id"]` 传递给业务消费方（sandbox、middleware、attribution）。
- 子 agent 的 `_build_initial_state(task)` `(DF/subagents/executor.py:635-721)` 用全新 `{"messages": [SystemMessage(skill+prompt), HumanMessage(task)]}`，**不带父对话历史**——只继承 `sandbox_state` 和 `thread_data`。

### 3.2 并行支持

**两个全局 ThreadPoolExecutor** `(DF/subagents/executor.py:259-260)`：
- `_scheduler_pool = ThreadPoolExecutor(max_workers=3)` —— 调度编排层
- `_execution_pool = ThreadPoolExecutor(max_workers=3)` —— 子 agent 真正执行（implicit，通过 `_submit_to_isolated_loop_in_context` 提交到持久 loop）

**并行约束**（`SubagentLimitMiddleware`，AGENTS.md 第 27 项）：
- `MAX_CONCURRENT_SUBAGENTS = 3` `(DF/subagents/executor.py:1118)` 硬编码上限。
- 运行时 `max_concurrent_subagents` 被 clamp 到 1-4，`max_total_subagents` clamp 到 1-50（默认 6）。
- middleware 在 `after_model` 里 **截断** 单次响应里超额的 `task` 工具调用 + 强制 `finish_reason="stop"` + 追加可见说明。

### 3.3 结果回收 / 超时 / 取消

**`SubagentResult`** `(DF/subagents/executor.py:76-158)` —— 线程安全的数据载体：
- `try_set_terminal(status, ...)` `(DF/subagents/executor.py:122-158)` 用 `_state_lock` 保证终态 **恰好转移一次**（timeout/cancel/worker 三方竞态：第一个 terminal 写入赢，后续写返回 False）。
- `cancel_event = threading.Event()` `(DF/subagents/executor.py:108)` —— 协作式取消信号。
- `update_token_usage_records(records)` `(DF/subagents/executor.py:116-120)` —— 实时发布累计 token 快照给前端。
- `stop_reason` 字段（additive，不破坏 v1 contract）`(DF/subagents/executor.py:91-95, 102)` —— 区分 `token_capped` / `turn_capped` / `loop_capped`。

**两种入口**：
- `execute(task, result_holder)` `(DF/subagents/executor.py:1010-1052)` —— 同步：检测当前是否在 event loop，是则 `_execute_in_isolated_loop` `(DF/subagents/executor.py:974-1008)`（在持久 loop 跑+future.result(timeout)），否则 `asyncio.run`。
- `execute_async(task, task_id)` `(DF/subagents/executor.py:1054-1115)` —— 异步：立即返回 task_id，把任务投到 `_scheduler_pool`，每 5s 轮询结果（AGENTS.md：poll 5s → SSE events）。

**超时**：`self.config.timeout_seconds`（默认 1800s）→ `Future.result(timeout=...)` 捕获 `FuturesTimeoutError` `(DF/subagents/executor.py:991-996, 1098-1107)`，set `cancel_event` + `try_set_terminal(TIMED_OUT)`。

**取消**：`request_cancel_background_task(task_id)` `(DF/subagents/executor.py:1121-1136)` 只 `set` cancel_event；`_aexecute` 在 `agent.astream` 迭代边界检查 `(DF/subagents/executor.py:854-861)` —— 注释明确：长工具调用中途无法中断，只能在下个 chunk 边界停。

**三档 guard 上限**（`_consume_guard_stop_reason`，`(DF/subagents/executor.py:896, 924)`）：
- **turn 轴**：`recursion_limit == max_turns`（默认 150），抛 `GraphRecursionError` → 捕获后 surfacing `stop_reason="turn_capped"` + 恢复 partial 结果（`(DF/subagents/executor.py:904-962)`）。
- **token 轴**：`TokenBudgetMiddleware` 硬停 → `consume_stop_reason` 取出 `"token_capped"`。
- **loop 轴**：`LoopDetectionMiddleware` 硬停 → `"loop_capped"`。
- 三者都 **不抛异常**，剥 tool_calls 让 loop 自然完，并通过 additive `stop_reason` 暴露给 lead（不破坏 v1 状态枚举）。

---

## 4. 检查点 / 持久化

### 4.1 与 LangGraph checkpointer 的关系

deer-flow **自己不实现 checkpointer 后端**，复用 LangGraph 官方三档（AGENTS.md "Persistence backend resolution"）：
- `InMemorySaver`、`SqliteSaver` / `AsyncSqliteSaver`、`PostgresSaver` / `AsyncPostgresSaver`。
- harness 层 `create_deerflow_agent(checkpointer: BaseCheckpointSaver | None)` `(DF/agents/factory.py:75, 168)` 把它当参数接住；Gateway 在 `(app/gateway/services.py)` 用 `get_checkpointer(request)` 注入。
- 子 agent 强制 `checkpointer=False`（AGENTS.md Subagent System）。

### 4.2 full / delta 两种 channel 模式（DeerFlow 在 LangGraph 1.2 DeltaChannel 之上的核心扩展）

- **full 模式**（默认）：每条消息全量写 checkpoint，O(N²) 时空。
- **delta 模式**：`DeltaChannel` 只存 sentinel + 增量 writes，O(N) 增长。
- **进程冻结**：`(DF/runtime/checkpoint_mode.py)` `freeze_checkpoint_channel_mode()` 在 graph 编译前冻结模式，同进程二次切换抛 `CheckpointModeReconfigurationError`。
- **兼容门**：`(DF/runtime/checkpoint_mode.py)` `ensure_checkpoint_mode_compatible()` 在每次 state 读写前比对 marker `deerflow_checkpoint_channel_mode`（delta 模式写入），full 进程开 delta 线程抛 `CheckpointModeMismatchError`（HTTP 409）；delta 进程读 full 则透明兼容。
- **单一 choke point**：`(DF/runtime/checkpoint_state.py)` `CheckpointStateAccessor` 包住 `get / update / history`，对外只暴露 materialized state（delta checkpoint 没有 `channel_values.messages`，裸读会拿到 sentinel）。

### 4.3 checkpoint_patches.py —— 给 LangGraph 上游 bug 打补丁

**(DF/checkpoint_patches.py)** 模块级 import 时立即生效（`thread_state.py:18` `import deerflow.checkpoint_patches as _checkpoint_patches # noqa: F401`），两个 patch：

1. **InMemorySaver delta-history patch** `(DF/checkpoint_patches.py:52-100)` —— 上游 `InMemorySaver.get_delta_channel_history` 单遍遍历漏掉 pending writes（full→delta 迁移首步丢失消息）；patch 改为委托 `BaseCheckpointSaver.get_delta_channel_history`（多走 `get_tuple`，dict 存储可接受）。**带版本守卫**：`_PATCH_VALIDATED_LANGGRAPH_VERSION = Version("1.2.9")` `(DF/checkpoint_patches.py:32)`，更新版本时只警告不失效。
2. **BinaryOperatorAggregate Overwrite first-write patch** `(DF/checkpoint_patches.py:103-188)` —— 上游空 channel 首个 `Overwrite` 写入会原样存 wrapper（Union-typed `sandbox/goal/todos/promoted` 无构造默认值），下次消费者 `TypeError` (#4380)；patch 拦截空 channel + 首个 Overwrite 情况，直接 unwrap。**行为探测守卫** `_binop_first_write_stores_overwrite_wrapper()` `(DF/checkpoint_patches.py:124-134)`：上游修了就 stand down。

### 4.4 状态序列化

- LangGraph checkpointer 自己负责 `messages` / 各 channel 的 pickle/JSON 序列化。
- DeerFlow 应用层表（`runs` / `threads_meta` / `feedback` / `run_events` / `channel_*`）用 SQLAlchemy ORM，`(DF/persistence/base.py:29)` `class Base(DeclarativeBase)` + `to_dict()` 反射；这些表与 LangGraph 的 `checkpoints/checkpoint_blobs/checkpoint_writes/checkpoint_migrations` 同库但由 alembic 管理（`migrations/_env_filters.py::include_object` 排除 LangGraph 表）。
- **schema migration** 用 alembic hybrid bootstrap `(DF/persistence/bootstrap.py::bootstrap_schema`，三档：empty → create_all + stamp head；legacy（无 alembic_version）→ create_all baseline + stamp 0001 + upgrade head；versioned → upgrade head）。

---

## 5. Lead Agent 主循环 + 状态 Schema

### 5.1 主循环 = langchain `create_agent` ReAct

deer-flow **不实现自己的 ReAct 循环**。`(DF/agents/lead_agent/agent.py:670, 751)` 两处 `return create_agent(...)` 直接调用 langchain 1.x 的 `create_agent(model, tools, middleware, system_prompt, state_schema, checkpointer, name)`，由它生成 `CompiledStateGraph`。

LangGraph 的 agents 模块内置 `ModelNode → ToolNode` 循环（super-step：ModelNode 决定 tool_calls → ToolNode 并发执行 → 回 ModelNode，直到 AIMessage 无 tool_calls），所有「思考-行动」逻辑由中间件在 `before_model` / `after_model` / `wrap_model_call` / `wrap_tool_call` 钩子里改写。

**入口**（`(DF/agents/lead_agent/agent.py:498-526)`）：
- `make_lead_agent(config: RunnableConfig)` —— langgraph.json 注册入口。
- `_make_lead_agent(config, app_config)` —— 真实工厂：解析 model_name / thinking / plan_mode / subagent / agent_name / custom agent config / authorization / tracing callbacks → `create_chat_model(..., attach_tracing=False)` → `build_middlewares(...)` → `assemble_deferred_tools(...)` → `create_agent(...)`。

### 5.2 状态 Schema

**(DF/agents/thread_state.py:253-265)** `class ThreadState(AgentState)`：

```python
class ThreadState(AgentState):
    sandbox: SandboxStateField                                   # merge_sandbox（不同 id 抛错）
    thread_data: NotRequired[ThreadDataState | None]             # workspace/uploads/outputs 路径
    title: NotRequired[str | None]
    artifacts: Annotated[list[str], merge_artifacts]             # 去重合并
    todos: Annotated[list | None, merge_todos]
    goal: Annotated[GoalState | None, merge_goal]                # 跨普通 update 保留
    uploaded_files: NotRequired[list[dict] | None]
    viewed_images: Annotated[dict[str, ViewedImageData], merge_viewed_images]  # 仅 metadata，不存 base64
    promoted: Annotated[PromotedTools | None, merge_promoted]    # catalog-hash scoped tool 提升
    delegations: Annotated[list[DelegationEntry], merge_delegations]  # append、同 id 最新赢、终态不降级
    skill_context: Annotated[list[SkillEntry], merge_skill_context]   # 路径去重 + LRU
    summary_text: NotRequired[str | None]                        # LastValue，summarization 写入
```

`(DF/agents/thread_state.py:298-352)` `merge_message_writes` —— **delta 模式专用 messages reducer**：线性 fold DeltaChannel writes，保留 `add_messages` 全部语义（dup id、替换位置、RemoveMessage 错误、REMOVE_ALL_MESSAGES、null write 错误、missing-ID 分配顺序）。LangGraph 私有的 `_messages_delta_reducer` 也是线性但缺一些公共语义，不能直接替换。

**(DF/agents/goal_state.py:22-32)** `GoalState`：`objective / status / continuation_count / max_continuations / no_progress_count / last_evaluation`，配合 `(DF/runtime/goal.py)` 的 `evaluate_goal_completion` —— 由独立 LLM 评测线程判断 satisfied，未满足则作为隐藏 HumanMessage 续跑（最多 8 次，2 次 no-progress 熔断）。

**(DF/agents/thread_state.py:21-216)** 其它关键 reducer：
- `merge_sandbox`（同 id 幂等，不同 id 抛 ValueError）
- `merge_goal`（None 新值保留旧）
- `merge_delegations`（append、同 id 最新赢、终态永不降级、cap 最近条数）
- `merge_skill_context`（按 path 去重、保留最近读、cap `_SKILL_CONTEXT_MAX_ENTRIES`、**只存 name/path/description，绝不存 body**）

---

## 6. 对 Hyperion runtime harness 的对标建议

### 6.1 可直接照搬的形状（生产级骨架）

| # | 来源 | 照搬到 Hyperion 的什么 | 备注 |
|---|---|---|---|
| 1 | LangGraph `AgentMiddleware` 钩子签名 | Hyperion **不要自造中间件 ABC**，直接继承 langgraph 1.x 的 `AgentMiddleware[State]`，按需 override `before_model/after_model/wrap_model_call/wrap_tool_call` | 与 deer-flow 一致，零抽象成本 |
| 2 | `create_deerflow_agent` factory 模式（`features: RuntimeFeatures` flag + `extra_middleware` + `@Next/@Prev` 锚点插入 + 末尾不变量保护） | Hyperion runtime factory：声明式 features 组主干 + 用户层 extra middleware 锚点插入 | `_insert_extra` 的冲突检测算法可直接照抄 `(DF/agents/factory.py:357-430)` |
| 3 | `TokenBudgetMiddleware` 的「累加 diff + warn queue + hard stop 剥 tool_calls + additive stop_reason + BoundedDict 防泄漏」 | Hyperion bug-RCA MVP 的 per-run token 预算（R2 必备） | 整文件可几乎原样移植，仅替换 config dataclass |
| 4 | `DeerFlowSummarizationMiddleware` 的「token/message/fraction trigger + REMOVE_ALL_MESSAGES 替换 + 独立 summary_text channel + 多模型 fallback + before_summarization hooks」 | Hyperion 长调研/ RCA 报告场景的上下文压缩（R3+ 必备） | 复杂度高，R2 可先简化为「滑窗 + 不摘要」 |
| 5 | `ToolOutputBudgetMiddleware` 的「阈值外化到文件 + 纯函数 synopsis + 磁盘失败降级 head+tail + 行边界对齐 + 5MB DoS 守门」 | Hyperion **必备**（R2）：delegate 给 omp/opencode 时，omp 的 grep/read 输出必须先经此中间件截断，否则一次 log dump 就爆 context | `tool_output_synopsis.py` 是无依赖纯函数，可整文件搬 |
| 6 | `SubagentExecutor` 的「持久 isolated loop + ContextVar copy + SubagentResult.try_set_terminal 锁 + cancel_event + Future.result(timeout) + additive stop_reason + 不传 checkpointer/thread_id 给子图」 | Hyperion 把重活委托给 omp/opencode 的执行器（R2 ★MVP 核心） | 直接对齐 |
| 7 | `build_lead_runtime_middlewares` 严格顺序 + 「InputSanitization → ToolOutputBudget → ToolErrorHandling」三件套作为共享基座（lead 和 subagent 共用） | Hyperion 同样应该有「调度 agent」和「委托 agent」共享一组防护中间件 | docstring 顺序表 `(DF/agents/factory.py:188-211)` 直接照抄 |
| 8 | `checkpoint_patches.py` 的「上游 langgraph bug 探测 + 版本守卫 + 行为 probe + 自动 stand down」模式 | Hyperion 如果用 delta checkpoint，需要类似的 patch 机制 | 暂缓到 R5 |

### 6.2 需要改造（不能照搬）

| 来源 | 为什么不能直接用 | Hyperion 怎么改 |
|---|---|---|
| Lead-agent 的 14-34 个中间件 | 大量是 DeerFlow 业务专有（SkillActivation、McpRouting、Authorization、Guardrail、Sandbox、Uploads、Title、Clarification、ViewImage、Todo...） | Hyperion R2 只挑：InputSanitization、ToolOutputBudget、ToolErrorHandling、Summarization（简版）、TokenBudget、SubagentLimit、LoopDetection；R3+ 再补 Memory / Skill |
| `viewed_images` / `uploaded_files` / `artifacts` channel | Hyperion P2 是 bug-RCA + 调研，不渲染 artifact 卡片 | 删掉 |
| Gateway REST + IM channels 持久化层（runs/threads_meta/feedback/run_events） | Hyperion 是 CLI/SDK 优先，不接 Feishu/Slack | 只保留 SQLite 检查点 + 自有记忆表 |
| Goal continuation（LLM evaluator + 8 次续跑） | bug-RCA 单次就出结果，不需要 LLM judge 续跑 | R2 完全省略 |
| dynamic_context_reminder ID-swap 三联 / SkillToolPolicy / DurableContext 的复杂规则 | DeerFlow-specific（slash 命令、skill 文件） | R3 调研场景可考虑简化的 durable_summary channel |

### 6.3 R2 末「最小骨架」推荐（5 件）

按重要性排序（必asta → nice-to-have）：

1. **【中间件框架】** —— 直接采用 `langchain.agents.middleware.AgentMiddleware` + `create_agent`，自建 `create_hyperion_agent(features, extra_middleware, checkpointer, state_schema)` factory，照抄 `_assemble_from_features` + `_insert_extra`。
2. **【TokenBudgetMiddleware】** —— 几乎原样移植（替换 config 来源为 Hyperion 的 `config.yaml`）。bug-RCA 一跑可能上万 token，必备硬停。
3. **【ToolOutputBudgetMiddleware + tool_output_synopsis】** —— 整文件搬，无依赖。**这是委托 omp 的前置条件**：omp 在沙箱里 grep 出来的东西，召回 Hyperion 时必须先过预算截断。
4. **【SubagentExecutor 简化版】** —— 持久 isolated loop + SubagentResult + cancel_event + timeout + additive stop_reason；删除 skill/memory/authorization/guardrail/tracing 装配，保留核心调度。这是 R2 「委托 omp」的执行器。
5. **【state schema: HyperionState(AgentState)】** —— `messages` + `summary_text`（LastValue）+ `delegations`（merge_delegations 简化版，记录每次 omp 委托结果）+ `sandbox`（若用沙箱）。其它 channel 全砍。

R2 **不要做**：summarization（先用滑窗保留尾部 N 条）、loop_detection（omp 自己有）、title、memory middleware、skill、guardrail、checkpoint delta 模式。

### 6.4 R3 深度调研要补的

- **SummarizationMiddleware** —— 调研场景对话长，必须补 LLM 摘要 + `summary_text` 独立 channel + `DurableContextMiddleware` 把摘要注入隐藏 HumanMessage（完整三件：summary + delegations + skill_context 类比）。
- **LoopDetectionMiddleware** —— 调研 agent 容易在搜索/抓取循环里空转。
- **DynamicContextMiddleware** —— 注入当前日期 + Hyperion 记忆召回结果作为 `<system-reminder>`，保持 system prompt 静态以利 prefix cache。
- **CheckpointChannel delta 模式评估** —— 调研场景消息量大，可参考 deer-flow 的 `bench_production.py` 决策树。
- **Aider repomap 风格的代码地图 channel** —— 类比 `skill_context`（只存引用不存 body）。

### 6.5 R5 生产化补齐

- **checkpoint_patches.py 模式** —— 对 langgraph 上游 bug 的探测 + 版本守卫 + 行为 probe + stand down。
- **`ConversationMemory` vs `View` 双层** —— 如果 R5 引入长任务（PR 跟踪、持续学习），可考虑真正的双层：一份全量历史（不喂模型）+ 一份窗口视图（喂模型），比 deer-flow 当前的单份历史 + 旁路 channel 更彻底（OpenHands 路线）。
- **跨进程 sandbox ownership / run lease / orphan reconciliation** —— deer-flow `(community/aio_sandbox/ownership/)` 是生产级多实例部署的必须，R5 多 worker 时照搬。
- **配置 hot-reload boundary / restart-required 字段注册表** —— `(DF/config/reload_boundary.py::STARTUP_ONLY_FIELDS)` 模式。
- **alembic hybrid bootstrap** —— `(DF/persistence/bootstrap.py)` 三档决策（empty/legacy/versioned），Hyperion 多人共享记忆库时必备。

---

## 附录：关键文件路径速查

```
DF/agents/factory.py                                # SDK factory（create_deerflow_agent）
DF/agents/lead_agent/agent.py                       # Lead-agent 应用 factory（make_lead_agent）
DF/agents/lead_agent/prompt.py                      # Lead-agent system prompt
DF/agents/thread_state.py                           # ThreadState schema + 所有 reducer
DF/agents/goal_state.py                             # GoalState schema
DF/agents/middlewares/                              # 38+ 个中间件
  ├─ summarization_middleware.py                    # DeerFlowSummarizationMiddleware
  ├─ token_budget_middleware.py                     # TokenBudgetMiddleware
  ├─ tool_output_budget_middleware.py               # 工具输出预算外化
  ├─ tool_output_synopsis.py                        # 纯函数 synopsis
  ├─ durable_context_middleware.py                  # 投影 summary/delegations/skills
  ├─ dynamic_context_middleware.py                  # 注入日期/memory
  ├─ tool_error_handling_middleware.py              # build_lead_runtime_middlewares 共享基座
  └─ ...
DF/subagents/executor.py                            # SubagentExecutor + isolated loop
DF/subagents/step_events.py                         # 步骤事件捕获
DF/subagents/status_contract.py                     # 跨语言契约
DF/checkpoint_patches.py                            # LangGraph 上游 bug 补丁
DF/runtime/checkpoint_mode.py                       # full/delta 模式冻结 + 兼容门
DF/runtime/checkpoint_state.py                      # CheckpointStateAccessor choke point
DF/runtime/context_compaction.py                    # 手动 /compact 路径
DF/runtime/goal.py                                  # goal continuation evaluator
DF/persistence/base.py                              # SQLAlchemy Base
DF/persistence/bootstrap.py                         # alembic hybrid bootstrap
DF/persistence/engine.py                            # 引擎初始化
DF/persistence/migrations/                          # alembic versions
```
