# Hyperion — 架构设计(v2,产品重规划版)

> 状态:设计稿 v2(2026-07-28 产品重规划)· 目标代码库:bluez / wpa_supplicant 等(但**不限语言**,P1 调研支持任意代码仓)
> 语言:Python 3.12 · 框架:LangGraph + LangChain · 默认模型:DeepSeek(多 provider)
> 参考实现(只读):[deer-flow/](../../deer-flow/)、oh-my-pi、code-review-graph(见 §10)

> **v1(v0.1)的纠偏**:v0.1 是"先把三大场景的共享地基建深再接场景",**地基跑在了场景前面** → 过度设计。v2 重新定义产品为**调度型 agent**(编排 + 记忆 + 委托),差异化聚焦在「记忆 + 持续学习 + 精准调度」,不在重造一个 coding agent。已建的代码理解层(code_index P1.0–P1.5)作为**资产保留**,降级为记忆/检索的后端 + 调研/委托的上下文源。

---

## 0. 一句话定位(北极星)

> **Hyperion = 给系统软件代码库做"带记忆的 bug 根因定位 + 深度调研"的调度型 agent。差异化在「记忆 + 持续学习 + 精准调度」,不在重造一个 coding agent。**

**三大支柱:**
- **P1 代码仓深度调研**:任意语言仓库(bluez/wpa/deer-flow…)→ 详细准确的文档(架构 + 关键模块实现)。含开源 **PR 持续跟踪 + 合入建议**(R4)。
- **P2 bug 根因定位**:源码 + 日志/漏洞报告 → 根因 + **补丁 + 分析报告**。**重活委托**给成熟 coding agent(omp/opencode),Hyperion 负责调度。
- **P3 记忆与持续学习(★特色)**:把"代码库调研知识"和"bug 分析报告"沉淀成可检索、带溯源、团队共享、持续学习的记忆。

**解决用户三大痛点:** ① 记忆跨会话(不再每次从零);② 省 token(委托前组装手术刀级上下文,不整库 dump);③ 流水线(一条命令跑完"召回→组装→委托→验证→报告→沉淀")。

---

## 1. 核心架构决策(v2)

| # | 决策 | 理由 |
|---|---|---|
| **D1** | **调度型 agent**:重活委托给 omp/opencode,自做记忆+调度 | 差异化在记忆+调度,不在重造 coding agent;一人/数月预算要用在刀刃上 |
| **D2** | **平台 + 三工作流 + 共享服务** 三层分离(沿用 v0.1 骨架) | 代码理解/记忆/沙箱/检索三流共用,避免三套实现 |
| **D3** | bug-RCA **多阶段委托 + 迭代 verify-refine**(localize→repair 两阶段,同会话自审重试),结构化 JSON 契约,直出报告 | 用户要求"第一版别太复杂";R2 先两阶段(解 glm-5.2 单 loop 不收敛),R3.1 改 verify-refine 双循环(弃多候选采样投票;**2026-07-31 patch rerank 整体移除**);自动 PoC 放 R5 |
| **D4** | **记忆底座 = 自有 `MemoryService` 契约**(deer-flow MemoryManager ABC + oh-my-pi backend-swap 形状);v1 native 后端 = SQLite+FTS5+向量(复用 code_index embedder/reranker)+ CRG 结构路(可选 extra,默认关);cognee/mem0 可换 | 组合已有引擎避免第三套重叠检索栈;差异化(持续学习闭环)必须自己握住;零锁死 |
| **D5** | **委托抽象 `CodingAgentDelegate`**;v1 默认 opencode(2026-07-29 调研修正:omp 本机装不上 github 墙+bun),omp/claude 可换 | opencode 已装 v1.18.3 + `run --format json` 事件流绕结构化坑 + 原生 MCP client 反向查记忆;omp 的 strict schema 强校验是最大价值(待本机可用切入) |
| **D6** | MVP 先 **bug-RCA**(有 demo1/demo2 金标准可对照) | 一次验证记忆+委托+省 token+流水线四个痛点 |
| **D7** | LLM provider **反射 + 配置声明**,不硬编码厂家(沿用 v0.1) | 直接移植 deer-flow `use: module:ClassName`,加厂家零代码(见 §4.1,**已实现**) |

