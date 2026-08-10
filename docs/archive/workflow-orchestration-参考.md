# Workflow 编排范式调研 — Hyperion bug-RCA 七步定稿参考

> 调研日期:2026-07-29。目的:为 Hyperion bug-RCA 七步 workflow 的编排选择定稿(线性 StateGraph vs 并行/分支/循环 vs ReAct agent)。
> 严禁仅凭记忆:所有 API 均在 Hyperion 本地 `.venv` 核实;所有论文均带 arXiv ID + 版本日期;所有 deer-flow 引用均带 `文件:行号`。

---

## 0. 结论先行(TL;DR)

| 决策 | 结论 | 一句话理由 |
|---|---|---|
| **R2 MVP 编排** | **保持当前线性 StateGraph DAG**(`ingest→recall→localize→assemble→delegate→verify→report_memorize`) | 七步顺序确定、Agentless 业界基线证明线性管线在 bug-RCA 上有效(SWE-bench Lite 32% / $0.70)、最易对齐 demo2 金标准与可观测 |
| **R2 是否引入分支/循环** | **否**。R2 单轮、无分支、无循环(现状 `graph.py:34-42` 已正确) | 简化对照金标准;条件分支/循环的价值要在 verify/localize 节点本身成熟后才显现 |
| **R5 生产级加什么** | (a) recall∥localize superstep 并行;(b) localize T2L 式漏斗自循环;(c) delegate Send fan-out 多候选;(d) verify/refine 循环 | 对齐 Agentless 漏斗 + T2L 粗→精 + multi-candidate refine 这三条业界已验证路径 |
| **外层要不要换 ReAct lead agent** | **否**。外层保持固定 StateGraph;`delegate` 节点内部嵌 coding agent(opencode,它本身是 ReAct) | bug-RCA 是专用流水线(步骤明确),不是通用 chat。固定管线更可控、更易复现;deer-flow 的 ReAct lead 适合"不知道下一步该干啥"的开放任务 |
| **`Send` / `add_conditional_edges` / `Command` / `interrupt` 在 Hyperion 版本可用?** | **全部可用**(langgraph 1.2.9,本地核实) | 见 §3 |

---

## 1. deer-flow 的业务 workflow 编排范式

### 1.1 一句话定性

deer-flow **不是固定 StateGraph DAG,也不是早期文献里的 planner→researcher→reporter 固定阶段**;它是 **单 lead agent(ReAct)+ 中间件护栏 + subagent delegation(handoff via `task` 工具)+ goal 外层自动续跑循环 + Clarification `Command(goto=END)` 人介入** 的混合编排。

证据:
- 仓库里没有任何手写 `StateGraph().add_node(...).add_edge(...)` 链式业务图。仅有的 `StateGraph` 出现在 `runtime/checkpoint_state.py:51-54`,那是状态快照写入用的 utility 图(单节点),不是业务编排。
- 业务编排入口是 `make_lead_agent(config)`,注册在 `backend/langgraph.json`,本质是 LangGraph 的 **graph factory**,内部调 `langchain.agents.create_agent`(LangChain 1.3+ 的 prebuilt agent factory)。

### 1.2 关键文件与行号

