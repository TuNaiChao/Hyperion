# 运行时上下文管理 harness — 设计文档

> 状态:**R3.0 骨架 ✅ 已落地**(2026-07-30,5 件 + 冒烟绿)· R3.2 深度调研上场 → R5 生产化补齐
>
> 📌 **2026-07-30 审核修订(F6/F7/F11)**:① 原「R2 末搭骨架」**整体挪到 R3 开场**(R2 九步不依赖 runtime、不验,R3 深度调研边搭边验)——文中残余「R2 末」字样均按「R3 开场」理解。② **factory 瘦身**:R3 中间件仅 ~5 个,用**普通有序 list**,不移植 deer-flow `_insert_extra`/`@Next/@Prev` 锚点机制(那是给 30+ 中间件排序的,R5 再上)。③ langchain **1.3.14 / langgraph 1.2.9**(`AgentMiddleware` 已实测可用);langgraph 1.2.9 的 delta-checkpoint 有上游 bug(deer-flow `checkpoint_patches.py` 正补它),**R3 用默认 full 模式避开**,delta 模式 + patches 推 R5。
> 上位文档:[architecture.md](architecture.md) · 对标调研:[deer-flow-runtime-参考.md](../调研/deer-flow-runtime-参考.md)
> 决策来源(用户 2026-07-29):「Hyperion 要有 deer-flow 同等的运行时上下文管理,自己能跑长 agent;但 coding 能力仍委托 opencode/omp」

---

## 0. 这是什么 / 为什么要自建(面向小白)

**类比:Hyperion 有两种活儿要干。**

1. **改代码的力气活**(读文件、跑命令、写补丁)→ 这类**委托**给成熟 coding agent(opencode/omp),它们天生会干,Hyperion 不重造。见 [bug-rca-design.md §3](bug-rca-design.md)。
2. **自己跑的长脑力活**(深度调研:读几千个文件、多轮检索、反复推理、写架构文档)→ 这类**没人可委托**(它不是改代码,是 Hyperion 自己的"思考")。一个长 agent 跑下来,对话历史会**爆炸**(读的代码、检索结果全堆在上下文里,几十万 token 轻轻松松),模型要么报错要么烧光预算。

**运行时上下文管理 harness 解决的就是第 2 类**:让 Hyperion 自己的 lead agent 能**跑很久不爆上下文**——

- **压历史**:对话太长时,把旧消息**摘要**成一小段(summary),只留最近的 + 摘要,上下文瞬间瘦身(对标 deer-flow `SummarizationMiddleware`、OpenHands `Condenser`)。
- **管预算**:设 token 上限,超了**优雅停**(不是崩溃),产出当前最佳结果(对标 `TokenBudgetMiddleware`)。
- **截工具输出**:一次 `grep` 返回 10 万行不能全塞进上下文,**外化到文件 + 只回一个确定性 synopsis**(json/csv/code 摘要,**非 LLM 摘要**;OpenHands 实证:LLM 摘要化反 +24~94% token、mask 省 8.6% 无损 —— 故工具输出优先 mask/确定性 synopsis,LLM 摘要只用于对话历史压缩,不用于工具输出)(对标 `ToolOutputBudgetMiddleware`)。
- **派子任务**:调研可以**并行**派几个子 agent 各查一个模块,互不污染上下文(对标 `SubagentExecutor`)。
- **断点续跑**:长任务挂了能从检查点恢复(对标 LangGraph checkpointer)。

**一句话:这套 harness = Hyperion 自己跑长 agent 的"变速箱 + 油量计 + 散热器"。没有它,深度调研(R3)根本跑不起来。**

---

## 1. 边界:runtime 自建 vs coding 委托(关键,别搅混)

| 能力 | 归属 | 对标 |
|---|---|---|
| **coding 动作**(读写文件 / 跑命令 / 写补丁 / 改代码) | **委托** opencode/omp | `CodingAgentDelegate`(R2) |
| **agent 运行时**(跑长 agent / 压上下文 / token 预算 / 并行子任务 / 断点续跑) | **Hyperion 自建** ★本文档 | deer-flow harness + OpenHands 3 层 |

**两者协作**:Hyperion 的 lead agent(自建 runtime 跑着)推理到"这步要改代码"→ 经 `CodingAgentDelegate` 委托 opencode → opencode 干完返回结构化结果 → lead agent 接着跑(它的上下文由 runtime 管,不会因为 opencode 返回一大坨而爆)。