> v0.1 的 D4(LangGraph Store+mem0+Graphiti+LightRAG 四层)、D5(自建静态分析)、D6(仿真验证)在 v2 中**降级**:四层记忆 → 自有契约 + 可换后端;静态分析/log_symbolizer → 委托给 coding agent,v1 不自建(移 backlog);仿真验证 → R5 生产化再议。

---

## 2. 总体架构(编排 + 记忆 + 委托)

```
                    ┌────────────────── Hyperion(调度型 agent)──────────────────┐
用户/团队 ──CLI──▶  │  workflows/  三条工作流(R1 起真正落地)                      │
                    │    ├─ bug_rca      调度:召回→组装精确上下文→委托→出报告→沉淀  │
                    │    ├─ deep_research 代码仓→架构/模块文档(复用记忆+检索)       │
                    │    └─ pr_tracker   上游 PR 跟踪 + 合入建议(R4)              │
                    │                                                              │
                    │  services/memory/   ★记忆核心(P3,差异化)★                 │
                    │    MemoryService 契约 + 可换后端(v1=code_index+code-review-graph)│
                    │                                                              │
                    │  services/code_index/  (已有,保留)语义检索 L1 + clangd L2   │
                    │  tools/  (已有,保留)导航/沙箱工具 + 委托接口                │
                    │  platform/ (已有,保留)模型工厂/配置/反射/沙箱/可观测         │
                    └──────────────────────────┬───────────────────────────────────┘
                                               │ CodingAgentDelegate(抽象接口)
                          ┌────────────────────┼────────────────────┐
                          ▼                    ▼                    ▼
                   opencode run(默认)   omp -p(备选)         claude -p/SDK
                   (本地,已装 v1.18)    (本机未装,可换)      (可选高档后端)
                          │
                          └── 反向:MCP 把 Hyperion 记忆暴露给委托 agent 现场查
```

**记忆/委托/检索的正交关系(面向小白):**
- **记忆(memory)** = Hyperion 的"长期笔记本":跨会话累积"这个库长啥样、之前哪些 bug 怎么修的"。
- **委托(delegate)** = 把"读代码、写补丁"的力气活外包给 omp/opencode,Hyperion 只递上精确的上下文片段。
- **代码理解(code_index L1/L2)** = Hyperion 的"代码地图 + IDE 导航",既给记忆当检索后端,也给委托前组装上下文用。
- 三者通过 **MCP** 打通:delegate 干活时能反查 Hyperion 的记忆(见 §6)。

---

## 3. 三大支柱概览(详细设计见独立文档)

| 支柱 | 工作流 | 编排范式 | 核心能力 | 详细设计 |
|---|---|---|---|---|
| **P1 深度调研** | deep_research | 调度(delegate + 自有工具) | 代码仓→架构/模块文档;PR 跟踪(R4) | [deep-research-design.md](deep-research-design.md) |
| **P2 bug-RCA** ★MVP | bug_rca | 调度(召回→组装→委托→报告→沉淀) | 源码+日志/漏洞→根因+补丁+报告 | [bug-rca-design.md](bug-rca-design.md) |
| **P3 记忆/学习** ★特色 | (横切于 P1/P2) | MemoryService 契约 + 可换后端 | 持续学习、团队共享、带溯源 | [memory-design.md](memory-design.md) |

---

## 4. Harness / 平台层(✅ 已实现,沿用 v0.1)

> 这一层 v0.1 已建成生产级(`platform/`),v2 **原样保留**:模型工厂、配置、反射、沙箱、可观测。已是 deer-flow 同款设计。

### 4.1 模型工厂:多 provider 自动适配 ⭐(已实现 `platform/models.py`)

**核心思想**:不硬编码 provider。`config.yaml` 每个模型声明 `use: <module>:<ClassName>`,工厂 `create_chat_model` 用**反射**(`platform/reflection.py:resolve_class`)动态加载该 LangChain chat model 类。**加一家新 provider 通常零代码——只改配置。**

