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

- **`codebase_fact`**(P1 调研):`kind_detail` 记录"这个模块/符号/架构是干啥的、关键设计"。
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

## 7. 接入方式(deer-flow 双模式)

对应 deer-flow [middlewares/memory_middleware.py:30](../../deer-flow/backend/packages/harness/deerflow/agents/middlewares/memory_middleware.py)(自动注入)+ [agents/memory/tools.py:60](../../deer-flow/backend/packages/harness/deerflow/agents/memory/tools.py)(工具自管):

- **(a) workflow 自动召回/沉淀**:bug-RCA/deep-research workflow 的首尾节点调 `recall`/`memorize`,用户无感。
- **(b) agent 工具** `memory_recall` / `memory_memorize`:agent 主动查/写(条件实例化——只在 memory 后端就绪时挂载,借 oh-my-pi `MemoryRecallTool.createIf`)。
- **(c) MCP server**:把 memory(+ code_index + code-review-graph)暴露给 **delegate**(omp/opencode)现场查——delegate 干活时能翻 Hyperion 的笔记本。

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