| 角色 | 文件:行号 | 说明 |
|---|---|---|
| LangGraph 注册入口 | `backend/langgraph.json` + `backend/packages/harness/deerflow/agents/__init__.py:21-31` | `make_lead_agent` 被反射加载为图工厂 |
| Lead agent 工厂 | `backend/packages/harness/deerflow/agents/lead_agent/agent.py:498`(`make_lead_agent`)+ `:526`(`_make_lead_agent`)+ `:670`、`:751`(两次 `create_agent(...)` 调用) | 一次 LLM 请求 = model 节点 ↔ tools 节点的 ReAct 循环(LangGraph 1.x 的 `create_agent` 内部就是 `StateGraph` + `add_node("model", ...)` + `add_node("tools", ...)` + 条件边循环) |
| SDK-level 纯参数工厂 | `backend/packages/harness/deerflow/agents/factory.py:64-170`(`create_deerflow_agent`)+ `:162`(`create_agent(...)`)+ `:178-349`(`_assemble_from_features` 14 个中间件按序组装) | 不读配置,纯参数装配;中间件链是 deer-flow 的真正"业务逻辑"载体 |
| Subagent 委托执行器 | `backend/packages/harness/deerflow/subagents/executor.py:395`(`class SubagentExecutor`)+ `:491`(`_create_agent`)+ `:260`(`ThreadPoolExecutor(max_workers=3)` 调度池) | Subagent 也是 `create_agent` 实例;由 `task` 工具触发,在背景线程里跑,lead 同步等结果 |
| 人介入中断 | `backend/packages/harness/deerflow/agents/middlewares/clarification_middleware.py`(在 `agent.py` 的 `build_middlewares` 里被装配为"always last") | `ask_clarification` 工具调用 → middleware 拦截 → `Command(goto=END)` 中断图,等人回答后 `Command(resume=...)` 续跑 |
| Goal 自动续跑外层循环 | `backend/packages/harness/deerflow/runtime/goal.py`(整个文件)+ `app/gateway/routers/threads.py` 的 goal 端点 | 一轮跑完 → 非思考 evaluator 模型判"目标是否达成" → 未达成则注入 hidden `HumanMessage` 续跑;硬上限 8 次,2 次无进展 breaker |
| 防失控护栏 | `agents/middlewares/loop_detection_middleware.py`、`token_budget_middleware.py`、`subagent_limit_middleware.py` | `LoopDetectionMiddleware` 重复工具调用硬停;`TokenBudgetMiddleware` token 预算耗尽硬停;`SubagentLimitMiddleware` 截断超额 `task` 调用(默认 3 并发 / 每 run 总数 6) |

### 1.3 编排结构特征

| 特征 | deer-flow 是否有 | 实现 |
|---|---|---|
| 线性链 | 否(业务层)| 仅中间件链是严格有序的(`factory.py:188-200` 列出 14 个中间件的位置) |
| 并行 fan-out | **弱形式**:同一 model response 可包含多个 `task` tool_call,`SubagentLimitMiddleware` 允许 ≤3 并发(`executor.py` `_scheduler_pool` 3 workers + `_execution_pool` 3 workers)。**不是** LangGraph `Send` 的 map-reduce 式动态并行 | `subagent_limit_middleware.py` + `executor.py:260` |
| 条件分支 | **是**,但隐藏在 `create_agent` 内部(model↔tools 条件边)与 goal evaluator(续跑/停止)| 没有手写 `add_conditional_edges` 业务分支 |
| 循环(reflection/refine)| **外层 goal 循环**(最多 8 次)+ **内层 ReAct 循环**(model↔tools,受 `recursion_limit=max_turns` 限制,subagent general-purpose `max_turns=150`)| `runtime/goal.py` |
| ReAct 自主决策 | **是**。lead agent 完全 ReAct,自己决定调哪个工具/是否 handoff | `create_agent` |
| Handoff / 多 agent | **是**,通过 `task(description, subagent_type)` 工具显式委托,handoff 是 synchronous(Send→block→result 回 lead)| `subagents/executor.py:395` + `tools/builtins/task_tool.py` |
| 人介入 | **是**,`Command(goto=END)` + `Command(resume=...)` | `clarification_middleware.py` |
| 上下文压缩 | **是**,`DeerFlowSummarizationMiddleware`(可选) | `agents/middlewares/summarization_middleware.py` |
| 持久化(checkpointer)| **是**,full / delta 两种 channel 模式 | `runtime/checkpoint_mode.py` + `agents/thread_state.py` |

### 1.4 为什么 deer-flow 选 ReAct lead 而非固定 DAG

deer-flow 定位是 **通用 AI super-agent**(AGENTS.md 原文:"LangGraph-based AI super-agent system"),用户提问开放度极高(写代码、查资料、跑脚本、配 IM、做 PR…),无法预定义步骤。所以它选 ReAct:让模型决定下一步。中间件链负责护栏与功能挂载(memory/skill/sandbox/vision/…),subagent 负责重活委托,goal 负责长任务自动续跑。