```yaml
# config/config.yaml(当前实际配置的快照;换 provider 只改这里)
models:
  - name: gpt-4.1                       # OpenAI
    use: langchain_openai:ChatOpenAI
    model: gpt-4.1
    api_key: $OPENAI_API_KEY
    base_url: https://api.openai.com/v1
    supports_vision: true
  - name: deepseek-v4-pro               # DeepSeek(OpenAI 兼容,性价比高,当前默认)
    use: langchain_openai:ChatOpenAI
    model: deepseek-v4-pro
    api_key: $DEEPSEEK_API_KEY
    base_url: https://api.deepseek.com
  # 任意 OpenAI 兼容网关(GLM/Kimi/火山/SiliconFlow/自建 vLLM)同形追加即可。
  # Anthropic / 本地 Ollama 见 config.yaml 里注释掉的样例(需 `uv sync --extra providers`)。
model_roles:                            # 角色→模型 路由(分层控成本;当前单模型,接入强模型后按角色分层)
  default: deepseek-v4-pro
  planner: deepseek-v4-pro
  locator: deepseek-v4-pro
  summarizer: deepseek-v4-pro
  verifier: deepseek-v4-pro
  memory_extractor: deepseek-v4-pro
  title: deepseek-v4-pro
```

- **反射加载器** `resolve_class(dotted, base)`:`"langchain_openai:ChatOpenAI"` → 真实类;缺包给安装提示而非 ImportError 栈。**已对齐 deer-flow** [resolvers.py](../../deer-flow/backend/packages/harness/deerflow/reflection/resolvers.py)。
- **工厂** `create_chat_model(name, role, thinking_enabled, **overrides)`:剥离元字段、thinking 归一化(OpenAI-compat 走 `extra_body`、Anthropic 走 `thinking`、vLLM 走 `chat_template_kwargs`)、base_url 归一化、未知字段告警、挂 Langfuse。详见 [platform/models.py](../../src/hyperion/platform/models.py)。
- **加新 provider**:标准 provider 只改配置;有非标字段(如 DeepSeek `reasoning_content`)写一个 `PatchedXxx(BaseChatOpenAI)` 子类。

### 4.2 配置系统(已实现 `platform/config.py`)

Pydantic-v2 + YAML;`$ENV` 解析(放 API key);`get_app_config()` 缓存 + 内容签名校验自动重载。子系统按 concern 拆(`LSPConfig` 等已落地)。对齐 deer-flow [config/](../../deer-flow/backend/packages/harness/deerflow/config/)。

### 4.3 工具注册与 MCP(已实现 `tools/registry.py`)

声明式 + 反射:`config.yaml` 的 `tools:` 每项 `use: <module>:func`,`get_available_tools()` 反射加载;`tool_groups` 分组(`web/code/memory/sandbox`)。MCP:v2 新增"把 Hyperion 记忆暴露给 delegate"的 MCP server(见 §6)。

### 4.4 沙箱执行(已实现 `platform/sandbox/`)

`Sandbox` ABC + `LocalSandboxProvider`(dev,宿主 FS)+ 预留 Docker/E2B。`env_policy` 刮掉 `*KEY*/*SECRET*/*TOKEN*`;命令超时 + pgid kill + 有界捕获。对齐 deer-flow [sandbox/](../../deer-flow/backend/packages/harness/deerflow/sandbox/)。工具:`bash/read_file/write_file/str_replace/ls/grep`(已挂)。

### 4.5 可观测性(已实现 `platform/tracing.py`)

Langfuse(自托管友好),无 env 时 no-op。约定 `langfuse_session_id=thread_id`、`langfuse_user_id=owner`。指南见 [langfuse.md](../已完成/langfuse.md)。

### 4.6 ⚠️ 平台 harness ≠ agent 运行时 harness(2026-07-29 决策修正)

本节 §4 是**平台 harness**(模型/配置/工具/沙箱/可观测),已实现。但**还有一层 agent 运行时 harness**(跑长 agent 的上下文管理:压历史/token 预算/截工具输出/并行子任务/断点续跑),对标 deer-flow 的中间件链 + OpenHands 3 层记忆。

**边界(关键,别搅混)**:① **coding 动作**(读写/命令/补丁)→ **委托** opencode/omp(§6);② **agent 运行时**(Hyperion 自己跑长 agent,如深度调研)→ **Hyperion 自建**(本节)。

**为什么必须自建**:深度调研(R3)是 Hyperion 自己的长 agent(读几千文件、多轮检索),不是 coding 任务、没法委托,上下文必爆——必须有运行时上下文管理压住它。v2 初版一度以为"重活全外包、runtime 也不做",**2026-07-29 用户修正:coding 外包,但 runtime 必须自建**。

