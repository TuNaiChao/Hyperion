# 记忆与持续学习核心 — 设计文档(P3,差异化)

> 状态:**R1 已实现(2026-07-29)**· 设计稿 v1(2026-07-28)· 退出标准见 §9(全绿)
> 上位文档:[architecture.md §5.2](architecture.md) · 参考实现:deer-flow MemoryManager、oh-my-pi mnemopi、OpenHands memory

---

## 0. 这是什么 / 为什么是特色(面向小白)

**类比:Hyperion 的"长期笔记本"。**

普通 coding agent(claude code / opencode)每开一个新会话都是"失忆"的——上次定位过的 bug、上次分析过的代码库结构,它全忘了,得从头读日志、读代码,费 token 又费时间。Hyperion 的记忆核心就是给它装一本**跨会话、跨人共享的笔记本**:

- **P1 调研**产出的"这个库长啥样、关键模块怎么实现"→ 记下来(带源码 commit SHA 溯源)。
- **P2 bug-RCA** 产出的"这个 bug 根因是啥、怎么修的"→ 记下来,并连到相关的代码库知识。
- 下一次任何人遇到类似问题 → 先翻笔记本("这模式之前见过,这是当时的修法")→ 省推导、省 token。

**为什么是"持续学习"而不只是"存储":** 笔记本会**去重、衰减、把反复出现的教训升级成稳定规则**(像人把短期记忆巩固成长期记忆)。否则就是越攒越乱的垃圾堆。这部分设计借自 oh-my-pi 的 mnemopi(见 §7)。

**与检索的分工(别混淆):** 记忆核心**复用**已有的两个检索引擎当后端——`code_index`(按"意思"语义找)和 `code-review-graph`(按"结构/调用关系"找)。记忆核心自己**不建第三个检索库**,它是在两者之上的"知识层 + 持续学习逻辑"。

---

## 1. 契约:`MemoryService` ABC(照搬 deer-flow 形状)

> 直接对应 deer-flow [agents/memory/manager.py:62](../../deer-flow/backend/packages/harness/deerflow/agents/memory/manager.py) 的 `MemoryManager(BaseModel)`。

```python
# src/hyperion/services/memory/manager.py(R1 实现,本文件展示在窗口由用户手敲)
class MemoryService(BaseModel, abc.ABC):
    """记忆核心契约:tier-1 抽象方法,tier-2 带默认实现的管理方法,tier-3 可选钩子。"""

    # tier-1:所有后端必须实现
    @abc.abstractmethod
    async def memorize(self, items: list[KnowledgeItem], scope: Scope) -> int: ...
    @abc.abstractmethod
    async def recall(self, query: str, scope: Scope, top_k: int = 5) -> list[RecallHit]: ...

    # tier-2:管理(带默认实现,后端可覆盖)
    async def search(self, query, scope, **kw): ...      # 默认走 recall
    async def get(self, item_id, scope): ...

    # tier-3:可选钩子(后端不支持就 raise NotImplementedError)
    async def consolidate(self, scope): ...              # 巩固/去重/衰减
    async def invalidate(self, item_id, scope): ...      # 显式失效(如补丁已合入)
```

- **`Scope`** = `(owner, codebase)`——每个"谁管的哪个库"一个独立记忆空间(租户隔离,R4 多人时生效;v1 单 owner)。
- **单例工厂** `get_memory_service()`(double-checked lock),对应 deer-flow `get_memory_manager()`。

---

## 2. 后端可换(照搬 oh-my-pi backend-swap 形状)