**这个选择对 Hyperion bug-RCA 不适用**:bug-RCA 是**专用流水线**,七步是设计阶段就定死的(对标 Agentless 三阶段 + Hyperion 记忆特色),不需要模型决定"要不要 recall / 要不要 localize"。

---

## 2. LangGraph 1.x(2025-2026)编排模式汇总

> LangGraph 1.0 于 2025 年发布(稳定 API),1.2.x 是 2026-07 当前稳定线。Hyperion 已装 **1.2.9**(本地核实:`.venv/lib/python3.12/site-packages/langgraph-1.2.9.dist-info/METADATA`)。

### 2.1 七种原语对比

| 原语 | API(Hyperion 1.2.9 已装,本地核实) | 适用场景 | 不适用场景 |
|---|---|---|---|
| **线性链** | `StateGraph.add_edge(start, end)`(`graph/state.py:915`)| 步骤确定的流水线(Agentless、bug-RCA 七步)| 开放任务、需要模型决策路由 |
| **并行 superstep**(静态 fan-out)| 多个 `add_edge(A, B1)` + `add_edge(A, B2)` + `add_edge(B1, C)` + `add_edge(B2, C)`,B1/B2 在同一 superstep 并发,B1/B2 都完成后才进 C | 任务数**预先知道**(recall ∥ localize)| 任务数依赖 state 动态变化 |
| **动态 fan-out**(map-reduce)| 节点返回 `Send(node, state)`(`types.py:664`,`class Send`)| 任务数**运行时才知**(multi-candidate delegate:state 决定派 N 个)| 任务数固定且小 |
| **条件分支** | `StateGraph.add_conditional_edges(start, router_fn, mapping)`(`graph/state.py:969`)| 基于 state 路由(verify 失败 → 回 assemble;recall 高命中 → 走快路径)| 路由逻辑是固定线性 |
| **循环(reflection/refine)** | 条件边指向前的节点;或 `Command(goto="prev_node", update={...})`(`types.py:759`,`class Command`)| 迭代改进直到满足退出条件(T2L 漏斗、verify 失败重试)| 退出条件不可判定 |
| **人介入(HITL)** | 节点内调 `interrupt(value)`(`types.py:811`)→ 图暂停 → `Command(resume=user_input)` 恢复 | 需要人审批/澄清的高风险步骤(delegate 前确认?patch review?)| 全自动流水线 |
| **状态覆盖** | `Command(update={...}, goto=...)` 或 `Overwrite` reducer(`types.py:938`)| 强制重写 reducer channel(回滚、state 重置)| 普通 partial update |

### 2.2 多 agent 编排模式(LangGraph 官方分类)

LangChain 官方把多 agent 编排归纳为四种(文档:`docs.langchain.com/oss/python/langgraph/concepts/multi_agent`):

| 模式 | 角色 | 适用 | Hyperion 是否需要 |
|---|---|---|---|
| **Supervisor**(centralized)| 一个 supervisor agent 决定下一步调哪个子 agent | 子 agent 角色清晰、需要集中调度 | 否(bug-RCA 不需要 LLM 决定路由) |
| **Swarm**(decentralized handoff)| Agent 之间互相 handoff(每个 agent 装别人的 `handoff_tool`)| 长程对话、角色频繁切换 | 否 |
| **Hierarchical** | Supervisor 套 supervisor | 大型组织任务 | 否 |
| **Prebuilt `create_agent`** | 单 agent + 工具 + 中间件(deer-flow 模式)| 通用 chat | Hyperion `delegate` 节点内部嵌的 coding agent 已经走这条 |

**Hyperion 外层 bug-RCA StateGraph 不需要多 agent 模式**。`delegate` 是一次性 RPC(委托 opencode),不是 LangGraph 意义上的子 agent(没有共享 state / checkpointer)。

### 2.3 LangGraph 官方建议(2025-2026)