**实现(R3.0 已落地)**:`platform/runtime/` 5 件已实现——factory(`create_hyperion_agent`)+ state(`HyperionState`)+ middlewares(token_budget/tool_output)+ context(tool_output_synopsis)+ checkpoint(SqliteSaver)。中间件 **pull-by-need**(不抄 deer-flow 30+,R3 仅 ~5 个);bug_rca graph 暂不用 checkpointer(线性 DAG,R3.2 deep_research 才真上);sandbox 虚拟路径映射**已简化掉**(tool_output 直接写本地 outputs_dir,留钩子备 R3.2 沙箱)。详见 [runtime-harness-design.md](runtime-harness-design.md);对标 [deer-flow-runtime-参考.md](../调研/deer-flow-runtime-参考.md)。

---

## 5. 共享服务层

### 5.1 代码理解服务 `services/code_index/`(✅ 已实现 P1.0–P1.5,资产保留)

**目标**:让 agent 能像 IDE 一样在大型代码库里"导航"——bug 定位、调研、PR 影响面的共同地基;**v2 还兼作记忆核心的语义检索后端**。

> **面向小白的类比——理解代码像查一栋大楼,分三层(叠加不替代)**:
> - **L1 向量检索**(大楼的"语义索引"):按**意思**模糊匹配,快、覆盖广。**已建成**。
> - **L2 LSP/clangd**(大楼的"精确导航"):像 IDE 精确列每一处调用点(宏展开/跨文件/系统头都准)。**已建成**。
> - **L3 DAP/lldb·gdb**(大楼的"现场勘查"):进程跑起来 attach,看此刻变量值/调用栈。**R3+/R5**。

**L1(P1.0–P1.3 已成)**:`parser.py`(tree-sitter 抽符号,Python 起步、**C 待加,R3 前补**)→ `chunker.py`(符号边界切块 + `fts_text` 拆词)→ `embed.py`(远端 DashScope 默认/本地可选)→ `store.py`(LanceDB 嵌入式,table-per-repo,原生 BM25+向量+RRF 混合)→ `retrieval.py`(cross-encoder rerank)。实测 **recall@5 = 0.65 达标**。
**L2(P1.5 已成,LSP/clangd)**:经 `multilspy` + 自写 `ClangdServer` 驱动 clangd,`find_references/goto_definition/hover`;硬前提 `compile_commands.json`。`get_callers/get_callees` **由 LSP 提供,取代原 code_graph 自建调用图**。
**L3(P2 末/P3,DAP)**:手写轻量 DAP client 驱动 `lldb-dap`/`gdb -i dap`,仅用于**可复现 bug** 的现场深挖(学术范式 ChatDBG;事后日志分析不适用)。

> 详细设计见 [p1-code-understanding-design.md](p1-code-understanding-design.md);演进依据见 [后续设计演进报告](../调研/后续设计演进报告-oh-my-pi与最佳实践.md)。
> **新增借鉴(Aider)**:R3 起新增 `repomap.py`——Aider 式 tree-sitter `tags.scm` → PageRank → token 预算"全仓最重要符号"地图,叠在 `parser.py` 上(见 §10/backlog)。

### 5.2 记忆与持续学习服务 `services/memory/`(★ P3 差异化,R1 新建)

> v0.1 是"LangGraph Store + mem0 + Graphiti + LightRAG 四层"。v2 改为**自有 `MemoryService` 契约 + 可换后端**,v1 后端 = 组合已有的 **code_index(语义)+ code-review-graph(结构)**;cognee/mem0 作可一键切换的备选后端(零锁死)。理由:组合已有引擎避免第三套重叠检索栈;持续学习闭环(差异化)必须自己握住。

**契约形状(照搬 deer-flow `MemoryManager` ABC + oh-my-pi backend-swap):** tier-1 `memorize/recall`;后端可换(丢 `backends/<name>/` + 配置 `memory.backend`)。**双接入**:workflow 内自动召回注入 + agent 工具自管(deer-flow 双模式)。

**知识项 schema(三类 `kind`,同一 `KnowledgeItem` 类):** `codebase_fact`(P1 调研)/ `bug_lesson`(P2 bug-RCA)/ `mental_model`(巩固升级出的稳定规则);每条带 commit SHA 溯源 + evidence[file:line]。整份报告作为 document 整体另存(可"翻回原报告")。

