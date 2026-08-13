# Hyperion 架构评估 + 改进路线(2026-08-12)

> **本文档是活的评估清单**。每条建议/功能点带 `[ ]` 状态框 + 优先级 + 触发条件,后续"挨个完成"时直接勾掉 + 记 commit。落盘后即作为单一真相来源,CLAUDE.md 低优 backlog 与 `.claude/memory/` 交接只引本文档锚点。
>
> **评估时间**:2026-08-12 · **作者**:Claude(模型 glm-5)
> **评估方法**:① 两个 Explore agent 全仓 file:line 测绘(上下文/中间件 + 记忆模块)② WebSearch 调研 2025-2026 前沿(arXiv 核验)③ 对照 deer-flow 35 级生产中间件链 ④ 过 YAGNI + 三支柱筛。

---

## 评估标尺与对标对象

| 维度 | 标尺 | 来源 |
|---|---|---|
| **上下文工程** | Write / Select / Compress / Isolate 四策略 | LangChain「Context Engineering for Agents」(2025-07-02);Anthropic「context as finite resource」 |
| **记忆系统** | 抽取/合并/检索 + 图增强;只追加 + 衰减排序 | Mem0(arXiv [2504.19413](https://arxiv.org/abs/2504.19413),2025-04-28,核验过);Cognee 1.0;Graphiti(双时态 KG) |
| **中间件链** | 35 级生产链(InputSanit→Budget→Sanitize→…→TerminalResponse) | deer-flow `backend/AGENTS.md` 中间件链章节 |
| **代码检索** | broad context → focused analysis → dependency traversal | Augment Code 2025 分层检索律;Code Researcher(ICLR 2026) |
| **本仓约束** | 三支柱(代码情报 / 记忆 / skill+工具)+ 不编译不复现 + YAGNI | CLAUDE.md 路线复核(2026-08-07) |

**前沿核验记录**(引 arXiv 前必查):
- ✅ Mem0 `2504.19413`(2025-04-28):"dynamically extracting, consolidating, and retrieving";graph 变体;"91% lower p95 latency, >90% token cost saving"。**本评估 §三 引用的数据准确。**
- (Cognee 1.0 79% BEAM、Graphiti 双时态 KG 来自 WebSearch 摘要,未逐篇核 PDF —— 落功能 3 前补核。)

---

## 一、Harness 整体设计

**结论:定位准、骨架对,但「主战场」反而最裸。** 3 条判断:

### 1.1 定位转型是对的 ✅

从「自己跑 bug-RCA 流水线」转成「记忆 + 代码情报 + skill 的 MCP tool server,重活委托 opencode」。与 Anthropic 多智能体研究结论一致(*"subagents with isolated context windows outperformed single-agent"*),避开「跟成熟 coding agent 抢重活」的死路。**差异化 = 委托 + 精准召回,不是调度本身。**

### 1.2 骨架对齐主流 ✅

- [factory.py:76-108](../../src/hyperion/platform/runtime/factory.py) `create_hyperion_agent` 是 langchain `create_agent` 的薄包装。
- [state.py:91-108](../../src/hyperion/platform/runtime/state.py) `HyperionState` 是 `TypedDict(AgentState)` + 自定义 reducer(`merge_delegations` cap=50)。
- `state_schema` 合并交给 `create_agent` 自动做([factory.py:8-10](../../src/hyperion/platform/runtime/factory.py) 注释:刻意不移植 deer-flow `normalize_middleware_state_schema`)。
- 标准 LangGraph 做法,没造轮子。

### 1.3 ⚠️ 结构性张力:上下文保护最重的链,只挂在 `deep_research`;MVP 主战场 `bug_rca` 最裸

| 路径 | 是否过预算中间件 | 真实使用 |
|---|---|---|
| `deep_research` | ✅ 完整 5 中间件 + TurnBudget + 强制摘要降级 | [research.py:240](../../src/hyperion/workflows/deep_research/_research.py) `create_hyperion_agent` 唯一调用点 |
| `bug_rca`(MVP 主战场) | ❌ pivot 后是 opencode 子进程,Hyperion 不包其 I/O | [delegate.py](../../src/hyperion/tools/delegate.py) 只做 64KB 分块读 + 全量落盘 |

→ **最需上下文保护的 bug 定位流程,主演员(opencode)没有 Hyperion 的预算中间件兜底。** 这是诚实的设计边界(delegate 的上下文是 opencode 自己的事),但要在对外讲法里讲清:**Hyperion 的 Compress 策略只覆盖自家 agent(deep_research),不覆盖 delegate。**

---

## 二、上下文管理(四策略逐项体检)

**能压缩上下文吗?——能,但只压「一半」,且触发器偏粗。**

| 策略 | 现状 | 评分 | 锚点 |
|---|---|---|---|
| **Write**(写出窗口) | MemoryService 跨会话写盘 + delegate 全量 stdout 落 `delegate_log` | ✅ | [delegate.py:341-369](../../src/hyperion/tools/delegate.py) |
| **Select**(按需拉入) | emit-concept + references-over-payloads;MCP 工具只回符号/路径不回文件体;`repo_map` 显式 token 预算 | ✅ 强 | [mcp_memory.py:184-226](../../src/hyperion/tools/mcp_memory.py) |
| **Compress**(压缩) | SummarizationMiddleware(token 触发,建议 B)+ ToolOutputBudgetMiddleware(>30K 外置 + wrap_model_call 历史兜底,建议 C)+ `_compact_evidence` 降级 | ✅ | [factory.py:68](../../src/hyperion/platform/runtime/factory.py);[tool_output.py](../../src/hyperion/platform/runtime/middlewares/tool_output.py) |
| **Isolate**(隔离) | 多 sub-agent 各自上下文;state 字段隔离 | ✅ | [research.py:300](../../src/hyperion/workflows/deep_research/_research.py) Semaphore |

### 2.1 Compress 策略的三个真实短板

**短板 1:摘要触发是「固定 50 条消息」,不是 token 感知。** ✅ 已治(建议 B,2026-08-12)
[factory.py:68](../../src/hyperion/platform/runtime/factory.py) `trigger=("messages",50), keep=("messages",20)`。deer-flow 的 SummarizationMiddleware 绑 token 预算触发;Anthropic auto-compact 在 95% 窗口触发。**50 条大消息(塞了 diff)会过晚压、50 条小消息会过早压**——消息数 ≠ token 数。
→ **建议 B**(见 §五)。**已落地**:`trigger` 改 `("tokens", 32000)` + `SummarizationConfig` dataclass;详见 §五 建议 B 落地段。

**短板 2:历史 ToolMessage 兜底缺失(已知 TODO)。** ✅ 已治(建议 C,2026-08-13)
[tool_output.py:13-14](../../src/hyperion/platform/runtime/middlewares/tool_output.py) 原 TODO:「R3.0 只做新工具结果的预算…历史 ToolMessage 截断推到 R3.2」。即 `wrap_tool_call` 只处理**新产出**的工具结果,历史里漏网的大消息(断点续跑 / 改过阈值 / 旧 checkpoint 恢复)无人兜底。
**校正(本会话调研):**「synopsis 累积」并非靠 ToolMessage 二次压缩治 —— deer-flow 生产级也**不二次压 synopsis**(synopsis ~3K ≪ fallback 阈值 20K,到不了截断线),累积完全靠 SummarizationMiddleware 在 token 触发时整体摘要掉(= 建议 B 已做)。真实缺口 = `wrap_model_call` 兜底钩子(deer-flow 有、Hyperion 原缺)。
→ **建议 C**(见 §五)。

**短板 3:`bug_rca` 主路径(delegate)完全不过任何预算中间件。**
[delegate.py](../../src/hyperion/tools/delegate.py) 只做两件事:64KB 分块读 stdout(防 readline 限制,踩坑#3)+ 全量落盘存档(可观测,非压缩)。opencode 自己的上下文管理 Hyperion 管不着。**这是诚实的设计边界,不是缺陷。**

### 2.2 Select 策略——最强一块,真对齐前沿 ✅

LangChain 引 Windsurf 工程师原话(*"embedding search becomes unreliable as codebase grows… must rely on grep + knowledge graph + rerank"*)。Hyperion 的 `search_codebase` 正是此打法:
- 只回索引内真实符号(防幻觉)+ file:line + 前 120 字摘录,top_k=5 上界。
- 所有工具统一 `body[:8000]` 截断;`repo_map` 塞 2048 token 预算([mcp_memory.py:406](../../src/hyperion/tools/mcp_memory.py))。
- = "references over payloads",对齐 Claude Code「弃向量库改 agentic search」。
- **这块不用改,是优点。**

---

## 三、记忆模块(实现扎实,一个扩展悬崖要盯)

### 3.1 架构(全锚 file:line)

- **契约分层清晰**:[manager.py](../../src/hyperion/services/memory/manager.py) `MemoryService` ABC 三级(Tier-1 abstract / Tier-2 default `NotImplementedError` / Tier-3 optional),后端可换(cognee/mem0 预留),`resolve_backend_class` 拒绝静默回退([manager.py:147-149](../../src/hyperion/services/memory/manager.py))。✅ 生产级契约。
- **native 后端**:[store.py](../../src/hyperion/services/memory/backends/native/store.py) SQLite + **FTS5 外部内容表** + 向量 BLOB(float32)。27 列,bi-temporal(`valid_at`/`invalid_at`),WAL + `BEGIN IMMEDIATE` 串行写,写锁 `threading.Lock`。✅
- **4 路召回融合**:[recall.py:126-193](../../src/hyperion/services/memory/backends/native/recall.py) BM25 / 向量 / code(code_index) / structural(CRG),RRF(K=60)→ rerank → 时间衰减 + 置信度加权。**= Mem0 思路(抽取/合并/检索)的更完整版**(多了 structural 一路)。
- **只追加 / mem0 v3 风格**:[memorize.py:109-129](../../src/hyperion/services/memory/backends/native/memorize.py) R3.5 起冲突结论双方都留 active,recall 按衰减「新为主、旧参考」,不再写入即 supersede。✅ 对齐 Mem0。
- **内容寻址 ID**:[schema.py:113-120](../../src/hyperion/services/memory/schema.py);patch id 按 diff 内容算([ingest.py:412-415](../../src/hyperion/services/memory/ingest.py))——同补丁二次入库自然合并。✅ 巧妙。
- **数据模型**:`codebase_fact`(调研)/`bug_lesson`(RCA)/`mental_model`(巩固提升);`SourceTier` 6 档权重 0.5–1.0;`Scope(owner,codebase)` 租户分区。

### 3.2 ⚠️ 两个真实短板

**短板 1(扩展悬崖):向量检索是 O(N) 内存全量余弦,无 ANN。** ✅ 已治(建议 A,2026-08-13)
[store.py:387-413](../../src/hyperion/services/memory/backends/native/store.py) 把 scope 内所有 active embedding 行 load 进内存逐条算 `np.dot`。DDL 注释(store.py:14-15)自承「几百条没问题,ANN 在 backlog」。
- **关键张力**:`code_index` 已用 **LanceDB(有原生 ANN)**,但记忆向量没用它 —— 可统一的点。
- 记忆条数上千后,每次 recall 全量扫描 → p95 延迟劣化(Mem0 论文核心卖点之一就是 91% lower p95 latency)。
→ **建议 A**(见 §五),**优先级最高**。**已落地**:渐进式 sqlite-vec —— `count(scope)>ann_threshold(默认 500)` 切 vec0 KNN(cosine metric,partition_key=owner+codebase 隔离),否则现状 Python loop(benchmark 实测 N<200 loop 更快、N>500 vec0 快 2-4×;numpy 向量化已证死路)。双路径阈值切换 + 加载失败降级纯 loop(绝不崩)。详见 §五 建议 A 落地段。

**短板 2:中文 FTS5 分词弱。**
[store.py:97](../../src/hyperion/services/memory/backends/native/store.py) `tokenize='unicode61 remove_diacritics 2'` 不做中文分词。纯中文关键词召回打折,靠向量路径兜底。
→ 标 backlog(中文 jieba 分词或 FTS5 `trigram` tokenizer),触发条件 = 中文召回投诉。低优。

---

## 四、工具与中间件盘点

### 4.1 中间件:5 级(全挂全测),deer-flow 的零头

实例化的([factory.py:63-73](../../src/hyperion/platform/runtime/factory.py)):

| # | 中间件 | deer-flow 对应 | 备注 |
|---|---|---|---|
| 1 | `ToolOutputBudgetMiddleware` | #2 | 最厚:281 行 + 651 行 synopsis(移植自 deer-flow) |
| 2 | `SummarizationMiddleware`(langchain 内置) | #18 | 触发器偏粗(短板 1) |
| 3 | `LoopDetectionMiddleware` | #28 | warn@3 / stop@5 / window=20 |
| 4 | `TurnBudgetMiddleware` | — | 自创(治踩坑#9:superstep≠轮) |
| 5 | `TokenBudgetMiddleware` | #29 | warn@0.7 / hard@1.0 |

deer-flow 另外 30 个(`InputSanitization`/`Sandbox`/`Authorization`/`ReadBeforeWrite`/`DurableContext`/`SkillActivation`/`MemoryMiddleware`…)在本仓只是 [factory.py:44-61](../../src/hyperion/platform/runtime/factory.py) spine 注释里的**扩展槽**,无代码。

**YAGNI 分流:**
- **合理 YAGNI(别建)**:`Sandbox`(R5 砍,与「不编译」冲突)、`Authorization`/多租户(R4 砍,本地 harness 不需要)、`Skill*`(opencode 原生发现 .claude/skills/ 已工作)、`Uploads`/`ViewImage`/`Title`(harness 无 UI)。**砍得对。**
- **可能值得补**:`MemoryMiddleware`(记忆自动注入,deer-flow #22)、token 感知摘要触发。

### 4.2 工具:15 个 MCP,统一「薄工具 + skill 编排」路线

[mcp_memory.py](../../src/hyperion/tools/mcp_memory.py) 15 工具全薄封装:
- **代码情报**(7):`search_codebase`/`call_chain`/`repo_map`/`repo_overview`/`blast_radius`/`cross_version_diff`/`merge_eval`
- **记忆**(3):`memory_recall`/`memory_memorize`/`memory_dump`
- **硬门 + 落盘**(5):`validate_patch`/`export_patch`/`export_report`/`fetch_patch`/`ensure_repo`

复杂编排(7 步 backport / 3 阶段 compare / 4 阶段 onboarding / 5 步记忆体检)放 skill 不放工具。✅ 对齐踩坑#2 + LangChain「tool selection RAG 在 >20 工具才有意义」(现 15 个,YAGNI)。

---

## 五、改进建议(按性价比排序,可追踪)

> 优先级:🥇 硬刚需 / 🥈 高性价比 / 🥉 按触发 / 📋 长期。
> 完成后把 `[ ]` 改 `[x]` + 记 commit。

### [x] 🥇 建议 A:记忆向量换 sqlite-vec ANN(渐进式 · 补扩展悬崖)✅ 2026-08-13

- **痛点**:§3.2 短板 1。记忆向量 O(N) 全量余弦,无 ANN。
- **做法(调研后校正)**:**不迁 LanceDB**(store docstring 明确拒绝:SQLite 关系操作是 KI 核心;LanceDB 留 code_index)。改用 **sqlite-vec**(SQLite 扩展,同栈零冲突,0.1.9 已在 site-packages)。调研三关键:① deer-flow 生产级纯 BM25 零向量(这规模段 ANN 易过度设计);② numpy 向量化是死路(benchmark 实测:瓶颈在逐行 frombuffer 解码 BLOB,不在循环);③ sqlite-vec benchmark 实测 N>500 稳定快 2-4×、N<200 反慢 3× → **双路径阈值切换**。
- **落地**:`search_vector` 检测 `count>ann_threshold(默认 500)` → vec0 KNN(cosine metric,distance=1-sim 转换误差<1e-7),否则现状 loop。延迟建表(镜像 code_index `_open_or_create` 维度探测);`upsert` 同事务双写 vec0(DELETE+INSERT,vec0 无 ON CONFLICT);partition_key=owner+codebase 硬隔离 KNN;active/repo 过滤 KNN 后回主表做(over_fetch=limit×4 补漏)。`auto_index:bool` + `ann_threshold:int` 开关,加载失败降级纯 loop(绝不崩)。**校正 1 处探针盲点**:sqlite-vec 默认 metric 是 L2 非 cosine → 建表须显式 `distance_metric=cosine` + 跳过零向量(cosine 未定义)。5 单测(延迟建表+双写/阈值分流/KNN 与 loop 召回一致/partition 隔离+active 过滤/auto_index=False 降级)+ 全记忆 42 绿。详见 [suggestion-a-handoff](../../.claude/memory/suggestion-a-sqlite-vec-ann-handoff.md)。
- **触发条件**:已满足(count>500 自动切;现真机规模未到但代码就绪)。

### [x] 🥈 建议 B:摘要触发改 token 感知(小改动 · 直击短板 1)✅ 2026-08-12

- **痛点**:§2.1 短板 1。[factory.py:68](../../src/hyperion/platform/runtime/factory.py) `trigger=("messages",50)` 不 token 感知。
- **做法**:SummarizationMiddleware 触发条件改绑 token 预算(对齐 deer-flow #18 / Anthropic auto-compact@95%)。langchain 新版 SummarizationMiddleware 支持 token-based trigger;无则自定义子类。
- **触发条件**:可立即做(改动小)。
- **验证**:塞大消息(含 diff)的轨迹不过早撑爆、不过晚压;单元测试覆盖。
- **预估**:小改动。
- **落地**:`trigger=("tokens", 32000)`(对齐 deer-flow 生产默认 tokens:32000)+ 新 `SummarizationConfig` dataclass(enabled/trigger_tokens/keep_messages)。langchain 1.3.14 原生支持 token trigger,零自写子类(踩坑#2)。**实测 `("fraction",F)` 排除**:Hyperion 三模型 ChatOpenAI profile=None,用 fraction 构造时 raise ValueError 让 agent 崩。`keep` 保持 messages 计数(YAGNI)。不进 config.yaml(token_budget/tool_output 的 yaml 当前都没 wire,避免造死配置,对齐 turn_budget 先例)。2 单测(`test_factory_summarization_trigger_is_token_aware`/`..._disabled_skips`)+ 全 runtime 26 绿。

### [x] 🥉 建议 C:补 wrap_model_call 兜底历史漏网大消息(对齐 deer-flow · 校正短板 2)✅ 2026-08-13

- **痛点**:§2.1 短板 2。[tool_output.py:13-14](../../src/hyperion/platform/runtime/middlewares/tool_output.py) 原 TODO。
- **做法(校正后)**:**不**二次压 synopsis —— 调研发现 deer-flow 生产级也不做 synopsis 二次压缩(靠 SummarizationMiddleware = 建议 B 治累积)。真实增量 = 补 `wrap_model_call` 钩子(deer-flow 有、Hyperion 原缺):每轮预扫描历史 ToolMessage,超 `fallback_max_chars`(20K)的漏网大消息做 head+tail 兜底截断(不外化);synopsis(~3K)不动。预扫描无超阈值 → 返 None 不重建 list(零开销)。
- **触发条件**:断点续跑 / 改过阈值 / 旧 checkpoint 恢复时历史混进未处理大消息。
- **落地**:`_budget_content` + `_patch_tool_message` 加 `externalize: bool = True` 参数(历史路径传 False → 跳过外化只走 fallback);新 `_is_over_fallback` 预扫描判据 + `_patch_model_messages`(抄 deer-flow:539-565 预扫描模式);`ToolOutputBudgetMiddleware` 加 `wrap_model_call`/`awrap_model_call`(抄 loop_detection:233-238 `request.override(messages=...)` 先例)。**不改 factory 装配**(中间件已在链里,加方法即生效)。3 单测(截断漏网大消息 / 不外化 / 干净历史返 None)+ 全 runtime 29 绿。详见 [suggestion-c-handoff](../../.claude/memory/suggestion-c-tool-output-wrap-model-handoff.md)。

### [x] 🥉 建议 D:记忆巩固自动触发(对齐「持续学习」卖点)✅ 2026-08-13

- **痛点**:`consolidate()`([consolidate.py:23-32](../../src/hyperion/services/memory/backends/native/consolidate.py),access_count≥3 升 `mental_model`)已实现但**无人调**。Cognee 1.0 主打 self-improving / 自动巩固。
- **做法**:加 `hyperion memory consolidate` CLI 命令;或 recall 后异步触发(access_count 达阈值即 promote)。
- **触发条件**:P3 记忆支柱核心卖点,临门一脚缺;可立即做(CLI 薄封装)。
- **预估**:小改动(CLI 子命令 + 接已有 consolidate())。
- **对标**:Mem0「consolidating」、Cognee「self-improving」。
- **落地 + 更正误判**:本会话实测发现「无人调」是**误判** —— ① `hyperion memory consolidate` CLI **早 wire**(cli.py:276);② recall **早就在 bump**(recall.py:186-190 `bump=True` → `store.bump_access`);③ e2e 实测全链 GREEN(写→recall×3→access_count=3→consolidate→promoted=1→mental_model)。**真正缺的只是「自转」**:recall 不会顺手 consolidate,得人手动敲 CLI。已补:`NativeMemoryService.recall` 命中 memory 路条目时,`asyncio.create_task` fire-and-forget 跑一次 `_safe_consolidate`(失败只记日志,不拖慢/崩 recall);`NativeMemoryConfig.auto_consolidate: bool = True` 开关(复用 `promote_access_count` 阈值,不造新阈值)。不挂 `search()`(它 `bump=False` 无信号)。3 单测(e2e 全链锁住 + 自转 + disabled)+ 全记忆 37 绿。详见 [suggestion-d-handoff](../../.claude/memory/suggestion-d-memory-consolidation-handoff.md)。

---

## 六、还能做什么功能(前沿对标 · 严过 YAGNI 筛)

> **已砍/不做**(R4/R5 或 YAGNI):Docker 沙箱、前端、多租户鉴权、artifacts 单建、工具选择 RAG(才 13 工具)、跨 codebase 联合图(各版图独立,语义配对靠 agent)。
> 以下 3 个**落在三支柱内 + 有前沿背书**。

### [x] 🥈 功能 1:代码 onboarding 导览 skill(填单仓调研空白)✅ 2026-08-13

- **现状**:5 skill(backport/bug-rca/patch-review/upstream-merge/compare)全 bug/补丁/对比导向,**无一「给新 contributor 讲清单 codebase 架构」纯调研型**。compare 是跨版本,非单仓入门。
- **做法(路线修正)**:原写「0 新工具」列了 `architecture_overview`/`hub_nodes`/`communities`,但**这三个是 CodeGraph 已实现的方法([code_graph.py:568-593](../../src/hyperion/services/code_index/code_graph.py#L568-L593)),不是 MCP 工具**(原「0 新工具」前提错,规范作者把底层方法误当现成工具)。用户拍板:**加第 14 个薄工具 `repo_overview`** wrap 这三个 + `bridge_nodes`(同 analysis.py 家族,~0 额外代码)。onboarding 是第一个真需「模块/耦合」视角的 skill(bug-RCA/compare 要的是具体调用链不是模块布局)。**这是生产级正确划分** —— `repo_map`=符号层(PageRank 最重要的函数)、`repo_overview`=架构层(社区/模块边界+枢纽+瓶颈),分层检索。
- **前沿对标**:Augment Code 2025「broad context first → focused analysis → dependency traversal」分层检索律;Code Researcher(ICLR 2026);theroadtoenterprise 2026-05 六阶段 onboarding 循环 **map→stack→patterns→trace journey→spot→document**(phase1「先看项目形状再读码」= repo_overview+repo_map;phase4「trace one real journey end-to-end」= call_chain+read)。
- **落地**:1 薄工具 `repo_overview`(聚合 architecture_overview/communities/hub_nodes/bridge_nodes 四方法一次返,纯图查询无 LLM,图驱动防幻觉)+ 1 `onboarding` skill + 1 `hyperion-onboarding` agent block(steps 24,read-only)。镜像 compare:**memorize 读码即记**(架构是纯读码事实不需等用户验证,同 compare 区别于 bug/补丁型)+ **recall-first 短路**(命中同 codebase 同主题导览事实直接复用出报告,「下次秒答」)+ 核心难点「挑哪条旅程是语义判断」(默认 hub_nodes 排第一,用户指定优先)。2 单测(图未建降级 / 假图聚合+格式化+top_n 透传)+ 全 mcp_tools 25 绿。详见 [onboarding-handoff](../../.claude/memory/onboarding-skill-handoff.md)。
- **YAGNI**:落「①代码情报 + ③skill」,纯读不编译。✅
- **形态**:镜像 compare/upstream-merge(单 `SKILL.md` + opencode agent block,read-only 权限)。

### [x] 🥈 功能 2:记忆体检 skill(团队知识转移 · 差异化卖点)✅ 2026-08-13

- **现状**:`memory_recall` 是 query 式(得先知道问啥),无「把某模块/符号所有记忆 + 置信度 + 溯源一次性摊开」能力。
- **做法(路线确认)**:**加第 15 个薄工具 `memory_dump(kind?, include_invalid?, codebase?)`** —— 包已是契约的 `MemoryService.list_items`([manager.py:61](../../src/hyperion/services/memory/manager.py#L61) + [service.py:161](../../src/hyperion/services/memory/backends/native/service.py#L161)),每条渲染成溯源卡(confidence/source_tier/evidence file:line/commit_sha/bi-temporal STALE/access_count)。**0 新服务代码,只差 MCP 薄封装。** skill 用它体检:摊全量 → 逐条读 → 聚四类健康信号(溯源弱/待巩固/已过期/未决矛盾)+ 建议。
- **非 spec-drift(区别于功能1)**:本会话查实确实无浏览/导出工具(现 memory_recall query / memory_memorize write 两件套),`list_items` 早已是契约只是没暴露 —— 故加 1 工具是对的,不是规范误差。
- **前沿对标**:2025-2026 治理型 agent memory 关键维度 = provenance + confidence + staleness + audit trails(Atlan/Mem0/OvalEdge/PMC-NIH 多源)。Hyperion 的 KnowledgeItem **天生带这些字段**(bi-temporal `valid_at`/`invalid_at` + `source_tier`/`evidence`/`commit_sha` + `access_count` + `superseded_by`)—— 功能 2 把这套字段最擅长的事(可审计知识库)做成可见。Mem0/Cognee 没这种「带溯源的团队记忆体检」(调研坐实)。
- **YAGNI**:落「②记忆」,纯读(体检只看+建议,不改库)。✅ **差异化,不追平。**
- **形态**:`memory_dump` 工具(薄,接 `svc.list_items` + `_render_audit_card` 渲染)+ `memory-health-check` skill 包装(镜像 onboarding/compare read-only,但更严:连记忆库都只读不写;体检默认不 memorize,仅发现未决矛盾才记一条「需裁决」)。


### [ ] 📋 功能 3:记忆图边强化(对标 Graphiti/Cognee 图原生 · 长期低优)

- **现状**:`related` 字段存在,但连线只靠 evidence 文件交集([memorize.py:61-77](../../src/hyperion/services/memory/backends/native/memorize.py),浅)。Graphiti(双时态 KG)/ Cognee(图原生,79% BEAM)在关联推理上强。
- **做法**:**别现在做** —— 标 backlog,等记忆条数上去 + 关联查询需求出现再触发。
- **触发条件**:功能 2(记忆体检)落地后,用户要查「X 和 Y 在记忆里怎么关联」时。
- **前置**:**先做建议 A(ANN)**,这是最容易过度设计的地方,先盯硬刚需。
- **⚠️ 落地前补核**:Cognee 1.0 79% BEAM、Graphiti 双时态 KG 的原始论文/技术报告(WebFetch 核验标题+数字)。

---

## 七、一句话总结

> **Harness 定位转型对、Select 策略强(前沿水准)、记忆实现扎实(超 Mem0 的 4 路召回);三个真短板是「向量无 ANN」「摘要触发不 token 感知」「主战场 bug_rca 不挂预算中间件」。功能上别碰已砍 R4/R5;优先补 ANN + 记忆巩固自转(生产级硬刚需);skill 矩阵补「onboarding 导览」+「记忆体检」两块调研型空白(差异化,不追平)。**

---

## 附录:完成日志(挨个做时记这)

> 五/六两节 7 项全 `[x]`(功能 3 图边强化标 `[ ]` 长期低优,不在本轮)。commit 日期取 git 提交日(2026-08-13);memory 侧旁出的 correction-link + e2e 验证一并记(非 review 条目但同轮收尾)。

| 日期 | 项目 | commit | 备注 |
|---|---|---|---|
| 2026-08-13 | 建议 A:记忆向量 sqlite-vec ANN | `1ea8153` | 渐进式双路径(count>500 KNN 否则 loop);5 单测 + 全记忆 42 绿 |
| 2026-08-13 | 建议 B:摘要 token 触发 | `505a6a6` | `trigger=("tokens",32000)`;实测 fraction 排除(三模型 profile=None 构造 raise);2 单测 + runtime 26 绿 |
| 2026-08-13 | 建议 C:wrap_model_call 历史兜底 | `505a6a6` | 补 `wrap_model_call` 钩子(校正「二次压缩」误判);3 单测 + runtime 29 绿 |
| 2026-08-13 | 建议 D:记忆巩固自转 | `505a6a6` | recall 命中 memory 路 fire-and-forget consolidate(更正「无人调」误判:CLI 早 wire + recall 早 bump);3 单测 + 记忆 37 绿 |
| 2026-08-13 | 功能 1:onboarding 架构导览 skill | `071acca` | 第 14 MCP 工具 `repo_overview`(原「0 工具」spec-drift 修正);2 单测 + mcp_tools 25 绿;e2e 真机全绿(11 步/26 工具) |
| 2026-08-13 | 功能 2:记忆体检 skill | `942474f` | 第 15 MCP 工具 `memory_dump`(包已有契约的 list_items);3 单测 + mcp_tools 28 绿;e2e 真机全绿(审 wpa 48 条,抓真未决矛盾) |
| 2026-08-13 | (旁出)correction-link 纠正关系闭环 | `4b5dca0` | memory-health e2e 暴露两派打架根因 → 补 `corrects`/`corrected_by` 双字段;2+1 单测 |
| 2026-08-13 | (旁出)correction-link 2 连锁 bug 修复 | `5fe2a99` | e2e 抓 bug(id 不渲染 + 前缀不匹配);真 DB 重放 corrected_by 0→4;50 测绿 |