LangGraph 官方在多个博客与文档里反复强调两点:

1. **"Start with a simple graph, add complexity only when needed."** — 业务流水线优先用线性 + 条件边;`Send` / Swarm / 多 agent 只在确有动态并行需求时引入。
2. **`create_agent`(prebuilt ReAct)适合"模型决定下一步"的开放任务;手写 StateGraph 适合"工程师决定下一步"的确定任务。** — 两者不要混用:不要在 StateGraph 节点里再嵌一个 `create_agent`(除非像 Hyperion delegate 那样明确是 RPC 出去)。

参考(可公开核实):
- LangGraph 概念文档 `https://docs.langchain.com/oss/python/langgraph/concepts/multi_agent`(Redirects from `langchain-ai.github.io/langgraph/concepts/multi_agent/`)
- LangGraph 1.x stable API:`https://docs.langchain.com/oss/python/langgraph/graph-api`
- arXiv 2407.01489 v2 (29 Oct 2024) — Agentless
- arXiv 2510.02389 v3 (4 Feb 2026) — T2L

---

## 3. Agentless / T2L 编排范式

### 3.1 Agentless(arXiv 2407.01489,Xia et al., v2 2024-10-29)

**图结构:严格线性三阶段,无 agent 循环。**

```
issue/bug report
   ↓
[1. Localization]  —— 漏斗式:文件级(file-level)→ 类级(class-level)→ 函数级(function-level)→ 行级(line-level)
   ↓                  每一级都是"看上一级结果 + 原始 issue,用 LLM 选下一级候选",无工具调用、无自主决策
[2. Repair]        —— 取 localization 的 top-k 函数/行,生成 N 个候选补丁(简单采样)
   ↓
[3. Patch Validation] —— apply 补丁 → 跑测试 → 过滤失败的 → 留下的即最终补丁
   ↓
final patch
```

关键设计:
- **不让 LLM 自主决定下一步动作**(`without letting the LLM decide future actions or operate with complex tools`)
- **无 ReAct 循环、无工具调用、无 sub-agent**
- 每阶段独立、可单独测试、成本极低($0.70/issue)
- SWE-bench Lite:**32.00%**(96/300),v1 时是 SOTA 开源 agent

**与 Hyperion 的对应**:
- Agentless Localization 漏斗 → Hyperion `localize` 节点(批3 已实现,文件:行号 锚点)
- Agentless Repair → Hyperion `delegate` 节点(委托 opencode 生成补丁)
- Agentless Validation → Hyperion `verify` 节点(R2 简化版只查 patch 非空;R5 加编译/测试)
- Hyperion 在两端加值:前置 `recall`(翻记忆)、后置 `memorize`(沉淀教训),这是 Agentless 没有的持续学习闭环

### 3.2 T2L(arXiv 2510.02389,Xi et al., v3 2026-02-04)

**图结构:渐进收窄 + evidence-guided refinement 循环。**

```
repo + crash trace / stack
   ↓
[ATA: Agentic Trace Analyzer]  —— 融合 runtime evidence(crash point、stack trace)→ 把"症状"翻译成"可行动的诊断"
   ↓
[AST-based chunking]            —— 按语法结构切仓,不是按行/按字符
   ↓
[Module-level localization]    —— 第一遍:粗
   ↓ ←─────── Evidence-Guided Refinement ──────┐
[Function-level localization]  ← 细化          │ 循环:上一轮找到的 evidence 指导下一轮
   ↓                                            │ 直到无新候选
[Line-level localization]      ← 再细化 ───────┘
   ↓
final vulnerable lines
```

关键设计(对比 Agentless 的进化):
- **不是单向漏斗,而是带反馈的 refinement 循环**:上一级的 evidence 指导下一级,如果下一级发现新 evidence,可以回到上一级扩大搜索
- **AST-based chunking**(而不是 Agentless 的纯文本切片):保留语法结构,适合代码语义
- **runtime evidence 优先**(crash point / stack trace 是硬信号,不是 LLM 猜的)
- **T2L-ARVO benchmark**:50 个真实 OSS 漏洞案例,5 个 crash 家族
- **58.0% detection / 54.8% line-level localization**