**持续学习闭环:** 调研→抽 `CodebaseFact`;bug-RCA→抽 `BugLesson` + 图边连相关 fact/历史 lesson;下一次先 `recall` 命中"这模式见过";巩固(借 mnemopi:去重/衰减/升级稳定事实)。

**多代码库/团队:** 每个 `(owner, codebase)` 一个 scope;native 后端 = SQLite + FTS5(知识项主表)+ 向量 blob(复用 code_index embedder)+ owner 字段做租户隔离。v1 单机,R4 多人。

> 详细设计见 [memory-design.md](memory-design.md)。

### 5.3 (裁剪)日志符号化 / 静态分析 → 移 backlog

v0.1 的 `services/log_symbolizer/`(addr2line/btmon/wpa)与 `services/static_analysis/`(sparse/smatch/coccinille)在 v2 **v1 不自建**——委托给 omp/opencode 后,日志/静态分析由 delegate 用自己的工具做。域工具(bluez/wpa 专用解析)后续按需再加。两条均记入 [backlog](../../.claude/memory/backlog-production-grade.md)。

---

## 6. 委托层 `CodingAgentDelegate`(★ v2 新增,R2 实现)

**为什么委托:** bug 定位 + 补丁合成正是成熟 coding agent 最擅长的;Hyperion 自建检索+推理+补丁引擎去拼 opencode/omp 会烧光预算在通用能力上。**Hyperion 的差异化在记忆 + 调度 + 团队知识,不在重造 agentic coding。**

**接口:** `CodingAgentDelegate.run(prompt, cwd, output_schema=None, *, agent=None, continue_session=False) -> DelegateResult`(`continue_session` 走 `opencode run --continue` 续同会话,是 verify-refine 双循环的承载;`agent` 指定子 agent)。后端:**`opencode`(v1 默认,已装 v1.18)**、`omp`(备选,本机未装)、`claude`(`-p`/SDK,可选)。配置 `delegate.backend` 切换(抽象接口从第一天起,三后端可换)。

**反向 MCP:** Hyperion 把 `memory` + `code_index` + `code-review-graph` 暴露为 MCP server,delegate 干活时现场查 Hyperion 累积的知识(不是 MCP 驱动 delegate,而是 delegate 查 Hyperion)。

> 详细(workflow 七步、结构化产出契约、delegate 选型对比)见 [bug-rca-design.md](bug-rca-design.md)。

---

## 7. 持续学习闭环(一等公民,横切 P1/P2)

- **Recall(读,入口)**:症状/问题 → memory 多路召回(语义 code_index + 结构 code-review-graph + 历史教训)→ 融合 → reranker 重排 → 只取 top-3~5 → 注入提示词(每条带 溯源+置信度+时间戳)。
- **Memorize(写,出口)**:报告 → 抽知识项 → 实体消歧 → 冲突合并(recency-wins + 显式失效)→ 设置信度 → 入 memory + provenance。write-time 严格过滤(只存根因/模式/规则,不存日志流水)。
- **清醒认知**:检索分数好 ≠ 记忆对。真正衡量是**合并策略让"该被召回的"存活下来**——所以必须有评测闭环(§9),否则记忆悄悄膨胀/污染。

---

## 8. 路线图(一人、数月、本地优先;v2)