> **v0→v2 的认知演进**:最初(v0.1)想"先建深地基建场景"过度设计;v2 重规划时一度认为"重活全外包、runtime 也不做"。**2026-07-29 用户决策修正**:coding 外包,但 **runtime 必须自建**——因为深度调研是 Hyperion 自己的长 agent,没法外包。本文档落地这条修正。

---

## 2. 对标:deer-flow + OpenHands(已核实,文件:行号)

完整对标见 [deer-flow-runtime-参考.md](../调研/deer-flow-runtime-参考.md)。核心三条:

### 2.1 中间件机制 —— 不自造 ABC,直接用 LangGraph 原生

deer-flow **不自造中间件基类**,直接继承 `langchain.agents.middleware.AgentMiddleware[State]`,按需 override 钩子(`deer-flow/.../agents/middlewares/token_budget_middleware.py:39,62`):

| 钩子 | 触发时机 | 干啥 |
|---|---|---|
| `before_model` / `abefore_model` | 每次调 LLM 前 | **改写 messages、压历史、注入提示**(上下文管理主战场) |
| `after_model` / `aafter_model` | 每次 LLM 返回后 | 查 usage、改 AIMessage、剥 tool_calls |
| `wrap_model_call` | 包住整个模型调用 | 改写 request.messages、注入隐藏 HumanMessage |
| `wrap_tool_call` | 包住单次工具调用 | 截断/外化工具输出 |

**Hyperion 照此:不自造 ABC,直接用 langgraph `AgentMiddleware`。** 零抽象成本,与 deer-flow 一致。

### 2.2 上下文管理 —— 单份历史 + 多个旁路 channel

deer-flow 不是 OpenHands 那种"全量历史 + 窗口视图"双份管理,而是「**单份历史(在 checkpointer)+ 多个旁路 channel**」:

| 层 | 存哪 | 怎么进模型 |
|---|---|---|
| 历史 messages | LangGraph checkpointer 的 `messages` 通道 | `before_model` 直接读 |
| 压缩后历史 | 同上(被 summarization 用 `REMOVE_ALL_MESSAGES` 原地换成"摘要+尾部") | 同上 |
| 持久摘要 `summary_text` | **独立 channel**(LastValue) | `DurableContextMiddleware` 注入为隐藏 HumanMessage |
| 委派账本 `delegations` | **独立 channel**(自定义 reducer) | 同上 |

**Hyperion 取舍**:R2/R3 先学 deer-flow 的"单份历史 + summary_text 旁路 channel"(够用、简单);R5 生产化时若需更长任务,再评估 OpenHands 式双份(全量 ConversationMemory + View 窗口)。

### 2.3 子 agent 调度 —— 持久 isolated loop + 锁

`SubagentExecutor`(`subagents/executor.py:395`)核心:
- **持久 isolated event loop**(daemon 线程共享,避免每子 agent 建/关临时 loop)。
- **上下文隔离**:子 agent 不传 checkpointer、不带父对话历史,全新 `messages`。
- **并发上限**:`MAX_CONCURRENT_SUBAGENTS = 3`。
- **`SubagentResult.try_set_terminal` 锁**:timeout/cancel/worker 三方竞态下"终态恰好转移一次"。
- **additive `stop_reason`**:`token_capped`/`turn_capped`/`loop_capped`(不破坏 v1 状态枚举)。

**Hyperion 取舍**:这套是"Hyperion 内部并行派多个子 agent"(R3 调研并行查多模块)用;**bug-RCA 的委托是 verify-refine 双循环(delegate_localize/repair_loop),由 `CodingAgentDelegate.run()` 驱动、不并派子 agent,故不需要 SubagentExecutor**。

---

## 3. 架构:`platform/runtime/` 目录结构