**与 Hyperion 的对应**:
- T2L 的 AST chunking → Hyperion 已有的 `code_index/parser.py`(tree-sitter 符号抽取,P1.0)+ `chunker.py`(符号边界切块,P1.1)— 完全对齐
- T2L 的 evidence-guided refinement 循环 → Hyperion R5 的 `localize` 自循环(§4.4)
- T2L 的 ATA(运行时证据融合)→ Hyperion `ingest` 节点扩展(日志/漏洞报告 → 线索摘要;R2 已占位 `nodes.py:43-49` 的"完整日志→线索摘要" backlog)

---

## 4. 对 Hyperion bug-RCA 七步的具体建议

### 4.1 R2 MVP:保持线性(结论:是)

**结论:R2 保持当前线性 StateGraph,不引入任何分支/循环/并行。**

理由(按重要性排序):

1. **业界基线背书**:Agentless(arXiv 2407.01489 v2)用纯线性三阶段在 SWE-bench Lite 拿 32% / $0.70,证明"线性管线 + 好的漏斗"在 bug-RCA 类任务上比复杂 agent 循环更有性价比。Hyperion 七步 = Agentless 三阶段 + 前置 recall + 后置 memorize,是 Agentless 的超集,线性结构天然合适。
2. **金标准对照优先**:R2 的核心验收是 demo2 金标准(wpa_supplicant 补丁 + 报告)。线性流水线每一步输出可独立检验、可重放、可 diff;引入分支/循环会让"为什么这次结果不同"的归因变复杂。
3. **当前实现已正确**:`src/hyperion/workflows/bug_rca/graph.py:35-42` 的七条 `add_edge` 就是线性,`graph.py:34` 注释也已写明"R2 单轮;verify 即使失败也继续出报告+记 lesson,故无分支"— 这是深思熟虑的选择,不要改。
4. **可观测性**:线性 = 单 trace = Langfuse/Greptile 上一条直线七个 span,排障最快。分支/循环会让 trace 变成树/图,排障成本指数上升。
5. **delegate 节点已经吸收了 ReAct 的价值**:opencode 本身是 ReAct coding agent,会自主调工具读码/试 patch。所以"外层 ReAct 化"是重复造轮子。

**R2 唯一可考虑的小优化**(不强制,留 backlog):
- `recall ∥ localize` 是否要并行?**R2 不做**。理由:recall 是 SQLite FTS5 + 向量查(R1 已实现,毫秒级),localize 是纯本地 tree-sitter(批3,秒级),两者串行总耗时本就小;并行引入的 superstep 协调复杂度不值得。R5 localize 变重(多轮 refine)时再做。

### 4.2 R5 生产级:四种增强(每种给触发条件 + 推荐原语)

#### 4.2.1 superstep 并行:recall ∥ localize_pre

- **触发条件**:R5 的 `localize` 拆成两段(`localize_pre` 粗漏斗 + `localize_refine` 细循环)后,`localize_pre` 与 `recall` 完全独立,可并行
- **推荐原语**:**superstep**(静态 fan-out,不用 `Send`)
- **实现**:
  ```python
  g.add_edge(START, "ingest")
  g.add_edge("ingest", "recall")          # fork
  g.add_edge("ingest", "localize_pre")    # fork
  g.add_edge("recall", "assemble")         # recall 完成等
  g.add_edge("localize_pre", "localize_refine")  # localize 走自己的链路
  g.add_edge("localize_refine", "assemble")
  g.add_edge("assemble", "delegate")       # join 在 assemble
  ```
  LangGraph 的 superstep 语义:`ingest` 出边到 `recall` 和 `localize_pre` 两条,两节点在同一 superstep 并发执行;`assemble` 有两条入边,会等两者都完成才触发。
- **为什么不用 `Send`**:任务数固定(就 2 个),`Send` 是给"运行时才知道 N"的 map-reduce 用的,过度设计。