| 阶段 | 目标 | 关键交付 | 退出标准 |
|---|---|---|---|
| **R0** | 本规划落地 | 重写 architecture.md v2 + 新建 memory/bug-rca/deep-research 设计文档;裁剪占位;更新 CLAUDE.md/记忆 | 文档自洽、断链修完;`uv run hyperion models/tools/lsp health` 仍绿 |
| **R1** | 记忆核心 v1 | `MemoryService` ABC + native 后端(code_index+code-review-graph)+ memorize/recall + MCP 暴露 + CLI `memory recall/add` | demo 报告抽成知识项存入、按语义+结构召回命中 |
| **R2** ✅MVP | bug-RCA 端到端 | `CodingAgentDelegate`(opencode **glm-5.2**)**多阶段委托**(localize→repair,见 [bug-rca-design.md](bug-rca-design.md) §7.5)+ **A+C**:自定义 agent(hyperion-localize/repair)+ `steps` 强制收敛 + `--continue` session 续接 + tolerant apply;报告 + 记忆闭环 | **2026-07-30 达标**:端到端 delegate 收敛,报告+补丁+BugLesson 入记忆(recall 命中) |
| **R3.0** ✅ | runtime 骨架 | `platform/runtime/` 5 件(factory/state/token_budget/tool_output/checkpoint)+ delegate 可观测(#56 流式+delegate_log) | 冒烟:中间件链+token 预算+checkpointer 生效;`hyperion models` 回归绿 |
| **R3.1** 🔧 | bug-RCA 硬化 | **workspace_changes(#51:git diff 观察补丁,已 e2e 验)** + **迭代 verify-refine(B,#54-rework:同会话双循环+verdict 自审+validate_patch 门控;patch rerank 2026-07-31 移除)**;trigger_parser(#53)/log_preprocess(#50)/方案A检索预筛/F2-eval 待做 | demo2 patch `git apply --check` 过 + verify-refine 双循环跑通 + report 标 METR 警示 |
| **R3.2** | 深度调研(P1 头条) | C parser + CRG 接入 + Aider repomap + runtime 正式上场(summarization/loop/子agent)+ deep_research workflow(多视角+事实一致性 rerank B) | `hyperion research --repo` → 带溯源架构/模块文档 + CodebaseFact 入记忆(recall 命中) |
| **R3.3** | 收尾 | opencode serve persistent(#55)+ report 精修(#46) | serve 长驻 session 精确续;report 对齐 demo 金标骨架 |
| **R3.4** | 文档摄取→记忆 | bug 报告/调研报告/补丁 → 分析 → 写记忆(PatchIngestPipeline:补丁 retrieve-then-summarize) | 三类文档 ingest→recall 命中;同根因去重合并 |
| **R4** | 团队/多代码库 + PR 跟踪 | 租户隔离、文档统一管理、PR tracker workflow、opencode 后端(团队分发) | 多 owner/多库互不串;PR 跟踪出合入建议 |
| **R5** | 生产化 | 对齐 deer-flow 边界处理、自动 PoC、仿真验证、可观测、backlog 逐条;**runtime 生产化**(checkpoint patches/双层记忆/sandbox ownership);**多候选 rerank 在 oracle 就绪后再评估**(不预建;测试套件/#50 repro 落地) | 按 backlog-production-grade 清单收敛 |

> **路线逻辑**:R1 记忆是 R2/R3 共同地基;R2 用 demo 金标准一次验证四痛点;R3 复用 R1 记忆 + R2 委托;R4/R5 扩展。**不 day-1 全上**——每阶段一个可验证场景。

---

## 9. 关键风险与对策

| 风险 | 对策 |
|---|---|
| 委托结果不稳定/锁 provider | 抽象 `CodingAgentDelegate` 接口,omp/opencode/claude 可换;锁结果 schema(JSON 契约)而非锁 provider |
| token 爆炸 | 委托前用 memory + code-review-graph blast-radius 组装**手术刀级上下文**;模型分层(便宜模型做抽取/摘要) |
| 记忆污染/膨胀 | write-time 严格过滤 + rerank top-3~5 + recency/confidence 降权 + 显式失效;借 mnemopi 巩固 |
| C 解析精度 | tree-sitter 容错主力 + clangd 精确补(`compile_commands.json`);R3 前补 `tree-sitter-c` |
| 真机复现难 | 委托给 coding agent 在沙箱编译/apply 验证;符号+日志级验证为主,真机最终确认 |
| LLM 自查不可靠 | 报告每条结论锚 file:line(证据纪律);可选对抗式二次委托验证 |

---

## 10. 参考项目(clone 优先级;URL 已核实,star 近似)

**P0 必 clone(核心参考):**
| 项目 | star | license | 借鉴什么 |
|---|---|---|---|
| [bytedance/deer-flow](https://github.com/bytedance/deer-flow) | ~47k | MIT | 架构主脊(单 agent + 中间件链)+ MemoryManager ABC + backend-swap(已本地;**无 Reporter/研究图**,cited-reporter 自建,见 deep-research-design.md) |
| [can1357/oh-my-pi](https://github.com/can1357/oh-my-pi) | — | MIT | 委托目标 omp + mnemopi 持续学习件(已本地) |
| [tirth8205/code-review-graph](https://github.com/tirth8205/code-review-graph) | ~26.5k | MIT | 结构图(blast-radius/社区/hub)+ 架构地图(已本地) |
| [Aider-AI/aider](https://github.com/Aider-AI/aider) | ~48k | Apache-2.0 | **repo-map**(`tags.scm`→PageRank→token 预算)→ 新增 repomap.py |
| [openautocoder/agentless](https://github.com/openautocoder/agentless) | ~2.1k | MIT | **分层定位漏斗**(file→function→line)→ 委托前预筛 |
| [SWE-agent/mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) | — | MIT | **ACI 工具契约**(swe-agent 已停维护,用活跃的最小化重写版)→ delegate 工具面规范 |
| [OpenHands/openhands](https://github.com/OpenHands/openhands) | ~82k | MIT | **3 层记忆**(Condenser→View→ConversationMemory)+ microagents |

**P1 值得 clone:** [mem0ai/mem0](https://github.com/mem0ai/mem0)(~55k,scoped 记忆,未来可换后端)、[getzep/graphiti](https://github.com/getzep/graphiti)(bi-temporal 知识图)、[assafelovic/gpt-researcher](https://github.com/assafelovic/gpt-researcher)(planner→reporter + 行内引用)、[plasma-umass/chatdbg](https://github.com/plasma-umass/chatdbg)(reproduce-then-debug,L3/DAP)、[nus-apr/auto-code-rover](https://github.com/nus-apr/auto-code-rover)(stratified+SBFL)、[continuedev/continue](https://github.com/continuedev/continue)(索引管线交叉验证)、[stanford-oval/storm](https://github.com/stanford-oval/storm)(多视角提问大纲)、[aorwall/moatless-tools](https://github.com/aorwall/moatless-tools)(大 C 仓向量索引 + eval)。

**P2 读文档/论文:** cognee、Sourcegraph Cody(SCIP/clangd)、Cline Memory Bank、nano-graphrag;**论文必读(无代码)**:**T2L-Agent**(arXiv 2510.02389,直接在 bluez/wpa 评估)、**Code Researcher**(arXiv 2506.11060,commit-log 因果做 C/C++ RCA)。

**跳过(不 fit):** letta/MemGPT、MemOS、microsoft/graphrag(DeepSeek 缺 response-schema,issue #2200)、getzep/zep(已废弃)、sweep(AGPL+停更)、Roo-Code(扩展已关)、langmem。

**其他参考(沿用 v0.1):** LangGraph Send/Store/Platform、langgraph-supervisor、SWE-bench、btmon wiki、wpa_supplicant 代码结构、PR-Agent、LongMemEval(ICLR 2025)。详见 [backlog](../../.claude/memory/backlog-production-grade.md)。

---

## 11. 项目结构(v2)

```
Hyperion/
├── src/hyperion/
│   ├── platform/         # ✅ 平台 Harness(已实现):models/config/reflection/sandbox/tracing/agent
│   │   └── runtime/      # ✅ agent 运行时 Harness(R3.0):中间件链(token_budget/tool_output)+checkpointer → runtime-harness-design.md
│   ├── services/
│   │   ├── code_index/   # ✅ 代码理解(已实现 P1.0–P1.5):L1 向量+L2 LSP+outline+eval
│   │   ├── memory/       # ✅ 记忆核心(R1):MemoryService 契约 + native 后端 + recall/memorize
│   │   ├── workspace/    # ✅ bug workspace(R3.1 #51):create_workspace + validate(Tier0)→ workspace-design.md
│   │   └── log_preprocess/ # 🆕 大日志预筛(R3.1 待做):grep+时间窗+addr2line+折叠 → delegate/context.md
│   ├── workflows/        # bug_rca ✅(R2 MVP + R3.1 B)/ deep_research 🆕(R3.2)/ pr_tracker 🆕(R4)
│   ├── tools/            # ✅ 导航/沙箱/code_nav/memory 工具 + CodingAgentDelegate(R2)
│   └── cli.py            # ✅ 入口(models/tools/lsp/memory/mcp 已实现;bug-rca 已实现;research 待 R3.2)
├── config/               # config.yaml(声明式)+ extensions_config.json
├── docs/                 # 已完成/· 调研/· 设计/(本文件在此)
├── example/              # demo1/demo2 金标准(输入+补丁+报告)
├── deer-flow/ · oh-my-pi/ · code-review-graph/ · agentless/ · opencode/   # 只读参考(各自 clone,.gitignore)
└── .claude/memory/       # 项目记忆(随 git)
```