```
src/hyperion/platform/runtime/        # ✅ R3.0 已落地(对标 deer-flow harness 包)
├── factory.py            # create_hyperion_agent(model, tools, *, middleware=None, state_schema, checkpointer, name)
│                         #   middleware=None → build_default_middlewares() 默认链;普通有序 list(R5 再上 @Next/@Prev)
├── state.py              # HyperionState(AgentState):messages + summary_text + delegations(+ merge_delegations reducer)
├── context/
│   └── tool_output_synopsis.py  # ✅ 整文件搬 deer-flow 纯函数(json/csv/code synopsis + 5MB 守门 + defusedxml)
├── middlewares/
│   ├── token_budget.py   # ✅ TokenBudgetMiddleware(三档阈值 + warn/hard_stop + BoundedDict)
│   └── tool_output.py    # ✅ ToolOutputBudgetMiddleware(超阈值外化磁盘 + synopsis)
└── checkpoint.py         # ✅ SqliteSaver 工厂 + get_checkpointer() 单例 + 断点续跑
# 🆕 R3.2 待加:middlewares/{summarization,loop_detection,dynamic_context,memory}.py + subagents.py
```

> 与现有 [platform/](../../src/hyperion/platform/)(models/config/reflection/sandbox/tracing)**并列**——platform/ 是"平台层"(模型/配置/沙箱/工具),runtime/ 是"agent 运行时层"(跑长 agent 的变速箱)。runtime/ 依赖 platform/(用它的 model factory / config / sandbox)。

---

## 4. 核心机制逐个(实现时照此对标)

### 4.1 中间件框架 + factory(✅ R3.0 已落)
- 继承 `langchain.agents.middleware.AgentMiddleware[HyperionState]`,override `before_model` 等。
- `create_hyperion_agent(model, tools, *, middleware=None, state_schema=HyperionState, checkpointer=None, name=...)`:`middleware=None` → `build_default_middlewares()` 返回默认链(ToolOutputBudget + TokenBudget)。**普通有序 list** 装配(F6 瘦身:不移植 deer-flow `_insert_extra`/`@Next/@Prev`——那是给 30+ 中间件排序的,**推 R5**)。
- 最终调 langchain `create_agent(model, tools, middleware, ...)`——不自造 ReAct 循环。详见 `platform/runtime/factory.py`。

### 4.2 TokenBudget(✅ R3.0)— 几乎原样移植
- `before_agent` 记已有 AIMessage 的 usage(不计本 run),`after_model` 累加 diff。
- 三档阈值:`max_tokens` / `max_input_tokens` / `max_output_tokens`。
- **warn**(默认 0.7)→ 注入 `HumanMessage(name="budget_warning")`;**hard_stop**(1.0)→ 剥 tool_calls + `finish_reason=stop` + 追加说明(**不抛异常**,loop 自然终止)。
- `BoundedDict(1000)` 防 abandoned run 泄漏;`additive stop_reason`。
- 文件:行号见调研报告 §2.2。

### 4.3 ToolOutputBudget + synopsis(✅ R3.0,委托前置)— 整文件搬
- `tool_output_synopsis.py` 是**无依赖纯函数**(按 json/csv/code/text 产出 synopsis),5MB DoS 守门,defusedxml 安全解析——**可整文件搬**。
- `ToolOutputBudgetMiddleware.wrap_tool_call`:工具结果 > `externalize_min_chars`(默认 30K)→ 外化到 `data/runtime/tool-outputs/`(R3.0 简化:**无 sandbox 虚拟路径映射,直接写本地**;留 `_resolve_outputs_dir` 钩子备 R3.2 沙箱切 `<workspace>/outputs`);磁盘不可用降级 head+tail(行边界对齐)。
- **意义**:opencode/grep 返回的大输出召回 Hyperion 时必须先过它,否则一次 dump 爆 context。

### 4.4 Summarization(R3)— LLM 摘要 + 独立 channel
- trigger 三档:`(tokens, N)` / `(messages, N)` / `(fraction, f)`。
- `_partition_messages` 拆"待摘要 / 保留尾部"→ LLM 摘要 → `REMOVE_ALL_MESSAGES + preserved` 原地替换 messages → summary 写独立 channel `summary_text`。
- **多模型 fallback**:[配置 model, run model, 默认 model, None],任一失败降级,最坏跳过本轮(不抛)。
- `DurableContextMiddleware` 把 summary_text 投影成隐藏 HumanMessage 注入。

### 4.5 SubagentExecutor 简化版(R3)
- 持久 isolated loop + `copy_context()` + `SubagentResult.try_set_terminal` 锁 + `cancel_event` + `Future.result(timeout)` + additive stop_reason。
- 删 deer-flow 的 skill/memory/authorization/guardrail/tracing 装配,留核心调度。
- **R3 深度调研用**:并行派子 agent 各查一个模块。

