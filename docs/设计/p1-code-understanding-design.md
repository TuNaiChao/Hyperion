# P1 设计:代码理解服务(`services/code_index/`)

> **状态**:P1.0–P1.5 已成(P1.5 L2 精确导航 find_references/goto_definition/hover 活验证通过,2026-07-28)。本文档随进展更新。
> **v2 路线对齐(2026-07-28 产品重规划)**:本文的 P1.x 标签指**代码理解层**的子阶段(已成,作为资产保留)。在 v2 的 R0–R5 路线([architecture.md §8](architecture.md))里,这层 = 已建成的 **L1+L2**,兼作记忆核心 native 后端的**语义检索那条腿** + 调研/委托的上下文源([architecture.md §5.1](architecture.md))。文内其余 **"P2" 指代的 L3/DAP → 对齐到 R3+/R5,且仅可复现 bug 适用**(事后日志分析不适用,见 [bug-rca-design.md §7](bug-rca-design.md))。
> **2026-07-31 更新**:`retrieval`/`search_code` 现**双重暴露**:内部 `@tool`(`tools/code_nav.py`)**+ `hyperion_search_codebase` MCP 工具**(`hyperion mcp serve`)。后者是 opencode 委托自主定位的**主消费路径**(opencode 现场调,回真实符号带 provenance,防幻觉;见 [bug-rca-design.md §6](bug-rca-design.md))—— Hyperion 不再自跑 file→function→line 漏斗(已砍,与 opencode 重复)。
> **目标代码库**:bluez / wpa_supplicant 等 Linux C 组件(P1 以 Python/deer-flow 起步把管线跑通,C 专属难点留到 C 场景)。
> **总纲**:[architecture.md §5.1](architecture.md#51-代码理解服务-servicescode_index);**演进依据**:[后续设计演进报告(oh-my-pi 与最佳实践)](../调研/后续设计演进报告-oh-my-pi与最佳实践.md)。

---

## 0. 这层是干什么的(先讲大白话)

### 为什么需要它
平台层(platform/)给了 agent "能跑命令/读文件" 的通用能力,但**不懂代码结构**。bug-RCA(R2/R3)要"定位根因到具体函数",PR-Tracker(R4)要"评估一个 PR 的影响面"——两者都要求 agent 能像 IDE 一样在大型 C 代码库里**按符号导航 + 语义检索**,而不是 `grep` 全文。代码理解层就是这个共同地基。

### 三层代码智能栈(理解本服务的主线)

> **面向小白的类比——理解代码像查一栋大楼,分三层,叠加不替代:**

| 层 | 比喻 | 你问什么 | 对应 | 状态 |
|---|---|---|---|---|
| **L1 向量检索** | 大楼的"语义索引" | "找处理蓝牙断连的地方"(按**意思**模糊匹配) | `services/code_index/`(本文档) | **P1.0–P1.3 已成** |
| **L2 LSP/clangd** | 大楼的"精确导航"(像 IDE) | "**谁调用**了 `disconnect_cb`"(精确到每一处,连宏/跨文件/系统头) | P1.5 | **已成** |
| **L3 DAP/lldb·gdb** | 大楼的"现场勘查" | "进程崩在这,此刻这个变量值是多少、栈是什么" | R3+/R5 | 待做(仅可复现 bug) |

三层叠加:L1 先定位到大概哪个模块 → L2 精确串调用链 → L3 验证现场。**本服务当前覆盖 L1+L2(已成);L3 留 R3+/R5(仅可复现 bug 适用)。**

### 退出标准
- **P1.3(已成)**:L2(语义查询)recall@5 ≥ 0.55 —— 实测 **0.650 达标**(见 §3)。
- **P1.4(已成)**:agent 能用导航工具(grep_symbol/read_function/search_code/grep)+ read BFS 摘要 + 二进制守卫 —— 6/6 验证通过(见 §4.9)。
- **P1.5(已成)**:精确 caller/callee 经 LSP 可查 —— fixture 实测 `find_references` 零漏召(见 §5.10)。

### 选型一览(P1 基础全部建在外部 SOTA 上)
| 能力 | 选型 |
|---|---|
| 解析 | tree-sitter + 每语言独立 grammar 包(`tree-sitter-python`;**C 已加**,R2 为 wpa 补);宏走 clangd 展开(P1.5),ctags 备选未接 |
| 切块 | 符号边界(对标 cAST,EMNLP 2025) |
| 向量库 | **LanceDB**(嵌入式,原生 BM25+向量+RRF 混合);`VectorStore` 接口留 Qdrant 扩展口子 |
| embedding | **DashScope `text-embedding-v4`**(远端默认)/ 本地可选 |
| 重排 | **DashScope `qwen3-rerank`**(远端默认)/ SiliconFlow 免费 / 本地 GPU |
| 融合 | RRF(k=60,Cormack 2009 标准) |
| 精确导航(P1.5) | **clangd 经 multilspy**(取代原 code_graph 自建调用图) |

---

## 1. 总体架构(L1 已成 + L2/L3 待接)

```
                  ┌─────────────────── 代码理解服务 services/code_index/ ───────────────────┐
                  │                                                                    │
   仓库路径 ──▶ ① 解析 ──▶ ② 切块 ──▶ ③ 索引 ──▶ ④ 检索 ──▶ ⑤ 导航工具(ACI)      │
              tree-sitter   符号边界     embedding   BM25+向量    grep_symbol          │
              + ctags(C)    chunk+元数据 + LanceDB   +RRF+rerank  read_function        │
                                                                          search_code         │
                  │                                                                    │
                  └────────────────────────────────────────────────────────────────────└── L2: LSP/clangd(P1.5)
                                     ▲ build/增量更新(§2.6)                            └── L3: DAP(P2)
```

四条数据流:
1. **建索引**(离线/低频):walk 仓库 → 解析 → 切块 → embedding → 存 LanceDB。
2. **检索 L1**(高频):query → BM25+向量 → RRF → reranker → top-k chunk。
3. **导航**(高频):符号名 → 定位定义/读函数体;caller/callee 经 L2 LSP(P1.5)。
4. **L2/L3**(P1.5/P2):LSP 精确导航 / DAP 现场勘查,叠在 L1 之上。

---

## 2. L1 已实现的管线(P1.0–P1.3)

### 2.1 解析层 `parser.py` [P1.0 已成]

> **面向小白**:想象要让 agent 看懂几十万行代码。第一步是把代码拆成一张张"符号卡片"——每个函数/类各一张,记着名字、属于哪个类、定义在第几行到第几行、参数签名。后面的切块、向量化、检索都建在这些卡片上。

- **tree-sitter 容错解析**:给它源码返回语法树(AST),我们从中摘出 function/class 符号。容错 = 代码有错也尽量解析(C 项目宏多、缺头文件,严格解析器会大面积失败)。
- **零联网**:每门语言用独立 grammar 包(`tree-sitter-python`,grammar 编进 wheel);**不用** `tree-sitter-language-pack` 1.x(运行时从 GitHub 下载语法,国内网络 timeout 不可用)。
- **多语言靠注册表,不改核心**:`GRAMMARS` 是张表(语言 → 节点类型/字段名);加 C = 加一条 + `uv add tree-sitter-c`。
- **同时解析 `.h` 头文件**:C 的 struct/typedef/宏大量在头文件,必须一并索引。

**对外接口**:
```python
@dataclass(frozen=True)
class Symbol:
    name: str                 # 简单名,如 "run"
    qualified_name: str       # 带作用域,如 "Agent.run"(消歧 / LSP 定位用)
    kind: str                 # function | method | class(C 当前仅 function;struct/macro/typedef/enum 待补——按 body 过滤 struct_specifier 噪声)
    language: str             # python | c …
    file: str                 # 相对仓根路径(parse_repo 给相对路径,索引稳定)
    start_line: int           # 1-indexed
    end_line: int             # 1-indexed
    signature: str | None     # 形参文本,如 "(self, question)";class 为 None
    docstring: str | None     # 函数/类的 docstring(去引号纯文本);parser 同一遍 DFS 抽

def parse_file(path, language=None) -> list[Symbol]: ...     # language=None 按后缀推断
def parse_repo(root, languages=None) -> list[Symbol]: ...    # 递归解析全仓;跳过 _SKIP_DIRS
def iter_source_files(root, languages=None): ...             # yield (绝对路径, 相对路径, 语言);与 chunker 共用,保证覆盖无符号文件
```

### 2.2 切块层 `chunker.py` [P1.1 已成]

> **面向小白**:卡片还得变成 **chunk**(检索/嵌入/排序的基本单位)。一个 chunk = 一块代码 + 它的检索元数据。研究(cAST,EMNLP 2025)证明按**语法边界**(函数/类)切,比按固定行数切质量高——固定行数会把函数从中间劈开。所以每个符号各成一个 chunk。

**关键设计**:
- **符号边界切**:每个 `function_definition` / `class` 一个 chunk;**模块级兜底 chunk**(kind=`module`)把每个文件里不在任何函数/类的代码(import/全局常量/`if __name__`)聚起来,**覆盖率 100%**(cAST 的 plug-and-play:所有 chunk 拼起来能还原原文件)。
- **`fts_text` 标识符拆词**(BM25 召回关键):同时处理 `snake_case`/`camelCase`/`SCREAMING_SNAKE`,如 `wpa_supplicant_assoc_cb` → `wpa supplicant assoc cb`、`hci_le_CisEstablished` → `hci le cis established`(光按 `_` 拆会漏 camelCase/宏)。纳入 docstring 自然语言词(高信号,加权重复)。
- **chunk 大小用非空白字符数**(`MAX_CHUNK_CHARS=20000`,学 cAST,省 tokenizer 依赖);超长仅整块保留,**超大符号 AST 子语句切分延后到 C 场景**([backlog #6](../../.claude/memory/backlog-production-grade.md))。
- **id 用 `qualified_name` 消歧**同类同名;**不含 start_line**(含行号对重构太敏感:重排函数顺序 → 全部 id 变 → 全量重嵌)。

**chunk schema**:
```python
@dataclass(frozen=True)
class CodeChunk:
    id: str               # 稳定主键 f"{file}:{qualified_name}"(超长分段加 ":p{N}");不含 start_line(决策 #8)
    symbol: str           # 限定名;module chunk 用文件相对路径
    kind: str             # function | method | class | module
    file: str
    language: str
    start_line: int
    end_line: int
    text: str             # 原始代码文本(read_function 直接用,无加工)
    content_hash: str     # text 的 sha256,增量更新按它判变(§2.6)
    fts_text: str         # 给 BM25 的词袋(标识符拆词 + docstring,小写空格分隔)
    part: int = 1         # 超长分段段号,预留
    total: int = 1        # 总段数,预留(切分延后,backlog #6)
    # 注:callers/callees 字段在代码里仍保留(默认空 tuple),但 P1.5 改用 LSP 后不再回填——
    # caller/callee 由 LSP 在查询时给(§5),不存进 chunk。无害,可在 P1.4 清理时移除。
```

### 2.3 嵌入层 `embed.py` [P1.2 已成]

> **面向小白**:检索光靠"词匹配"(BM25)不够——你问"蓝牙断连处理",但函数叫 `disconnect_cb`,词对不上。embedding 把每块代码变成一个"语义向量"(一串数字),意思相近的代码向量也相近,这样能按**意思**召回。

- **provider 抽象**:`Embedder` Protocol;**远端默认**(DashScope `text-embedding-v4` = Qwen3-Embedding 全血版,免下载/免 torch)/ **本地可选**(`sentence_transformers`,如 Qwen3-0.6B / bge-m3,需 `uv sync --extra embedding-local`)。
- **chunk_expansion 元数据头**:嵌入时在代码前拼一行语言对应注释(`# file: src/adapter.c · function: disconnect_cb · lang: c`)再嵌——注释是代码的一部分(模型训练分布内),缓解 document(带头)/query(自然语言不加)的不对称。query 端不加。
- **铁律:embedding 模型选定后不能换——换要全量重嵌**。`index.py` 存模型指纹,与 LanceDB 表元数据比对,变更触发重建。

**实现陷阱(P1.2 已处理)**:`max_seq_length` 默认静默截断(须显式设 + 进指纹);query instruction(远端模型 query 端加,prompt_name 或拼串,document 端不加);`normalize_embeddings=True`;维度动态取(`get_sentence_embedding_dimension()`);批编码(sentence-transformers 原生动态 padding);HF 国内下载走 `hf-mirror.com`。本地 Embedder 完整三态加载+冷却自愈 + ONNX int8 提速见 [backlog #8/#9](../../.claude/memory/backlog-production-grade.md)。

### 2.4 存储层 `store.py` [P1.2 已成]

> **面向小白**:得有个地方存这些"代码块 + 向量",还能快速按向量/关键词查。选 **LanceDB**:嵌入式(进程内、零 server、表就是文件目录,随 git/rsync 跨机),原生支持 BM25+向量混合检索。每仓库一张表(table-per-repo),物理隔离。

**LanceDB 0.34 真实 API(已 live 核实)**:
- **schema(pyarrow,生产优于 pydantic LanceModel)**:id/symbol/kind/file/language/start_line/end_line/text/content_hash/fts_text(string)/vector(`pa.list_(pa.float32(), dim)`,**维度建表时定死**,改维度=新建表迁移)。
- **embedded 连接**:`lancedb.connect("data/code_index/<repo>/lancedb")`,每仓库一张表。
- **建表后立即建两个索引**:
  - `create_index("id", config=BTree())` —— **merge_insert 必须**,否则撞 "unindexed rows > 10000"。
  - `create_index("fts_text", config=FTS(stem=False, remove_stop_words=False, with_position=True))` —— **代码场景关键参数**:`stem=False`(否则 malloc 被 stem)、`remove_stop_words=False`(否则 `int`/`void`/`public`/`return` 被当停用词删!)、`with_position=True`。
  - (新 API:`create_scalar_index`/`create_fts_index` 已在 0.25+ 弃用。)
- **metric**:向量已 L2 归一化 → 用 `dot`(最快);建 IVF 时 metric 必须建索引时定。
- **upsert**:`merge_insert("id").when_matched_update_all(where="target.content_hash <> source.content_hash").when_not_matched_insert_all().execute(...)` —— 条件更新,只重写 content_hash 变了的行(增量利器)。
- **写后 `tbl.optimize()`**:把新行折叠进 FTS/向量索引,否则新行走 flat scan 慢路径。
- **`VectorStore` Protocol**(留 Qdrant 扩展口子,P1 不实现 QdrantStore):`upsert` / `hybrid_search`。

### 2.5 检索层 `retrieval.py` [P1.3 已成]

> **面向小白**:用户给一句查询,这层分两步:① **召回**——BM25(关键词精确)+向量(语义)两路各找一批,用 **RRF** 融合成 top-50 候选;② **重排**——cross-encoder(reranker)把候选按真实相关性精排到 top-5。两阶段(bi-encoder 召回 + cross-encoder 精排)是十年工业标准,reranker 典型增益 +20~48%。

```python
# 真实 LanceDB 0.34 API(无 hybrid_search()/match_text(),均已弃用)
from lancedb.rerankers import RRFReranker
res = (tbl.search(query_type="hybrid", vector_column_name="vector", fts_columns="fts_text")
         .vector(qvec)            # 已算好的稠密向量(L2 归一化)
         .text(query)             # FTS 查询串
         .limit(candidate_top_n)  # 候选池,默认 50
         .rerank(reranker)        # RRF(k=60)融合;或自定义 Reranker 子类调远端 reranker
         .limit(final_top_k)      # 最终,默认 5
         .to_list())
```

- **为什么必须混合**:dense 和 sparse 失败模式正交——改名 dense 强、错误码/宏名 BM25 强,单路都漏。
- **RRF**:`RRFReranker(K=60)` 是 hybrid 默认,客户端算。k=60 是 Cormack 2009 原始论文标准。
- **reranker provider 抽象(镜像 embed.py)**:`Reranker` Protocol;**远端默认** DashScope `qwen3-rerank`(原生端点,同 `DASHSCOPE_API_KEY`,¥0.0005/千token),SiliconFlow `BAAI/bge-reranker-v2-m3` 免费 fallback(Cohere 形态),本地仅 GPU(CPU 257s/100doc 不可交互)。用 LanceDB `Reranker` 子类(重写 `rerank_hybrid`)把远端 API 包进 `.rerank()` 接口。reranker 对 **fts_text**(短)打分,不对全长代码体。
- **`_out_mode` 可观测**:每查询记录实际走了 hybrid+rerank / hybrid / rerank-failed:hybrid / empty。
- **三级降级**:hybrid(FTS+向量)→ 仅 FTS → 仅向量;reranker 失败时降级用 hybrid 顺序。
- ⚠️ **weakest-link**:RRF 不是越多路越好([Balancing the Blend, arXiv 2508.01405](https://arxiv.org/html/2508.01405v2));加第 3 路前必须单路 eval 消融,弱路径砍掉。TRF(ColBERT MaxSim 当融合器)作 R3+ 精度升级([backlog #10](../../.claude/memory/backlog-production-grade.md))。

### 2.6 建索引编排 `index.py` [P1.2 已成]

> **面向小白**:把上面几步串起来建库:walk 仓库 → 解析 → 切块 → 批量 embedding → 存 LanceDB。关键是怎么**不写坏**(原子性)和**只更新变化的部分**(增量)。

**原子性(按存储引擎分两条路)**:
- **LanceDB 向量库(无事务)**:**全量重建**先写 temp 目录 `data/code_index/<repo>/lancedb_tmp`,全部成功后 `os.rename` 原子切成 `lancedb`(同文件系统 rename 是原子 syscall),失败丢弃 tmp——要么旧索引、要么新索引,无半成品。**增量**用 `merge_insert` 条件 upsert(content_hash 不变跳过),消失路径用 `when_not_matched_by_source_delete` 清孤儿。
- **SQLite(若未来用,有事务)**:连接三件套 `isolation_level=None` + `journal_mode=WAL` + `busy_timeout=5000`;每文件/批一个 `BEGIN IMMEDIATE` 事务(删旧+插新原子)。借鉴 CRG graph.py(9 版本验证)。

**`index_manifest.json` sidecar**(建库成功后落盘,检索前校验):`repo_commit`(建库锁定的 commit)/ `model_fingerprint`(`model+dim+normalize`,变→全量重建)/ `schema_version`/ `file_manifest`(`{相对路径: content_hash}`,增量对账)。

**增量加速**:用 `git diff --name-only` 定位变化文件 + content_hash 短路(非 git 仓库回退 walk + file_manifest 对账);并行 parse 串行写(ProcessPoolExecutor,tree-sitter 释放 GIL;LanceDB 单写者;多进程 `spawn` 非 `fork`)。重命名/移动:对账时消失路径删、新增嵌、hash 变重嵌。

---

## 3. L1 评测 `eval/` [P1.3 已成]

> **面向小白**:怎么知道检索好不好?造一批"题目"(query + 标准答案 gold),"查询 → 看前 5 个结果有没有命中答案",算 recall/precision/MRR。金标(答案)必须**独立于检索系统**(不能用检索自己的结果当答案,那是循环论证)。

**金标(独立)**:从 git 历史 `git log --grep="Fixes:"` 取 fix commit;**行级映射**(`git diff <parent> <fix>` 拿改动行 → 对子提交跑一次 parser → 改动行映射到包住它的最内层符号 = gold)。⚠️ 不对父子提交 diff 符号列表(只改函数体几行时父子符号集相同,抓不到)。此法独立于检索系统(SWE-bench/SweLoc 标准)。

**难度分层**:**L1 词汇**(query 含 gold 符号名/文件名)/ **L2 语义**(描述行为/日志,同模块内;**退出标准瞄准这档**)/ **L3 推理**(跨模块多跳,需 L2 LSP,P1.5 后评)。

**警示**:① **循环论证**(禁用图派生 caller 当金标评"检索能否找到 caller");② **SWE-Bench Illusion**(公开 issue 被 LLM 记忆,评测集须时间 cutoff + holdout)。

**指标(自己实现)**:Recall@k、Precision@k、多标签 MRR、nDCG@k(BEIR 标准)。

**退出标准(多指标,95% bootstrap CI)**:① L2 recall@5 ≥ 0.55(CI≥0.45)② precision@5 ≥ 0.40 ③ MRR ≥ 0.45 ④ L1 recall@5 ≥ 0.85(sanity)⑤ BM25 baseline L2 recall@5 ≤ 0.40 ⑥ holdout 衰减 ≤ 15pp。

### P1.3 实测结果(2026-07-27,Hyperion 自身代码首测)
评测集 `eval/sets/hyperion.jsonl`:人工 curate **18 条**(8 L1 + 10 L2;production 级 ≥150 + L3 + git-diff 自动抽取见 [backlog #13](../../.claude/memory/backlog-production-grade.md))。在 `src/hyperion`(201 chunk)上建索引(DashScope text-embedding-v4 + qwen3-rerank):

| tier | recall@5 | precision@5 | MRR | nDCG@5 | hit@5 |
|---|---|---|---|---|---|
| L1 (n=8) | **1.000** | 0.200 | 0.854 | 0.891 | 1.000 |
| L2 (n=10) | **0.650** | 0.240 | 0.483 | 0.496 | 0.800 |
| L2 无 reranker | 0.600 | 0.220 | 0.378 | 0.402 | 0.800 |

**裁定**:主标准 **L2 recall@5 = 0.650 ≥ 0.55 ✅ 达标**;L1 sanity 1.000 ✅;L2 MRR 0.483 ≥ 0.45 ✅。reranker 主要提升排序质量(L2 MRR +0.105、nDCG +0.094)。precision@5=0.240 未达 0.40 —— **指标定义问题非缺陷**:小 gold 集(1-2 符号)数学上封顶 ≈ |gold|/k,改 `precision@min(k,|gold|)`([backlog #16](../../.claude/memory/backlog-production-grade.md))。BM25 baseline(条件⑤)+ holdout(条件⑥)待补([backlog #14/#15](../../.claude/memory/backlog-production-grade.md))。

**结论**:P1.3 主退出标准达标,检索管线端到端跑通。诚实保留:18 条 indicative(非 ≥150 统计 tight),production 级评测是后续。

---

## 4. P1.4:导航工具 + 平台护栏

> **状态**:**已成**(2026-07-28)。3 新文件(`platform/sandbox/_search.py`、`services/code_index/outline.py`、`tools/code_nav.py`)+ 2 升级(`tools/sandbox.py` 的 read_file/grep、`platform/sandbox/local.py`)+ config.yaml 接入 + `hyperion index`/`hyperion tools` CLI。6/6 退出标准绿(§4.9)。
> **调研依据**:deer-flow `sandbox/search.py`(纯 Python 搜索内核)+ oh-my-pi `pi-ast/summary.rs`(tree-sitter BFS 摘要)+ `crates/pi-natives/grep.rs`(ripgrep 边界处理)。出处见 §4.10。

### 4.0 这步要干什么(先讲大白话)

> **面向小白的类比——给 agent 配一个"代码 IDE 的导航键盘":**

L1 检索(P1.3)目前只是后台一个**函数** `retrieve(query)`,agent 自己调不到。P1.4 把它和几个常用动作包成 agent 能直接调的**工具**,像 IDE 里那几个高频键:

| agent 想干的事 | IDE 里的键 | 本步给的工具 | 走哪一层 |
|---|---|---|---|
| "按名字找 `disconnect_cb` 定义在哪" | 转到符号(Cmd-Shift-O) | `grep_symbol` | tree-sitter 符号(parser) |
| "把这个函数完整读出来" | 选中函数体 | `read_function` | tree-sitter 符号(parser) |
| "找处理蓝牙断连的地方"(按**意思**) | 语义搜索 | `search_code` | L1 向量检索(P1.3) |
| "找所有 `DBG(` 调用"(按**字面正则**) | 全局搜索(Cmd-Shift-F) | `grep` | 文件系统正则扫描 |
| "看一眼这个大文件长啥样"(别 dump 全文) | 代码大纲(outline) | `read_file`(升级) | tree-sitter BFS 摘要 |

外加一道横切护栏:**二进制文件守卫**——读 `.o`/`.so`/图片这类文件会毁终端、烧 context,统一拦下。

> **关键增量**:deer-flow **没有**符号工具、**没有** tree-sitter、**没有** read 摘要(它的 grep 纯文本);oh-my-pi 有 tree-sitter BFS 摘要和 ast-grep,但是 Rust。本步把 deer-flow 的搜索内核(Python 可移植)+ omp 的 BFS 摘要思想(Python 重写,复用已有 parser)**结合**到 Hyperion——这是两家都没有的、Python 栈的完整导航层。

### 4.1 工具总览

| 工具 | 文件 | 一句话 | 退出标准 |
|---|---|---|---|
| `grep_symbol` | `tools/code_nav.py`(新) | 按名/正则找符号定义 → `file:line` 列表 | 给名字能定位到定义 |
| `read_function` | `tools/code_nav.py`(新) | 读一个符号的完整定义体 + 元数据 | 给符号+文件能拿到完整代码 |
| `search_code` | `tools/code_nav.py`(新) | 语义混合检索 → top-k chunk(包 P1.3 `retrieve`) | demo agent 能用它定位模块 |
| `grep` | `tools/sandbox.py`(新工具) | 正则搜索文件内容 → `path:line: content` | 正则/ignore/二进制不被拖累 |
| `read_file`(升级) | `tools/sandbox.py`(改) | 无行范围→tree-sitter BFS 摘要+elision footer;有行范围→原文 | 大文件信息密度↑、可恢复性不丢 |
| 二进制守卫 | `platform/sandbox/_search.py`(新) | `is_probably_binary(path)` 共用 | 二进制不再毁终端/烧 context |

### 4.2 搜索内核 `platform/sandbox/_search.py`(新,纯算法零依赖)

> **面向小白**:这是 `grep` 的发动机。给它一个目录 + 一个正则,它在目录里逐文件逐行找匹配,返回 `path:行号: 行内容` 列表。**核心是几道安全闸**(直接照搬 deer-flow `sandbox/search.py`,纯 Python、零新依赖、已验证):

- **内建 ignore 黑名单**(`IGNORE_PATTERNS`):`.git`/`node_modules`/`__pycache__`/`.venv`/`dist`/`build`/`data`/`.pytest_cache` 等 ~50 项。**不是**解析 `.gitignore`(deer-flow 也没做;omp 用 Rust `ignore` crate,P1.4 不引新依赖,`.gitignore` 解析延后 [backlog #1](../../.claude/memory/backlog-production-grade.md))。
  - 性能优化(借 deer-flow `should_ignore_name`):字面量名进 `_EXACT_IGNORE_NAMES` frozenset 做 O(1) 查;通配模式预编译成单条联合正则 `_GLOB_IGNORE_RE`。每个目录项只 1 次 set 查 + 1 次 regex。
- **二进制守卫** `is_binary_file(path, sniff=8192)`:读前 8192 字节,**含 NUL(`\0`)** 或 **非 UTF-8**(fatal decode 失败)即判二进制(双保险,借 omp `binary.ts` + deer-flow `is_binary_file`);`OSError` 时 fail-closed(当二进制跳过)。
- **大文件守卫** `DEFAULT_MAX_FILE_SIZE_BYTES = 1_000_000`(1MB,借 deer-flow):超限跳过该文件。
- **ReDoS 守卫** `_max_line_chars = 2000`(借 deer-flow):超过的行直接 skip,防 minified/无换行文件把正则拖进灾难回溯。
- **行截断** `truncate_line(line, limit=200)`:超长行尾部 `...`(借 deer-flow + omp `truncate_line`)。
- **symlink 守卫**:跳过 symlink;`resolve()` 后必须仍在 root 下(`is_relative_to`),防符号链接逃逸(借 deer-flow)。
- **正则回退链**(借 omp `grep.rs:987-1055` 的"搜索永不整次失败"思想):`re.compile(pattern)` → 失败则尝试未闭合括号自动转义重试 → 最终 `re.escape` 降级为字面匹配。**保证一条坏正则不会让整个搜索炸掉。**

**核心循环** `find_grep_matches(root, pattern, *, glob, literal, case_sensitive, max_results)`(借 deer-flow `find_grep_matches`):`os.walk` 遍历 → 先 prune 黑名单目录(`dirs[:] = [...]`)→ 按 glob 过滤候选文件 → 跳过二进制/超大 → UTF-8 逐行正则搜 → 命中追加 `GrepMatch(path, lineno, line)` → 到 `max_results` 立即返回 `truncated=True`。

**输出格式**(借 deer-flow `_format_grep_results`,LLM 友好):
```
Found 3 matches under /repo/src
src/a.c:42:   disconnect_cb(dev);
src/a.c:108:  register(DISCONNECT, disconnect_cb);
src/b.c:7:    void disconnect_cb(struct device *d) {
Results truncated. Narrow the path or add a glob filter.   ← 仅截断时
```

### 4.3 grep 升级(`Sandbox.grep` + `grep_tool`)

> **面向小白**:P0 的 grep 是"字面子串、啥都扫、二进制也硬读"(local.py 现状:`pattern in line` + `errors="ignore"`)。升级成"正则、跳过垃圾目录/二进制/超大文件、有结果上限"。

- **引擎层**:`Sandbox.grep()`(base.py 接口签名不变,local.py 改实现)改为调用 `_search.find_grep_matches`,从"字面子串"升级到"正则 + ignore + 二进制/大小/ReDoS 守卫 + max_results"。
- **工具层** `grep_tool`(新增,暴露成 agent @tool,目前 grep 只能经 bash 调):
```python
@tool("grep", parse_docstring=True)
def grep_tool(description, pattern, path, glob=None, literal=False,
              case_sensitive=False, max_results=100) -> str:
    """正则搜索文件内容(默认大小写不敏感、走内建 ignore)。literal=True 走字面。
    返回 'path:line: content'。命中过多会截断并提示收窄 path/glob。"""
```
  - 参数三件套 `literal`/`case_sensitive`/`glob` 直对标 deer-flow `grep_tool`;`max_results` 双重 clamp(调用者 vs 硬上限 500,借 deer-flow `_MAX_GREP_MAX_RESULTS`)。
- **FS 扫描缓存**(借 omp `pi-walker` cache):TTL 1s + 空结果 200ms 重检 + 写后失效。**P1.4 先做无缓存版**(correctness 优先),缓存记 [backlog #1](../../.claude/memory/backlog-production-grade.md)。

### 4.4 read 升级:tree-sitter BFS 摘要 + elision footer

> **面向小白**:让 agent 读一个大 `.c`/`.py` 时,默认**别把全文糊它脸上**——先给一张"大纲"(函数/类的签名 + docstring 留着,函数体折叠成一行省略号),末尾告诉它"第 42-80 行被折叠了,想看就用 `read_file(start_line=42, end_line=80)` 捞回来"。这就是 omp 的 `read` 无 selector 自动摘要。

**触发规则**(分层,保可恢复性):
1. 给了 `start_line`/`end_line` → **走原文 verbatim**(当前行为,绕过摘要)。
2. 没给行范围 + 文件是**可解析代码** + 行数过阈值(如 >50 行)→ **BFS 摘要 + elision footer**。
3. 散文(md/txt)/ 小文件 → 当前 head-truncate(50000 字符)。
4. 二进制 → 守卫拦下(§4.7)。

**BFS 摘要** `services/code_index/outline.py`(新,复用 P1.0 parser):
- **elidable spans = 每个 Symbol 的函数/类/方法体**(直接拿 parser 的 `start_line/end_line`,**Symbol 粒度**;omp 是 AST 每 elidable 节点更细,语句级折叠记 [backlog #6](../../.claude/memory/backlog-production-grade.md))。模块级代码(import/全局/`if __name__`)留全文。
- **BFS unfold**(借 omp `select_folded_spans`,summary.rs:118-162):初始所有符号体折叠 → 广度优先逐个展开,直到"可见行数 ≥ 目标 50"(硬上限 100)。**关键边界**:若展开某符号会让可见行超硬上限,**跳过它但继续处理兄弟**(借 omp summary.rs:150-152,防单个超大函数饿死整个大纲)。
- **解析失败/无 grammar**:回退 head-truncate(借 omp `unparsed_result`)。

**elision footer**(借 omp `formatSummaryElisionFooter`,issue #1046——没它模型会瞎猜或全文重读):
```
…[已折叠 128 行;如需细读用 read_file 取这些范围:start_line=42 end_line=80, start_line=108 end_line=160] …
```
**给真实被折叠的范围当例子**(取前 2 段),引导模型只捞需要的,而非全文重读。

### 4.5 `grep_symbol` / `read_function`(借 parser,deer-flow/omp 没有的增量)

> **面向小白**:这两个工具直接用 P1.0 的 parser(已经在抽符号了),不需要 LanceDB 索引——**就算没建索引也能用**,是纯结构导航。

```python
@tool("grep_symbol", parse_docstring=True)
def grep_symbol_tool(description, name, path=None, regex=False, max_results=50) -> str:
    """按名字(或正则)找符号定义——function/class/method 在哪个 file:line。
    返回 'file:start_line  kind  qualified_name  signature'。"""

@tool("read_function", parse_docstring=True)
def read_function_tool(description, symbol, file) -> str:
    """读一个符号(函数/类/方法)的完整定义体 + 元数据(签名/docstring/行范围/kind)。
    file 用 grep_symbol 拿到的路径;symbol 给 qualified_name 精确匹配。"""
```

- **实现**:`grep_symbol` → parse 文件(带 mtime 缓存)→ 过滤 name 匹配的 Symbol → 输出 `file:line  kind  qualified  signature`。`read_function` → parse 那个文件 → 按 qualified_name 定位 Symbol → 切 `[start_line, end_line]` 原文 + 元数据头。**不依赖索引**,parse 慢则记 backlog 用 LanceDB FTS 加速。
- **为什么 omp/deer-flow 没这俩**:omp 把它们折进 `read`(无 selector = 大纲,带 selector = 读体)和 `ast_grep`(结构搜);deer-flow 让模型自己用 grep + 正则 `def xxx`。Hyperion 单列两个工具**对 agent 更直白**(显式 > 隐式),且复用现成 parser,零额外成本。

### 4.6 `search_code`(包 P1.3 `retrieve`,语义层)

> **面向小白**:这是把 P1.3 的混合检索(BM25+向量+RRF+rerank)暴露成工具。agent 问"断连处理在哪",它返回最相关的几块代码。

```python
@tool("search_code", parse_docstring=True)
def search_code_tool(description, query, top_k=5) -> str:
    """语义搜索代码:自然语言查询 → 混合检索 → top-k 代码块(file:line + score + 代码片段)。"""
```

- **依赖装配**(`_retrieval_bundle()`,模块级 `lru_cache` 单例):从 `AppConfig.code_index` 建 `create_embedder` + `LanceDBStore(cfg.vector_store.path)` + `create_reranker`(可 None)——**懒构造、只建一次**(镜像 sandbox 工具的 `_sandbox()` 模式)。
- **repo 解析**:新增 `code_index.repo` 配置字段(显式);缺省回退 `workspace` 目录名。必须与 `build_index` 用的 repo 名一致(否则查空表)。
- **前置**:表必须已建(`uv run hyperion index <repo> <path>`,见 §4.9);未建 → 返回提示"先建索引"。
- **降级**:reranker 失败 `retrieve` 已内置降级(§2.5);整表缺失 → 工具返回可操作错误,不抛异常(借 deer-flow "错误返串不抛")。

### 4.7 二进制守卫(横切,read/grep 共用)

> **面向小白**:防止 agent 一不小心 `read_file` 一个 `.o`/`.png`,把终端刷乱码、把几万字节二进制塞进 context。

- `is_probably_binary(path, sniff=8192)`(放 `_search.py`):前 8192 字节含 NUL **或** fatal UTF-8 decode 失败 → 二进制;`OSError` → fail-closed 当二进制。
- read 命中二进制 → 返回可操作错误(借 deer-flow `test_read_file_tool_binary.py` 锁定的文案,含 "binary" + 指向 bash):
  `错误:'xxx.o' 是二进制文件,read_file 只支持 UTF-8 文本。用 bash 工具查看(如 xxd/file),或后续 :raw 逃生舱(backlog)。`
- omp 的 `:raw` 逃生舱(绕过守卫读原始字节)记 backlog;P1.4 用 bash 兜底。

### 4.8 声明式接入(`config.yaml`)

`config.yaml -> tools` 追加 4 条(registry 反射加载,P0 已验证):
```yaml
  - { name: grep_symbol,  group: code,      use: hyperion.tools.code_nav:grep_symbol_tool }
  - { name: read_function,group: code,      use: hyperion.tools.code_nav:read_function_tool }
  - { name: search_code,  group: code,      use: hyperion.tools.code_nav:search_code_tool }
  - { name: grep,         group: file:read, use: hyperion.tools.sandbox:grep_tool }
```
`code_index` 段加 `repo:` 字段(显式 repo 名);`read_file` 已在 tools 里(升级,不改 name)。

### 4.9 退出标准 + 验证

| # | 标准 | 验证方式 |
|---|---|---|
| 1 | `grep_symbol("retrieve")` 命中 `retrieval.py` 的定义 | `uv run hyperion tools` 列出工具 + demo 调用 |
| 2 | `read_function("retrieve",".../retrieval.py")` 返回完整函数体 | demo 调用 |
| 3 | `search_code("混合检索")` 返回 retrieval.py 相关 chunk | demo 调用(需先 `hyperion index`) |
| 4 | `grep("disconnect_cb", src)` 正则生效、跳过 .git/二进制 | demo + 对比 P0 字面 grep |
| 5 | `read_file` 大文件出 BFS 摘要 + 真实范围 footer;带行范围出原文 | demo 双路径 |
| 6 | 二进制文件被守卫拦下,返回可操作错误 | `read_file` 一个 .pyc |

**实测结果(2026-07-28,全绿)**:`hyperion tools` 列出 9 个工具全 ✓;`grep_symbol('retrieve')`→retrieval.py:236;`read_function` 出完整函数体+元数据;`search_code('混合检索 RRF')`→hybrid+rerank top-3(store.py hybrid_search, score 0.74);`grep` 正则命中 2 处且跳过 `__pycache__`;`read_file` 大文件出 BFS 摘要+真实范围 footer、带行范围出原文;二进制(`.pyc`)被守卫拦下。`hyperion index src/hyperion hyperion` 增量 58 chunk。

**CLI(已成)**:`uv run hyperion index <repo_path> [repo_name] [--force]`(建/更新索引)、`uv run hyperion tools [--group X]`(列工具,✓/✗ 标加载结果)。

### 4.10 借鉴对照(file:line 出处)

| 设计点 | 借鉴源 | Hyperion 落点 |
|---|---|---|
| 搜索内核(IGNORE_PATTERNS/is_binary_file/max_size/ReDoS/truncate_line/symlink/os.walk+prune) | deer-flow `sandbox/search.py`(全文) | `_search.py` |
| grep 工具参数(literal/case_sensitive/glob/max_results clamp)+ 输出格式 `path:line:` | deer-flow `sandbox/tools.py:grep_tool` + `_format_grep_results` | `grep_tool` |
| read head-truncate + marker 内嵌下一步提示 | deer-flow `_truncate_read_file_output` | `read_file` verbatim 路径 |
| 正则回退链(compile→括号修复→escape 字面,搜索永不失败) | omp `grep.rs:987-1055` | `_search._compile_pattern` |
| 二进制双保险(NUL + fatal UTF-8,8192 sniff) | omp `binary.ts` + deer-flow `is_binary_file` | `is_probably_binary` |
| BFS unfold(select_folded_spans,超限跳过继续兄弟) | omp `summary.rs:118-162` | `outline.summarize` |
| elision footer 给真实范围(issue #1046) | omp `read.ts:385-399` | `read_file` 摘要路径 |
| 错误返串不抛 + 可操作错误文案 | deer-flow `test_read_file_tool_binary.py` | 全部工具 |
| grep_symbol / read_function(显式符号工具) | **Hyperion 增量**(复用 P1.0 parser;omp 折进 read/ast_grep,deer-flow 无) | `code_nav.py` |

### 4.11 生产级补齐(记 backlog,不在 P1.4 做)

- grep 用 ripgrep(`subprocess rg` 或 `pyre2`/`regex` 库)替纯 `re`:大仓性能 [backlog #1]。
- `.gitignore`/`.hyperionignore` 解析(替硬编码黑名单):`pathspec` 库 [backlog #1]。
- ast-grep 式**结构化 AST 搜索**(omp `signature` 档):替 `grep_symbol` 的名匹配 [backlog #28]。
- 语句级折叠(omp 每 elidable 节点,非 Symbol 粒度) [backlog #6]。
- FS 扫描缓存(TTL 1s + 空结果 200ms + 写后失效)+ `.gitignore` 解析 [backlog #1 / #30]。
- model 文本 vs display 双轨(给 LLM 的可被工具反向解析) [backlog #29,P2 Hashline 一起]。
- `:raw` 逃生舱 + 结果分页 `skip` [backlog #30]。

---

## 5. 待做 · P1.5:L2 精确导航(LSP/clangd)

> **面向小白**:L1 检索是"模糊"的——你问"谁调用了 X",它按意思猜,可能漏调用点。L2 用 **clangd**(C/C++ 的语言服务器,IDE 那套"转到定义/查找引用"的引擎),给你**精确**的调用点:每一处、连宏展开、跨文件、系统头文件都准。它本质就是一个"由编译器前端建好的、精确的代码图"。

### 为什么弃 code_graph,改 LSP(关键转向)

原计划 P1.5 自建 `code_graph.py`:用 tree-sitter 找所有函数调用 → 连成"谁调用谁"图 → 支持 `get_callers/get_callees`。**改为 LSP/clangd**,理由:

- **clangd 取代 code_graph 的 caller/callee 职责**,且更准。code_graph 对 C 有三座翻不过的墙:① `static` 同名函数跨文件 ② 调用被宏包着(`DBG(...)` 拿到的是宏名不是真函数)③ 函数指针/回调注册根本解析不到。clangd 有完整编译数据库(`compile_commands.json`),预处理展开宏、按作用域消歧、处理重载——**绕开这三墙**。
- **业界印证**:ChatDBG、oh-my-pi、所有 IDE 类 agent 都用 LSP/调试器做精确导航,不自建静态图(RepoGraph 那类自建图是"agent 还没法假设有 LSP"时代的妥协)。
- **code_graph 独占的"图算法"价值**(PageRank 排"最重要符号"、BFS 影响面)→ 降级存活到 `repo_map`(Aider 式"全仓最重要符号"地图,与 LSP 互补,用途不同),**延后**,记 backlog,不单独建 `code_graph.py`。

> 三层栈视角:L1(向量,已成)回答"大概在哪",L2(LSP)回答"精确谁调谁",L3(DAP,P2)回答"运行时为什么"。L1 不被取代——L2 叠在其上(先 L1 定位模块,再 L2 串调用链)。

### 落地(2026-07-28 调研后定稿)

> 调研结论一句话:**经 multilspy(微软 Python LSP client),自写一层 `ClangdServer` 适配器接 clangd**——multilspy 不自带 clangd(见下"勘误"),但它的 LSP 通用机制(JSON-RPC、initialize 握手、文件同步、请求/响应关联、超时、同步包装)全部可复用,我们只补"怎么起 clangd"这一小块。

#### 5.1 multilspy 现状勘误(关键,改了原假设)

原 §5 假设"multilspy 开箱即用 clangd"。**实测装包读源码后修正**:

- **multilspy 0.0.15**(MIT;依赖轻:pygls / lsprotocol / requests,**无 torch、无 node**)。
- 它**自带 9 种**语言服务适配器:python(jedi)/ rust(rust-analyzer)/ go(gopls)/ java(jdtls)/ js/ts / ruby / c#(omni)/ kotlin / dart。**没有 C/C++/clangd**(对应 GitHub issue #14「C++ Support?」至今未官方接入)。
- 其 `Language` 枚举(`multilspy_config.py`)**不含 C/C++** → 不能用 `LanguageServer.create(config, ...)` / `SyncLanguageServer.create(...)` 工厂(工厂按 `config.code_language` 分发,没有 clangd 分支)。
- **关于 `main` 分支(2026-07-28 核实)**:multilspy 的 `main`(未发版)其实已并入官方 `ClangdLanguageServer` + `Language.CPP` + `MultilspyConfig.server_binary`(用系统 clangd、免下载)。**但仍不自用它**,原因:① PyPI 最新仍 **0.0.15**(clangd 仅在 `main`,无发版 wheel,pin 到移动 git HEAD 不符生产级);② 它的 `ProcessLaunchInfo(cmd=[clangd])` **不带任何 flag**——加不了 `--limit-references=0`(clangd 默认截断到 1000 条 references,见 5.6,对 agent 是"假完整"致命);③ `start_server` 里硬编码 `assert ... completionProvider == {...}`(连触发字符都断言),换 clangd 版本就崩。**故即便用 main 仍要 override**——那就直接在 released 0.0.15 上自写,pin 干净、完全自控。

**对策**:照 `rust_analyzer.py` / `gopls.py` 的模板(rust-analyzer 与 clangd 同为「stdio + 编译型」语言服务,最像),自写 `ClangdServer(LanguageServer)`——只实现三件事,其余继承:

| 要实现的 | 干什么 | 参考模板(file:line) |
|---|---|---|
| `__init__` | 找 clangd 二进制(`shutil.which` 或 config)→ 拼 `ProcessLaunchInfo(cmd="clangd --background-index ...", cwd=repo_root)` → `super().__init__(config, logger, repo_root, launch_info, "cpp")` | rust_analyzer.py:`__init__` |
| `_get_initialize_params(root)` | 填 `rootPath`/`rootUri`/`workspaceFolders`(clangd 靠它定位工程根 + 找 compile_commands) | rust_analyzer.py:`_get_initialize_params` |
| `start_server`(async ctx-mgr) | 注册 `window/logMessage`·`textDocument/publishDiagnostics`·`$/progress` 处理器 → `await self.server.start()` → 发 `initialize` → `notify.initialized({})` → `yield self` → `shutdown`/`stop` | rust_analyzer.py:`start_server` |

> **不 monkeypatch multilspy 内部枚举**,只继承 + 直接 `ClangdServer(...)` 实例化,再包进 `SyncLanguageServer(clangd_server, timeout)`。绕开工厂,零侵入。这是 P1.5 唯一的「原创代码块」(约 80 行);JSON-RPC 收发、Content-Length 分帧、文件 did_open/did_close 同步、请求 id 关联、超时——全部白嫖 multilspy。

#### 5.2 multilspy 真实 API(照着写代码用)

读 `multilspy/language_server.py` 确认:

- **`SyncLanguageServer`**(line 686)是同步门面,**自带一个 asyncio loop + daemon 线程**(经 `start_server()` ctx-mgr 起停,line 757)。我们要的三个方法都是**同步**签名(内部 `run_coroutine_threadsafe(...).result(timeout)`):
  - `request_references(file_path, line, column) -> List[Location]`(line 791)——**精确调用点 = callers**。
  - `request_definition(file_path, line, column) -> List[Location]`(line 775)——跳定义,含系统头宏展开。
  - `request_hover(file_path, line, column) -> Hover | None`(line 840)——签名/宏展开/枚举值/类型。
  - 另送:`request_workspace_symbol(query)`(line 856,全仓符号)、`request_document_symbols(rel_path)`(line 826,单文件大纲)——P1.5 先不暴露,记 backlog。
- **`open_file(rel_path)` ctx-mgr**(line 727):发 `did_open`(带文件全文)→ 请求 → `did_close`,带引用计数。**工具调用模板**:
  ```python
  with sync_server.start_server_held():        # 单例里只进一次(见 5.3)
      with sync_server.open_file(rel_path):
          locs = sync_server.request_references(rel_path, line_0, col_0)
  ```
- **`ProcessLaunchInfo.cmd` 是 shell 字符串**(`lsp_protocol_handler/server.py:54`,经 `create_subprocess_shell` 启动,line 217)→ 不是 list。拼 clangd 命令行必须用 `shlex.join([clangd_path, "--background-index", ...])`,**别手拼**(路径/参数有空格会被 shell 拆错)。
- **LSP 位置是 0-based**(line 和 character 都从 0 起,character 按 UTF-16 code unit)。
- **agent 暴露的三件套工具**(命名对齐 Cursor / Claude Code,LLM 更熟):`find_references` → `request_references`(精确调用点 = callers)、`goto_definition` → `request_definition`(跳定义,含系统头)、`hover` → `request_hover`(签名/宏展开/类型)。

#### 5.3 生命周期:进程级单例(照搬 sandbox provider)

clangd 起一次要数秒(建索引),**不能每个工具调用都重启**。故镜像 `get_sandbox_provider`(`platform/sandbox/provider.py`):

- `get_lsp_server(repo_root) -> SyncLanguageServer`:**首次懒起**一个 clangd 常驻进程——手动 `cm = sync_server.start_server(); cm.__enter__()` 进上下文一次(不退出),`atexit.register(cm.__exit__, None, None, None)` 注册优雅关闭。
- 双检锁、锁内解析类、锁外构造(防回调自死锁)——与 sandbox provider 同构。
- 多 repo:单例按 `repo_root` 缓存(`dict[str, SyncLanguageServer]`);P1.5 实际只单仓,留口子。

#### 5.4 定位协议:`file + line + symbol → column`

工具统一收 `file + line + symbol`(line 用 parser 的 1-based)。`request_references` 要精确 `(line, column)` 指在符号上,故:
1. `line_0 = line - 1`(转 0-based);
2. 读该行,正则找 `symbol` 出现的列 `col_0`(取首个;符号名一般是 ASCII,CJK 极少,UTF-16 近似够用);
3. 调 `request_references(file, line_0, col_0)`。
找不到列(行里没这符号)→ 返可操作错误(提示用 `read_file` 核对行号)。

#### 5.5 索引未就绪 → 重试一次

clangd `initialized` 后即可响应,但**后台索引还在建**,首次 `references` 可能少召回。空或偏少时**重试 1 次 + ~300ms 退避**(`--background-index` 二次启动会复用 `.cache/clangd/index`,故重试收益主要在首次冷启)。多次重试收益递减,不做。

#### 5.6 clangd 启动参数(走 config 可调)

| flag | 作用 | 默认 |
|---|---|---|
| `--limit-references=0` | **★ 关键**:clangd 默认把 references 截断到 **1000 条**(对 `g_dbus_proxy_new` 这类高频符号会不够),`=0` 取消上限。不加这个,agent 看到的是"假完整"调用点列表 → 漏判根因。出处:[clangd FAQ](https://clangd.llvm.org/faq) | **开** |
| `--limit-results=0` | 同理,`workspace/symbol` 默认也截断;取消上限 | **开** |
| `--background-index` | 持久化索引到 `.cache/clangd/index`,二次启动快 | 开 |
| `-j=<N>` | 索引并行度 | 4 |
| `--compile-commands-dir=<path>` | 强制 compile_commands.json 位置(不填则 clangd 从源文件目录向上找) | 空(自动找) |
| `--query-driver=<编译器路径>` | 交叉编译器识别(bluez/wpa 交叉编译场景) | 空 |
| `--header-insertion=never` | 导航用不上自动插 include | 开 |
| `--clang-tidy` | lint(**噪声大**,导航用不上) | **关** |

#### 5.7 降级路径(生产级关键,绝不全静默返空)

空结果会被 agent 误判「没人调用」→ 致命。故:

- **clangd 二进制缺失** / **compile_commands.json 缺失** → `hyperion lsp health` 明确报告缺什么 + 怎么补(`bash scripts/setup.sh` 装 clangd+bear;`bear -- make` 或 `cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON` 生成 compile_commands)。
- 工具(`find_references` 等)在上述缺失时返**可操作错误串**(含上述指引),**不抛、不静默空**。
- compile_commands 缺失但仍能起 clangd(heuristic 模式)→ 工具结果前加⚠️提示「无 compile_commands,references 质量降级」。

#### 5.8 配置(`config.yaml`,新增 `code_index.lsp`)

```yaml
code_index:
  lsp:
    clangd_path: null          # null = shutil.which("clangd") 找;或写绝对路径
    extra_args: []             # 追加 clangd flag(如 ["--query-driver=/usr/bin/arm-linux-gnueabihf-gcc*"])
    start_timeout: 30          # 起 clangd + initialize 的超时(秒)
    request_timeout: 15        # 单次 references/definition/hover 超时(秒)
    index_retry: 1             # 结果为空/少时重试次数
    index_retry_delay: 0.3     # 重试间隔(秒)
    compile_commands_dir: null # null = 不强制,clangd 自动找;或写绝对路径
```

#### 5.9 CLI(`hyperion lsp ...`)

- `hyperion lsp health` —— 自检:clangd 在否(版本号)、compile_commands.json 在否、(可选)起一个 clangd 跑一次 hover 看能不能通。绿/红 + 修复指引。
- `hyperion lsp refs <file> <line> <col>` —— 冒烟:直接打一次 references,打印调用点。给"装好 clangd 后验证"用。

#### 5.10 退出标准(可验证)

1. `uv run hyperion lsp health` 在「装了 clangd + 有 compile_commands」的仓报**绿**;在缺 clangd 的机器报**红 + 修复指引**。
2. 对自带 C fixture(`tests/fixtures/lsp_c/`:mini.c 调 lib.c 的函数,手写 compile_commands.json),`find_references` **精确返回全部调用点(零漏召)**——对比 L1 向量检索的同义 query,漏召显著降低。
3. clangd 缺失时,三个 LSP 工具返**可操作错误串**(含 setup 指引),不抛、不静默空。
4. `uv run ruff check .` clean;multilspy 导入 + 单例起停**不泄漏子进程**(`ps` 无残留 clangd)。
5. `CodeChunk.callers/callees` 字段不再被任何代码回填(P1.4 留的空 tuple,本阶段确认无害或清理)。

> **实测计划**:① 自带 C fixture(零依赖,装 clangd 即跑)验证 plumbing;② bluez / wpa 真实仓的 `bear -- make` 生成 compile_commands + 全仓 references 留到 **R3+ bug-RCA 首个真实任务**(需源码 + 干净 build 环境,P1.5 不强行)。

> **实测结果(2026-07-28,自带 fixture `tests/fixtures/lsp_c/`,clangd 17.0.6)**:
> - 退出标准 **①**✓:`hyperion lsp health` 报 ✓✓ 绿(clangd + compile_commands 就位);缺 clangd 的机器报 ✗ + 修复指引(降级路径验过)。
> - 退出标准 **②**✓:`find_references(add @ lib.c:3)` 精确返回 **main.c:5 与 main.c:7 两处调用点,零漏召**(含 definition 精确跳 lib.c:3、hover 含 `int add(int a,int b)` 签名)。
> - 退出标准 **③**✓:clangd 缺失时三工具返可操作错误串(`先跑 hyperion lsp health 自检` + setup 指引)。
> - 退出标准 **④**✓:`ruff check .` clean;clangd 经 `atexit` 优雅 shutdown,无残留子进程。
> - 退出标准 **⑤**:`CodeChunk.callers/callees` 默认空 tuple,L1 代码不读、LSP 不回填——无害(清理记 backlog)。
> - ⚠️ **首次 references 冷启动会空**(后台索引未就绪)——单进程内带 1 次重试(`index_retry`)兜;**跨进程的 `hyperion lsp refs` CLI 不带重试**(冒烟用,首次可能空,再跑一次或用 agent 工具路径)。

#### 5.11 借鉴对照(file:line 出处)

| 决策/做法 | 出处 |
|---|---|
| `LanguageServer`/`SyncLanguageServer` 公开 API | multilspy `language_server.py:178/363/440/627/686/791` |
| 「stdio+编译型」语言服务的 start_server 模板 | multilspy `language_servers/rust_analyzer/rust_analyzer.py`(`__init__`/`_get_initialize_params`/`start_server`) |
| `ProcessLaunchInfo.cmd` 是 shell 字符串 → `shlex.join` 拼装 | multilspy `lsp_protocol_handler/server.py:54,217` |
| 进程级单例 + 双检锁 + 锁内解析/锁外构造 + atexit | Hyperion `platform/sandbox/provider.py`(自洽,照搬) |
| `file+line+symbol` 定位 + 索引重试 | 本项目 P1.4 `code_nav` 模式延续 + 设计原案 |
| compile_commands 生成(bear / cmake / compiledb) | 业界惯例(bear=Build EAR;cmake `-DCMAKE_EXPORT_COMPILE_COMMANDS=ON`;compiledb 干跑 `make -nw`) |

#### 5.12 生产级补齐(记 backlog,不在 P1.5 做)

- **get_callees 聚合**:对定义体里每个调用点逐一 `goto_definition` → 聚合成 callee 列表(P1.5 只做 callers/references)。
- **严格 caller 列表(callHierarchy)**:multilspy 只暴露 `textDocument/references`,**不含** `textDocument/prepareCallHierarchy` + `callHierarchy/incomingCalls`(LSP 标准的"真·caller 树")。对 C 函数 references ≈ callers(够用),但 references 会混入"取地址/赋值"等非调用点;要严格调用树得绕过 multilspy 直接 `self.server.send.call_hierarchy_incoming(...)`。
- **索引就绪信号**:用 clangd `$/progress` 的 background-indexing 完成通知(`N==M`)或 `experimental/serverStatus` 的 `quiescent==true` 替代固定 300ms(更准)。
- **references 渲染给 LLM 的截断策略**(高价值):大仓 references 可能上百条 → ① 按 `(uri,line)` 去重(宏展开会多次命中同行)② 按文件分组、每文件只展首条 + `(N more in this file)` ③ 每条带 **caller 函数名**(对该 reference 反查所属函数定义)④ Top-N(10–20)+ `N more omitted, 用 depth=2 展开`。别 dump 原始 JSON。
- **多语言**:rust/python/go LSP 直接复用 multilspy 自带 adapter(只需在 `get_lsp_server` 按 repo 语言分发)。
- **`.clangd` 配置 / `--query-driver`**:交叉编译(bluez arm/wpa)的编译器识别;`.clangd` YAML 还能 `CompileFlags.Add`、对大目录 `Index: Background: Skip`(systemd 的 test/、vendor/)。真实仓落地时补。
- **大仓离线索引(SCIP)**:systemd / Linux kernel 量级,实时 clangd 首次索引可能数分钟~数小时且内存膨胀。备选 [`scip-clang`](https://github.com/sourcegraph/scip-clang) 离线把全仓索引成 SCIP 文件再查询(Sourcegraph Cody 路线),稳定性/性能远胜实时 clangd,代价是增量更新麻烦。`.cache/clangd/index/` 须持久化(随仓分发 / Docker volume)。
- **compile_commands 生成优选 compiledb**:autotools 项目(bluez/wpa)用 `compiledb --parse make -nW V=1`(解析 dry-run,不真编、不受 `LD_PRELOAD`/SELinux/CCACHE 干扰)比 `bear -- make V=1` 稳;bear 4.0.x 有"产出空 JSON"bug(#660/#656),出问题回退 3.1.6 或换 compiledb。setup.sh 两者都装。
- **LSP 结果短期缓存**:同一 `(file,line,col)` 短期复用(镜像 `_symbols_for_file` 的 mtime 键)。
- **业界印证**(方向校准):Cursor / Claude Code(v2.0.74 原生 9 个 LSP 工具)/ Continue 都用「LSP 符号图 + 向量」分层检索——正是本项目 L1(向量)+L2(LSP)设计;反方证据(SWE-bench 上纯 grep 偶尔胜过纯向量)恰好说明**向量必须配精确层**,不可单独用。

> L3(DAP 调试器,attach/读栈/读变量)留 **R3+** bug-RCA(可复现 bug 现场深挖);TTSR 流式规则 / advisor 副驾 / Hashline 补丁等护栏也留 R3+。详见 [后续设计演进报告](../调研/后续设计演进报告-oh-my-pi与最佳实践.md)。

---

## 6. 配置(`config.yaml`,P1.2–P1.3 已落地)

```yaml
code_index:
  repo: hyperion                       # P1.4:search_code 查的表名(须与 build_index 一致;缺省回退 workspace 目录名)
  embedding:                           # embed.py(P1.2 已落地:远端 OpenAI 兼容默认)
    provider: openai_compatible        # 远端默认(免下载/免 torch);本地可选 sentence_transformers
    base_url: $DASHSCOPE_BASE_URL      # 走 ENV(.env);未设回落 serverless dashscope.aliyuncs.com
    api_key: $DASHSCOPE_API_KEY
    model: text-embedding-v4           # = Qwen3-Embedding 全血版;换则触发全量重建
    dimensions: 1024
    batch_limit: 10                    # DashScope v4 每请求 10 条
    normalize: true
  vector_store:
    path: data/code_index              # table-per-repo:<path>/<repo>/lancedb
  retrieval:
    rrf_k: 60                          # Cormack 2009 标准
    candidate_top_n: 50                # 喂 reranker 的候选池
    final_top_k: 5
    fts_stem: false                    # 代码场景必须关
    fts_remove_stop_words: false       # 代码场景必须关(否则 int/void/public/return 被删)
  reranker:                            # provider 抽象,镜像 embedding。DashScope rerank 走原生端点
    provider: dashscope                # dashscope(默认)| siliconflow(免费)| sentence_transformers(本地,GPU)| off
    base_url: $DASHSCOPE_RERANK_URL     # 走 ENV;未设回落 serverless(DashScope rerank 走原生端点,非 OpenAI 兼容面)
    api_key: $DASHSCOPE_API_KEY        # 与 embedding 同一个 key
    model: qwen3-rerank
    rerank_top_n: 5

tools:                                 # code 导航:P1.4(grep_symbol/read_function/search_code)+ P1.5 LSP 三件套(find_references/goto_definition/hover)
  - { name: grep_symbol,     group: code,      use: hyperion.tools.code_nav:grep_symbol_tool }
  - { name: read_function,   group: code,      use: hyperion.tools.code_nav:read_function_tool }
  - { name: search_code,     group: code,      use: hyperion.tools.code_nav:search_code_tool }
  - { name: find_references, group: code,      use: hyperion.tools.code_nav:find_references_tool }
  - { name: goto_definition, group: code,      use: hyperion.tools.code_nav:goto_definition_tool }
  - { name: hover,           group: code,      use: hyperion.tools.code_nav:hover_tool }
  - { name: grep,            group: file:read, use: hyperion.tools.sandbox:grep_tool }
```

---

## 7. 模块文件布局

```
src/hyperion/services/code_index/
├── __init__.py
├── parser.py        # [P1.0]  tree-sitter:Symbol 抽取(含 .h)
├── chunker.py       # [P1.1]  符号边界切块 + fts_text 分词
├── embed.py         # [P1.2]  embedding(远端默认/本地可选)
├── store.py         # [P1.2]  LanceDB 表(schema/BTree+FTS 索引/merge_insert/查询)+ VectorStore 接口
├── index.py         # [P1.2]  编排:walk→parse→chunk→embed→store(+ 原子性 + 增量,§2.6)
├── retrieval.py     # [P1.3]  混合检索(BM25+向量+RRF+rerank)
├── outline.py       # [P1.4]  tree-sitter BFS 摘要 + elision footer(复用 parser,§4.4)
├── lsp.py           # [P1.5]  ClangdServer(multilspy 适配器)+ get_lsp_server 单例 + health(§5)
├── loc_translate.py # [已建成] 行区间渲染原语(transfer_locs/merge_intervals/line_wrap_content/sticky_scroll)—— 通用构建块,非 funnel 专用
├── skeleton.py      # [已建成] 符号骨架/文件树渲染(render_file_tree/render_skeleton;复用 parser.Symbol)—— 喂 MCP 工具紧凑锚点 + 报告
└── eval/            # [P1.3]  scorer.py(指标)+ runner.py(harness)
```

P1.4 还涉及另两处(不在 services/code_index/ 下):
```
src/hyperion/tools/code_nav.py            # [P1.4 已成] grep_symbol / read_function / search_code + _retrieval_bundle
src/hyperion/tools/sandbox.py             # [P1.4 已成] read_file 升级(BFS 摘要)+ 新增 grep_tool
src/hyperion/platform/sandbox/_search.py  # [P1.4 已成] 搜索内核(IGNORE_PATTERNS/is_probably_binary/find_grep_matches)
src/hyperion/platform/sandbox/local.py    # [P1.4 已成] grep() 用 _search 内核;read_file() 加二进制守卫
```
P1.5 还涉及另两处(不在 services/code_index/ 下):
```
src/hyperion/tools/code_nav.py            # [P1.5 已成] find_references / goto_definition / hover + _do_lsp_request(§5)
src/hyperion/cli.py                       # [P1.5 已成] `hyperion lsp health` / `hyperion lsp refs` 子命令
```

> 注:`code_graph.py`(原 P1.5 计划)**不建**——caller/callee 由 LSP 提供(§5);`repo_map.py`(Aider 式)**延后**记 backlog。

---

## 8. 风险与对策(精简)

| 风险 | 对策 |
|---|---|
| C 宏/条件编译/头文件解析不全 | tree-sitter 容错 + 一并索引 .h + ctags 补宏;精确导航走 clangd(P1.5) |
| embedding 模型锁定后想换 | config 锁定 + `model_fingerprint` 检测 → 变更触发全量重建 |
| embedding CPU 推理慢(首建) | 远端默认(免本地推理);本地档分批 embed + 进度;极端慢上 ONNX int8(backlog #9) |
| 全量索引慢 / 日常 stale | 增量:按 content_hash 只重嵌变化 chunk(§2.6) |
| LanceDB FTS 对 C 标识符召回弱 | `fts_text` 预拆词(snake/camel/SCREAMING)+ FTS 参数 `stem=False`/`remove_stop_words=False` |
| LanceDB 运行时兼容 | wheel 已覆盖 cp39-abi3(含 3.12)+ macOS arm64,无需编译;锁 `lancedb>=0.34,<0.36` |
| LSP 无 compile_commands(bluez/wpa) | P1.5 前置:建 compile_commands 生成脚本(`bear -- make`) |

---

## 9. 决策一览

| # | 决策 | 理由 | 出处 |
|---|---|---|---|
| 1 | embedding 远端 DashScope `text-embedding-v4` 默认 | 免下载/免 torch/代码为核心能力;本地 Qwen3-0.6B/bge-m3 作可选档 | §2.3 |
| 2 | reranker 远端 DashScope `qwen3-rerank` 默认 | 同 key/同价/CPU 本地不可交互;SiliconFlow 免费 fallback | §2.5 |
| 3 | 向量库 LanceDB 嵌入式,拒 Qdrant 收敛 | 本地优先/零 server/两台机一致/规模未到;`VectorStore` 接口留口子 | §2.4 + [向量库报告](../调研/向量数据库设计分析报告.md) |
| 4 | 多仓库 table-per-repo | 物理隔离、可单独 rsync/重建;LanceDB 多租户最佳实践 | §2.4 |
| 5 | grammar 包:每语言独立,弃 language-pack | 后者运行时从 GitHub 下载语法,国内网络 timeout | §2.1 |
| 6 | 切块取 cAST 的 split,不取跨符号 merge | chunk 兼任 read_function 单符号语义,合并会破坏;加模块级 chunk 兜底覆盖率 100% | §2.2 |
| 7 | chunk id = `{file}:{qualified_name}`,不含 start_line | 行号对重构太敏感(重排→全 id 变→全量重嵌) | §2.2 |
| 8 | 原子性分两路:LanceDB 目录 swap / SQLite BEGIN IMMEDIATE+WAL | 前者无事务用 rename 原子切;后者借 CRG 9 版本验证 | §2.6 |
| 9 | 评测行级映射 + 多指标 + 难度分层,弃单指标 0.6 | 单指标偏进取;行级映射独立于检索系统 | §3 |
| 10 | **P1.5 弃 code_graph,改 LSP/clangd** | clangd 取代自建调用图(绕开 C 三墙);图算法降级到 repo_map 延后 | §5 + [演进报告](../调研/后续设计演进报告-oh-my-pi与最佳实践.md) |
| 11 | **三层栈**:vector(L1)→LSP(L2)→DAP(L3),叠加非替代 | L2/L3 建在 L1 上;ChatDBG/omp 学术+工业印证 | §0 + [演进报告](../调研/后续设计演进报告-oh-my-pi与最佳实践.md) |
| 12 | **P1.4 搜索内核用纯 `re`**,不用 ripgrep | 零新系统依赖、Python 可移植、deer-flow `search.py` 已验证;大仓性能升级(rg subprocess)记 backlog | §4.2 + [backlog #1](../../.claude/memory/backlog-production-grade.md) |
| 13 | **grep_symbol / read_function 单列显式工具**(不学 omp 折进 read/ast_grep) | 复用现成 parser 零成本;显式工具对 agent 更直白(不依赖模型记得"无 selector=大纲"隐式约定) | §4.5 |
| 14 | **read BFS 摘要用 Symbol 粒度**(不学 omp 每 AST 节点) | 直接复用 P1.0 parser 的 start/end_line;语句级折叠更细但收益递减,记 backlog | §4.4 + [backlog #6](../../.claude/memory/backlog-production-grade.md) |
| 15 | **二进制守卫双保险**(NUL + fatal UTF-8,8192B),`:raw` 延后 | omp `binary.ts` + deer-flow 双印证;P1.4 用 bash 兜底,`:raw` 逃生舱记 backlog | §4.7 |

---

## 10. 参考

- 切块:[cAST (EMNLP 2025)](https://arxiv.org/html/2506.15655v1)
- 混合检索:[Hybrid Search Reference 2026](https://www.digitalapplied.com/blog/hybrid-search-bm25-vector-reranking-reference-2026) · [RRF explained](https://blog.serghei.pl/posts/reciprocal-rank-fusion-explained/) · [Balancing the Blend (arXiv 2508.01405)](https://arxiv.org/html/2508.01405v2)(weakest-link + TRF)
- embedding/rerank:[Qwen3-Embedding](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) · [bge-reranker-v2-m3](https://huggingface.co/BAAI/bge-reranker-v2-m3) · [CoIR 代码检索榜](https://github.com/coir-team/coir) · [Anthropic Contextual Retrieval](https://www.anthropic.com/engineering/contextual-retrieval)
- 向量库:[LanceDB Hybrid Search](https://docs.lancedb.com/search/hybrid-search) · [RRF Reranker](https://docs.lancedb.com/reranking/rrf) · 完整分析见 [向量数据库设计分析报告](../调研/向量数据库设计分析报告.md)
- 精确导航(P1.5):[multilspy](https://github.com/microsoft/multilspy) · [ChatDBG](https://github.com/plasma-umass/chatdbg)(LSP/调试器驱动 RCA 先验)
- 评测:[SWE-bench](https://www.swebench.com/) · [SWE-Bench Illusion (NeurIPS 2025)](https://arxiv.org/abs/2506.12286)
- 演进依据(三层栈 / Hashline / TTSR / advisor / 记忆 / scheme FS):[后续设计演进报告(oh-my-pi 与最佳实践)](../调研/后续设计演进报告-oh-my-pi与最佳实践.md) · [code-review-graph 调研](../调研/code-review-graph-调研与借鉴.md)