> 对应 oh-my-pi [memory-backend/types.ts:14](../../oh-my-pi/packages/coding-agent/src/memory-backend/types.ts#L14) + [resolve.ts:23](../../oh-my-pi/packages/coding-agent/src/memory-backend/resolve.ts)。

```
src/hyperion/services/memory/
├── manager.py          # MemoryService ABC + get_memory_service()
├── schema.py           # KnowledgeItem / RecallHit / Scope(下 §3)
├── backends/
│   ├── native/         # ★ v1 后端:组合 code_index + code-review-graph
│   ├── mem0/           # 备选:mem0(mem0ai)
│   └── cognee/         # 备选:cognee(graph+vector)
├── recall.py           # 读路径编排(§4)
├── memorize.py         # 写路径编排(§5)
└── consolidate.py      # 巩固(§6,借 mnemopi)
```

**切换 = 丢一个 `backends/<name>/` 文件夹(暴露 `BACKEND_CLASS`)+ 配置 `memory.backend: <name>`**。v1 只实现 `native`,`mem0`/`cognee` 留接口位(需要时接入,零锁死)。

**为什么 v1 不直接用 cognee/mem0(用户决策):** Hyperion 已有 code_index(语义)+ 将接入 code-review-graph(结构)两个引擎,cognee 会是第三套重叠检索栈;且持续学习闭环(差异化)要自己握住。cognee/mem0 作为**可切换备选后端**保留,需要时一行配置换上。详见 architecture.md §1 D4。

---

## 3. 知识项 schema(三类 kind 共用一个 `KnowledgeItem` 类)

> ⚠️ 下方为设计示意;**实际以 `src/hyperion/services/memory/schema.py` 为准**(已演化:无子类,三类 `kind` 共用 `KnowledgeItem`,按 kind 填不同字段;另有 `source_tier`/`access_count`/`superseded_by`/`active` 等持续学习字段)。

```python
# src/hyperion/services/memory/schema.py(示意,完整字段见源码)
class KnowledgeItem(BaseModel):
    id: str               # sha256(f"{owner}/{codebase}:{kind}:{content_key}")[:16] —— 稳定 id:
                          #   同根因重复 memorize → 同 id → 走"合并/加权"而非"新增"(持续学习基础)
    kind: Literal["codebase_fact", "bug_lesson", "mental_model"]  # 三类共用此类,无子类
    repo: str
    scope: Scope          # (owner, codebase)
    summary: str          # 人读摘要(检索 + 注入用,核心字段)
    # —— 按 kind 填的可选展开字段 ——
    detail: str
    root_cause: str       # bug_lesson 专用
    fix_patch: str        # bug_lesson:补丁文本/引用
    blast_radius_files: list[str]
    kind_detail: Literal["module", "symbol", "architecture"]  # codebase_fact 专用
    # —— 溯源 + 证据 ——
    commit_sha: str | None    # ★ 溯源到 commit(记忆"保质期"锚点)
    evidence: list[Evidence]  # [file:line + 原文片段]
    source: str
    source_tier: SourceTier   # 可信度档(stated/delegate/inferred/imported/tool,合并时加权)
    # —— 持续学习信号 ——
    confidence: float         # Bayes 累加(初始 = tier_weight · 0.5)
    access_count: int         # 被召回次数(≥N 升级 mental_model)
    valid_at / invalid_at / superseded_by  # bi-temporal:矛盾时"失效"而非"删除"
    embedding: list[float] | None  # native 后端写入时算
```

- **`codebase_fact`**(P1 调研):`kind_detail` 记录"这个模块/符号/架构是干啥的、关键设计"。**2026-07-31 增强(RepoGraph 实证)**:结构化事实 > 裸 chunk —— 应带**模块职责 + 公开签名 + 调用边**(从 code-review-graph 抽),而非仅 prose `detail` + 裸 `snippet`(schema 待扩字段)。
- **`bug_lesson`**(P2 bug-RCA):`root_cause`/`fix_patch`/`blast_radius_files`。
- **`mental_model`**:被召回 ≥N 次的教训巩固升级出的"稳定规则"(借 Letta 3+ 规则)。
- **整份报告**:作为可检索 **document** 整体另存一份(便于"翻回原报告"),同时抽取其中的知识项(便于精确召回)。

---

## 4. Recall(读路径,工作流入口)

```python
# 多路召回 → 融合 → rerank → 只取 top-k → 注入(每条带 溯源+置信度+时间戳)
async def recall(query, scope, top_k=5) -> list[RecallHit]:
    sem   = await code_index.retrieve(query, repo=scope.codebase, top_k=20)   # 语义
    struct = await code_review_graph.blast_radius(query, repo=...)            # 结构
    facts = await native_store.search(query, scope, top_k=20)                 # 已存知识项
    fused = rrf([sem, struct, facts])                                         # 倒排融合
    final = await reranker.rerank(query, fused, top_k=top_k)                  # qwen3-rerank
    return final   # 注入提示词:每条 {summary, evidence[file:line], confidence, valid_at, source}
```

注入规则:`score × confidence × recency_weight` 排序,只取 top-3~5,**每条带溯源 + 时间戳**,让模型知道可信度与时效(对应 v0.1 §7.1)。

---

## 5. Memorize(写路径,工作流出口)

```python
# 抽知识项 → 实体消歧 → 冲突合并 → 设置信度 → 入库 + provenance
async def memorize(report, scope):
    items = await extractor.extract(report)            # LLM 按 schema 抽 CodebaseFact/BugLesson
    for it in items:
        it.related = await link_to_existing(it, scope) # 连图边(同模块/同符号/历史 bug)
        it.confidence = llm_score(it) * source_weight(report)
    merged = await merge_conflicts(items, scope)       # recency-wins + 显式失效(不删除旧的)
    await native_store.upsert(merged, scope)
```

**关键原则(沿用 v0.1):** write-time 严格过滤(只存根因/模式/规则,**不存日志流水**)——脏数据进索引后 rerank 也救不回来;冲突**显式失效**而非并存(避免两条矛盾事实都进 top-k);每条保留到原始报告的引用,可追溯。

---

## 6. 巩固 / 持续学习(借 mnemopi,差异化关键)

> 对应 oh-my-pi mnemopi [packages/mnemopi/src/core/beam/consolidate.ts](../../oh-my-pi/packages/mnemopi/src/core/beam/) + `veracity-consolidation.ts`。

**巩固(`consolidate(scope)`,后台/手动触发):**
- **去重**:同一根因的多个 `BugLesson` 合并成一个 + 累加置信度。
- **衰减(Weibull)**:长期没被召回命中的知识项降权(不删除)。
- **升级稳定事实**:反复出现(≥N 次)的教训 → 升级为"mental model"稳定规则(类似 oh-my-pi hindsight 的 `mental-models`)。
- **失效**:补丁已在上游合入 → 对应 `BugLesson` 标 `invalidated`(graphiti bi-temporal 思路)。

**OpenHands 3 层记忆(借鉴架构):** [openhands/memory/](https://github.com/OpenHands/openhands) `Condenser → View → ConversationMemory` + EventStream append-log + `.openhands/microagents/` 仓库级知识——native 后端的"工作记忆/情景记忆/语义记忆"分层参考它。v1 先做语义(`CodebaseFact`/`BugLesson`),工作/情景记忆随 workflow state 走,不另建。

---

## 6.5 文档摄取与学习(R3.4 · P3 扩充)

> 代码:`src/hyperion/services/memory/ingest.py` + CLI `hyperion memory ingest`。

**干什么(面向小白)**:§5 的 `memorize` 吃的是「workflow 内部产出的报告」;ingest 把**外部文档**也吃进来——团队历史 bug 报告、调研报告、上游已合入的补丁——沉淀成可召回、带溯源的记忆。三类入口按扩展名分流,最后都汇到 `svc.memorize`(去重/合并/关联已在 native 后端就位,**零新存储**):

```
hyperion memory ingest <path> [--kind auto|report|patch] [--source-tier imported]
 ├─ .md/.txt/.pdf → parse_issue(复用 trigger_parser)→ LongDocChunker(切节)→ 每块 svc.memorize_report(extract + memorize)
 └─ .patch/.diff  → PatchIngestPipeline:解 hunk → retrieve 周围代码 → LLM 抽 root_cause/intent → 组装 bug_lesson → svc.memorize
```

**为什么不自造存储(调研定论)**:mem0/cognee/Zep/Letta 2026 格局 + mnemopi `veracity-consolidation.ts` 逐条核实——Hyperion 已走的结构化 KnowledgeItem 路线 = mnemopi Memory 式(bayesian 合并 step=0.3 / source_tier 权重 / 确定性 sha256 ID / bi-temporal + superseded_by 全就位)。Cognee 的 Extract→Cognify→Load 是**从零造整条管线**;Hyperion 复用已有 extract/memorize/retrieval,只补三块拼图:① 文档入口 ② 长文分块 ③ 补丁 retrieve-then-summarize。deer-flow/mnemopi **都没有**文档摄入/补丁理解(记忆源仅限对话消息)——这块是 Hyperion 新东西,但建在已对齐 mnemopi 的原语上。

**补丁为什么要 retrieve-then-summarize**:裸 diff 缺周围代码上下文,LLM 难判根因/意图;先 `code_index.retrieve` 取被改符号周围代码再喂 LLM。依据(均 WebFetch 核验):[PATCH(ACM 2025)](https://dl.acm.org/doi/full/10.1145/3718739) / [SpecRover(ICSE 2025)](https://arxiv.org/abs/2408.02232) / [What-Do-They-Fix(NDSS 2026)](https://www.ndss-symposium.org/wp-content/uploads/2026-s328-paper.pdf)。(计划旧稿曾误引「Codeant/ICSE2026 arXiv:2503.15223」——该 id 实为 SWE-bench correctness 论文,Codeant 是商业产品非论文,已订正。)

**五个设计决策(调研实锤,落地遵循)**:

1. **不新增 kind**:补丁产出的教训用现有 `bug_lesson`(本就有 `symptom/root_cause/fix_patch/blast_radius_files`,schema.py:144)+ `tags=["patch_insight"]` + `source_tier=SourceTier.imported`(枚举里本就有,schema.py:50)。加新 kind 要动 extract prompt / `_same_subject` / consolidate / FTS,牵动大不值。
2. **PDF/md/txt loader 复用**:`trigger_parser.parse_issue`(parser.py:25,pypdf 已在 uv.lock)通用,直接 import,**不重造** DocumentLoader。
3. **CLI `memory ingest` = `memory add --from-report` 的泛化**:后者(cli.py:203)已支持「读一份 .md → memorize_report」;ingest 把它扩成「任意路径、按扩展名分流」,report 路复用同一条 `svc.memorize_report`。
4. **补丁路 retrieve 降级**:`code_index.retrieve` 在 repo 未索引时自动返空 → PatchIngestPipeline 降级为「只喂 diff」(不阻塞),同 Verifier 降级哲学(不误杀、不硬挡)。
5. **同 bug 报告 + 补丁不硬并**:根因叙事 vs 修复实现粒度不同 → 保持两条独立 KI,靠 `related`(evidence 文件交集,memorize.py:61 自动填)关联;冲突走 bi-temporal `superseded_by`。

**维护参数不改**:`recall halflife=180 天`、`bayesian step=0.3`、`source_tier 权重`(stated 1.0 / inferred 0.7 / imported 0.6 / tool 0.5)与 mnemopi 校准值对齐,**ingest 侧不调这些**。

**复用清单(别重造,均已 file:line 核实)**:
- 写入:`svc.memorize_report`(manager.py:65,report 路)+ `svc.memorize`(manager.py:45,patch 路)。
- 抽取:`extract_items`(extract.py:136,DeepSeek-safe `_extract_json_object`)+ `_EXTRACTION_PROMPT` 模板。
- 检索(取周围代码):`code_index.retrieval.retrieve`(retrieval.py:236)→ `RetrievalHit{file,start_line,end_line,text}`。
- hunk 行号防御解析:bug_rca `_coerce_evidence_line`(nodes.py:311);evidence 片段渲染:`_render_evidence_snippets`(nodes.py:162)。
- 去重/合并/关联:`make_id`(sha256,schema.py:113)+ `_bayesian_update`(memorize.py:33)+ `_link_related`(memorize.py:61),全在 native 后端,ingest 侧零代码。

**R3.4 交付**:① ingest.py(分发器 + LongDocChunker + PatchIngestPipeline)② CLI `memory ingest` ③ 本章节 ④ 测试(report 路离线单测 + patch 路 monkeypatch 接线 + e2e 三类文档 ingest→recall 命中 + 去重合并)。

---

## 7. 接入方式(deer-flow 双模式)

对应 deer-flow [middlewares/memory_middleware.py:30](../../deer-flow/backend/packages/harness/deerflow/agents/middlewares/memory_middleware.py)(自动注入)+ [agents/memory/tools.py:60](../../deer-flow/backend/packages/harness/deerflow/agents/memory/tools.py)(工具自管):

- **(a) workflow 自动召回/沉淀**:bug-RCA/deep-research workflow 的首尾节点调 `recall`/`memorize`,用户无感。
- **(b) agent 工具** `memory_recall` / `memory_memorize`:agent 主动查/写(条件实例化——只在 memory 后端就绪时挂载,借 oh-my-pi `MemoryRecallTool.createIf`)。
- **(c) MCP server**(★ 2026-07-31 起主路径):把 memory + code_index(`hyperion_search_codebase`)+ code-review-graph + `hyperion_filter_logs`(日志过滤)暴露给 **delegate**(opencode)现场查 —— delegate(opencode)自主定位/修复时,经 MCP 调这些工具(**不是** Hyperion 预筛喂它),见 [bug-rca-design.md §6](bug-rca-design.md)。

---

## 8. 多代码库 / 团队(数据模型现在定,v1 单机,R4 多人)

- **scope = (owner, codebase)**:native 后端 = **SQLite + FTS5**(知识项主表 + 全文检索)+ 向量 blob(复用 code_index embedder);知识项 `owner` 字段做租户隔离。v1 单 owner,R4 多人。
- **文档统一管理**:RCA/调研报告按 `(owner, codebase, report_id)` 列/查/版本。
- v1 单 owner,R4 再做多 owner 并发隔离与权限。

---

## 9. R1 退出标准(可验证)— ✅ 2026-07-29 全绿

1. ✅ `MemoryService` ABC + `native` 后端可 `memorize`/`recall`(组合 code_index;CRG 结构路代码就绪、默认 `none`,装 extra 启用)。
2. ✅ 报告→KI→语义召回:DeepSeek 抽取 + DashScope 向量+BM25+rerank 全链验通(`hyperion memory add --from-report` + `recall`,8 个离线测试绿)。
3. ✅ MCP server 起来 + delegate 经 `tools/call memory_recall` 查到记忆(+ 自动带 code 路命中)。
4. ✅ CLI:`uv run hyperion memory recall/add/list/consolidate/invalidate` + `mcp serve`。

## 10. 待办(记 backlog)

- 巩固算法参数:去重(精确 content_key)已做;**Weibull 衰减未启用**(mnemopi 也未启用,生产跑 exp halflife);语义近邻去重(同根因不同措辞)需 embedding 聚类。
- graphiti bi-temporal 的 `valid_at/invalid_at` 完整实现(schema 已留位 + 软删已用)。
- 本地 ONNX 向量档(fastembed,免 API)。
- mem0/cognee 备选后端接入验证(需要时)。
- OpenHands 式工作/情景记忆分层(按需)。
- **CRG 结构路**:`native.structural=crg` 需 `uv sync --extra code-review-graph`(重依赖:tree-sitter-language-pack/networkx/igraph);wpa/bluez 的 C 图需 tree-sitter-c(R3)。R1 在小 Python 仓验(代码就绪,实测待跑)。
- **CJK BM25 分词**:unicode61 不切中文(纯中文查询靠向量路兜底);jieba / 逐字切分待加。
- **DashScope 端点**:专属 MaaS 端点时 `base_url` 走 `$DASHSCOPE_BASE_URL`/`$DASHSCOPE_RERANK_URL`(.env);未设回落 serverless 默认(create_embedder/create_reranker `or DEFAULT`)。
- **ingest(R3.4)pull-by-need**:① LongDocChunker 现为 markdown-header + 段落切分(纯 stdlib);遇乱序长文换 RecursiveCharacterTextSplitter。② PatchIngestPipeline 的 retrieve 用 `code_index.retrieve`(符号级语义);跨文件/头文件符号加 LSP go-to-def 兜底(multilspy)。③ `consolidate()` 周期任务口已留(manager.py:79),范本 mnemopi veracity-consolidation —— R3.4 末评估启用。
- **extract.py 逐条容错(R3.4 e2e 发现)**:`_ExtractionResult.model_validate(data)` 是**整批校验** —— LLM 偶把 `kind` 的值(`bug_lesson`)错塞进 `kind_detail`(只认 module/symbol/architecture,踩坑 #5 LLM schema 不守)→ 整批丢失、该次 ingest 写 0(降级不崩,report 重摄取时偶现)。改逐条 `_ExtractedItem.model_validate` + skip 坏条留好条更鲁棒(严格更优,不失更多)。补丁路不受影响(PatchIngestPipeline 自己组 KI,id 按 diff 内容算,见 §6.5)。
- **patch 语义近邻去重**:当前 id 按 diff 内容算(同 .patch 摄取两次 → 合并 ✓);不同但修同一 bug 的两个补丁的"语义近邻去重"需 embedding 聚类,记 backlog(与上面的"语义近邻去重"同属一类难题)。