### 4.6 checkpointer(✅ R3.0)
- 复用 LangGraph 官方 `SqliteSaver`(本地文件,零依赖),不自造后端。
- `create_hyperion_agent(checkpointer=SqliteSaver(...))`,长任务可断点续跑。
- full/delta channel 模式、checkpoint_patches(上游 bug 补丁)→ **R5 生产化再评估**。**R3 用默认 full 模式**(F11:langgraph 1.2.9 delta 模式有上游 bug —— InMemorySaver delta-history 丢 pending writes + Overwrite first-write 存 wrapper,deer-flow `checkpoint_patches.py` 正补;本机正是 1.2.9,full 模式不踩坑)。
- ⚠️ **R3.1 bug_rca graph 暂不挂 checkpointer**(线性 DAG,`run()` 一次性 `ainvoke`);`get_checkpointer()` 单例已就绪,**R3.2 deep_research 长任务才真上**。

### 4.7 HyperionState(✅ R3.0)
```python
class HyperionState(AgentState):
    summary_text: NotRequired[str | None]              # LastValue,summarization 写入
    delegations: Annotated[list[DelegationEntry], merge_delegations]  # 每次 omp 委托结果(append/同 id 最新赢)
    # R3+ 再加:sandbox(若用)、memory_context(召回注入)等
```
删掉 deer-flow 的 viewed_images/artifacts/uploaded_files(Hyperion 不渲染 artifact 卡片)。

---

## 5. 分档实现路线(对标调研报告 §6.3-6.5)

### R3.0:最小骨架(5 件)✅ 已落地
> ⚠️ **bug-RCA 五步 graph 本身不依赖 runtime**(线性 DAG,不长)。R3.0 搭骨架是给 **R3.2 深度调研**铺路。冒烟测试 `tests/runtime/test_smoke.py` 已验中间件链 + token 预算 + checkpointer 生效。

1. ✅ **中间件框架**:`AgentMiddleware` 接入 + `create_hyperion_agent` factory。
2. ✅ **TokenBudgetMiddleware**:移植(三档阈值 + warn/hard_stop)。
3. ✅ **ToolOutputBudget + synopsis**:整文件搬 `tool_output_synopsis.py`。
4. ✅ **HyperionState** schema + `SqliteSaver` checkpointer 工厂(`get_checkpointer()` 单例)。
5. ✅ 冒烟测试。

**R3.0 不做**(留 R3.2):summarization、loop_detection、memory middleware、skill、guardrail、delta checkpoint。

### R3 深度调研:runtime 正式上场
- **SummarizationMiddleware**(调研对话长,必备 LLM 摘要 + summary_text channel + DurableContext 注入)。
- **LoopDetectionMiddleware**(调研易在搜索循环空转)。
- **DynamicContextMiddleware**(注入当前日期 + Hyperion 记忆召回作 `<system-reminder>`,保 system prompt 静态利 prefix cache)。
- **SubagentExecutor**(并行查多模块)。
- **delta checkpoint 评估**(调研消息量大)。
- Aider repomap 风格的"代码地图 channel"(类比 skill_context,只存引用不存 body)。

### R5 生产化:补齐
- checkpoint_patches 模式(上游 langgraph bug 探测 + 版本守卫 + stand down)。
- OpenHands 式双层记忆(全量 ConversationMemory + View 窗口,若引入更长任务)。
- 跨进程 sandbox ownership / run lease / orphan reconciliation(多 worker 部署)。
- 配置 hot-reload boundary / alembic hybrid bootstrap(多人共享记忆库)。

---

## 6. 待办(记 backlog)

> 同步进 `.claude/memory/backlog-production-grade.md`。

- [x] ✅ **R3.0**:`platform/runtime/` 骨架(factory + state + token_budget + tool_output + checkpointer)已落地。
- [ ] **R3.2**:summarization + loop_detection + dynamic_context + SubagentExecutor + memory middleware。
- [ ] **R5**:checkpoint_patches + 双层记忆 + sandbox ownership + alembic bootstrap。
- [ ] 中间件顺序表照抄 deer-flow `factory.py:188-211`(顺序敏感,langgraph 反向 dispatch after_model)。
- [ ] `tool_output_synopsis.py` 整文件搬(无依赖纯函数,优先)。
- [ ] 评测:R3 深度调研跑一个长任务(wpa 全仓),验证 condenser 能把上下文压住 + 不丢关键信息。