#### 4.2.2 循环:localize T2L 式漏斗 refinement

- **触发条件**:R5 想对齐 T2L(arXiv 2510.02389)的"evidence-guided refinement"——上一轮找到的 evidence 指导下一轮扩大/收紧搜索,直到无新候选
- **推荐原语**:**`add_conditional_edges` + 节点自指**
- **实现**:
  ```python
  def _localize_router(state):
      if state["new_candidates_found"] and state["refine_iter"] < MAX_LOCALIZE_ITER:
          return "localize_refine"   # 自指,继续 refine
      return "assemble"              # 无新候选 or 超上限,出

  g.add_conditional_edges(
      "localize_refine",
      _localize_router,
      {"localize_refine": "localize_refine", "assemble": "assemble"},
  )
  ```
- **上限**:必须设 `MAX_LOCALIZE_ITER`(建议 3),否则可能死循环。T2L 原文是"两段粗→精",Hyperion 可放宽到 3 段(文件 → 函数 → 行)。

#### 4.2.3 动态 fan-out:delegate multi-candidate

- **触发条件**:R5 想做 multi-candidate voting —— 同一个 bug 让 delegate 生成 N 个候选补丁(不同 temperature / 不同 provider),verify 阶段投票选最优
- **推荐原语**:**`Send`**(动态 fan-out)
- **实现**:
  ```python
  def _dispatch_candidates(state):
      # state["candidate_specs"] 由 assemble 决定(如 3 个:omp/deepseek、omp/qwen、opencode/…)
      return [Send("delegate_one", {**state, "spec": spec}) for spec in state["candidate_specs"]]

  g.add_node("delegate_one", node_delegate_one)   # 单次委托
  g.add_conditional_edges("assemble", _dispatch_candidates)  # 动态 fan-out
  g.add_edge("delegate_one", "vote")               # join 在 vote 节点
  ```
  LangGraph 的 `Send` 语义:N 个 `Send` 在同一 superstep 并发执行,全部完成后才进 `vote`(类似 map-reduce 的 map 阶段)。
- **为什么不用 superstep**:候选数 N 是运行时由 `assemble` 决定的(根据置信度 / 成本预算),不是固定的。

#### 4.2.4 循环:verify 失败 → 回 delegate / localize

- **触发条件**:`verify` 节点成熟后(会真的跑编译/测试),失败时应该带 failure info 回退
- **推荐原语**:**`add_conditional_edges`** + **`Command(update=...)`** 注入 failure hint
- **实现**:
  ```python
  def _verify_router(state):
      if state["verified"] or state["verify_iter"] >= MAX_VERIFY_RETRY:
          return "report_memorize"
      # 路由策略:小修回 delegate,大修回 localize(漏斗没圈准)
      if state["failure_type"] == "patch_wont_apply":
          return "assemble"          # 带编译错误回 assemble,让 delegate 重写
      if state["failure_type"] == "wrong_root_cause":
          return "localize_refine"   # 漏斗没圈准,回 localize 扩大搜索
      return "report_memorize"

  g.add_conditional_edges("verify", _verify_router, {
      "report_memorize": "report_memorize",
      "assemble": "assemble",
      "localize_refine": "localize_refine",
  })
  ```
  回退时上一轮的 failure info 通过 state 字段(`verify_feedback`)传给目标节点;目标节点把它拼进 prompt(`assemble` 已经在做 prompt 组装,加一段即可)。

#### 4.2.5 人介入(可选,R5+)

- **触发条件**:高 blast-radius 补丁(改了多个核心文件)在 delegate 前想让人确认范围
- **推荐原语**:`interrupt()` + `Command(resume=...)`
- **实现**:`assemble` 节点末尾调 `interrupt({"anchors": state["anchors"], "prompt_preview": ...})`,图暂停,CLI/Web 展示给人 review,人按 y/n/修改后 `Command(resume=user_decision)` 续跑
- **不强制**:看产品定位。bug-RCA 强调"一条命令跑完",默认全自动更对路。

### 4.3 R2 StateGraph 结构建议(= 当前已有,无需改动)

```
START
  → ingest        (nodes.py:43)  初始化 scope,trigger 占位(完整日志摘要留 backlog)
  → recall        (nodes.py:52)  MemoryService.recall(top_k=5)
  → localize      (nodes.py:62)  Agentless 漏斗(批3)
  → assemble      (nodes.py:97)  渲染锚点 + 拼手术刀级 prompt
  → delegate      (nodes.py:127) 委托 opencode,output_schema 强契约
  → verify        (nodes.py:144) R2:patch 非空即过
  → report_memorize (nodes.py:152) 渲染报告 + 抽 BugLesson 入记忆
  → END
```

文件:`src/hyperion/workflows/bug_rca/graph.py:24-43`。

### 4.4 R5 StateGraph 结构建议(生产级目标)

```
START
  → ingest
  → [recall ∥ localize_pre]          # §4.2.1 superstep 并行
        ↓(join)
  → localize_refine                  # §4.2.2 T2L 漏斗自循环(条件边自指,MAX=3)
        ↓
  → assemble
        ↓
  → [delegate_one × N]               # §4.2.3 Send 动态 fan-out(R5 multi-candidate)
        ↓(join)
  → vote                             # 投票 / 选最优候选
        ↓
  → verify                           # R5:真跑编译/测试
        ↓
     ┌──── 条件分支 §4.2.4 ────┐
     │ verified=True → report_memorize
     │ failure=patch_wont_apply → assemble(带 failure hint)
     │ failure=wrong_root_cause → localize_refine(扩大漏斗)
     │ verify_iter ≥ MAX → report_memorize(强制出,带降级标记)
     └────────────────────────┘
  → report_memorize
  → END
```

R5 节点新增:`localize_pre`、`localize_refine`、`delegate_one`、`vote`。原 `localize` / `delegate` 拆分。`verify` 加条件出边。

### 4.5 编排决策表(对外层 vs 内层)

| 层 | 编排范式 | 理由 |
|---|---|---|
| **Hyperion 外层(bug-RCA workflow)** | 固定 StateGraph DAG(R2 线性 → R5 加并行/分支/循环)| 步骤确定、要对金标准、要可观测。Agentless 基线背书 |
| **Hyperion `delegate` 节点内部** | ReAct agent(opencode / omp,本身是 `create_agent` 式)| 读码、试 patch、回退重试是开放任务,适合 ReAct;deer-flow lead agent 同款 |
| **Hyperion demo agent(P0,`platform/agent.py`)** | ReAct(`langchain.agents.create_agent`)| 通用 chat,deer-flow 同款;与 bug-RCA 专用流水线并存,不冲突 |
| **Hyperion deep-research workflow(R3.2 ✅已建 2026-08-03)** | 看齐 deer-flow:外层固定阶段 workflow(ingest→index→plan→research→report→memorize)+ research 节点每模块 ReAct subagent(asyncio fan-out)。落地印证了本行判断:"查多模块"用 ReAct 子 agent,"出报告"是固定阶段 |

---

## 5. 已核实清单(不臆测)

- [x] **Hyperion langgraph 版本**:`1.2.9`(`.venv/lib/python3.12/site-packages/langgraph-1.2.9.dist-info/METADATA`),满足 `pyproject.toml` 的 `langgraph>=1.2`
- [x] **`StateGraph` / `add_node` / `add_edge` / `add_conditional_edges` / `compile` 可用**:`langgraph/graph/state.py:130`(class)、`:375`(add_node)、`:915`(add_edge)、`:969`(add_conditional_edges)、`:1164`(compile)
- [x] **`Send` 可用**:`langgraph/types.py:664`(class Send)
- [x] **`Command` 可用**:`langgraph/types.py:759`(class Command,generic)
- [x] **`interrupt` 函数可用**:`langgraph/types.py:811`(def interrupt)
- [x] **`Overwrite` reducer 可用**:`langgraph/types.py:938`
- [x] **Hyperion 当前 bug-RCA graph**:`src/hyperion/workflows/bug_rca/graph.py:24-43`(build_graph),线性七步,R2 单轮
- [x] **Hyperion 当前 nodes**:`src/hyperion/workflows/bug_rca/nodes.py`(七个 async/sync 节点 + OUTPUT_SCHEMA 契约)
- [x] **Hyperion demo agent 已用 `create_agent`**:`src/hyperion/platform/agent.py:15, 62`(与 deer-flow 同款 API)
- [x] **deer-flow lead agent 工厂**:`backend/packages/harness/deerflow/agents/lead_agent/agent.py:498, 526, 670, 751`
- [x] **deer-flow subagent 执行器**:`backend/packages/harness/deerflow/subagents/executor.py:395`(class), `:491`(_create_agent), `:260`(ThreadPoolExecutor)
- [x] **deer-flow SDK factory**:`backend/packages/harness/deerflow/agents/factory.py:64-170`(create_deerflow_agent), `:162`(create_agent), `:178-349`(14 中间件链)
- [x] **deer-flow 没有 planner/researcher/reporter 固定阶段**:仓库 grep 无业务级 StateGraph DAG;deep research 的"阶段化"是早期版本残留概念,当前版本是 ReAct lead + subagent delegation
- [x] **Agentless 论文**:arXiv 2407.01489 v2(2024-10-29),Xia/Deng/Dunn/Zhang(Illinois Tech),三阶段 localization → repair → patch validation,无 agent,SWE-bench Lite 32% / $0.70
- [x] **T2L 论文**:arXiv 2510.02389 v3(2026-02-04),Xi/Shao/Dolan-Gavitt/Shafique/Karri(NYU),progressively narrows + AST chunking + evidence-guided refinement + ATA,T2L-ARVO 58% detection / 54.8% line-level

---

## 6. 落地建议(给 R2/R5 实施者)

### R2(当前批次,无改动)
- **保持 `graph.py` 线性七步不变**
- 重点放在节点本身的成熟度:`localize` 漏斗质量(对齐 Agentless)、`delegate` 契约稳定性(对齐 demo2 金标准)、`verify` 真能不能验证(R2 只查 patch 非空是合理的最小实现)
- **不需要 import `Send` / `Command` / `interrupt`**——R2 全程只用 `add_edge`

### R5(生产级排期)
- 新增 `localize_pre` + `localize_refine`:把 `localize.py` 的漏斗从一次性改成可循环,加 `MAX_LOCALIZE_ITER=3`
- 新增 `delegate_one` + `vote`:把 `delegate` 节点拆成"单次委托"原语,multi-candidate 在外层用 `Send` 派发
- `verify` 升级:真跑 `git apply --check` / 编译 / 测试,产 `failure_type` 字段供条件分支消费
- `graph.py` 改用 `add_conditional_edges`,引入 `Command(update={"verify_feedback": ...}, goto="assemble")` 风格的回退
- **每加一条分支/循环,先加测试**:`tests/workflows/test_bug_rca_graph.py` 覆盖"verify 失败 → 回 assemble"、"localize 三轮无新候选 → 出"等关键路径

### 不建议做的事(排雷)
- **不要把外层换成 ReAct lead agent**。bug-RCA 是专用流水线,ReAct 会让"为什么这个 bug 没定位准"的归因变难。
- **不要在 R2 引入 `interrupt`**。bug-RCA 定位是"一条命令跑完",人介入留 R5+ 看产品定位再说。
- **不要在 `delegate` 节点内部用 LangGraph `Send`**。delegate 是 RPC 出去给 opencode,opencode 内部自己怎么并行是它的事,Hyperion 外层一个节点串行调一次就够;multi-candidate 是 R5 的事(R5 用 Send 在 Hyperion 外层派发多个 delegate_one)。
- **不要混用 `create_agent` 与手写 StateGraph**。Hyperion 外层 StateGraph 节点应该是"薄壳"——要么纯函数(读 state、返 dict),要么 RPC 出去(delegate),不要再嵌一个 LangGraph agent。
