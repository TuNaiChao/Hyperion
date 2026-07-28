# P1 设计:代码理解服务(`services/code_index/`)

> **状态**:P1.0–P1.4 已成(P1.4 导航工具 6/6 验证通过,2026-07-28);P1.5 待做。本文档随进展更新。
> **目标代码库**:bluez / wpa_supplicant 等 Linux C 组件(P1 以 Python/deer-flow 起步把管线跑通,C 专属难点留到 C 场景)。
> **总纲**:[architecture.md §5.1](architecture.md#51-代码理解服务-servicescode_index);**演进依据**:[后续设计演进报告(oh-my-pi 与最佳实践)](../调研/后续设计演进报告-oh-my-pi与最佳实践.md)。

---

## 0. 这层是干什么的(先讲大白话)

### 为什么需要它
P0 给了 agent "能跑命令/读文件" 的通用能力,但**不懂代码结构**。P2 Bug-RCA 要"定位根因到具体函数",P4 PR-Tracker 要"评估一个 PR 的影响面"——两者都要求 agent 能像 IDE 一样在大型 C 代码库里**按符号导航 + 语义检索**,而不是 `grep` 全文。P1 就是这个共同地基。

### 三层代码智能栈(理解本服务的主线)

> **面向小白的类比——理解代码像查一栋大楼,分三层,叠加不替代:**

| 层 | 比喻 | 你问什么 | 对应 | 状态 |
|---|---|---|---|---|
| **L1 向量检索** | 大楼的"语义索引" | "找处理蓝牙断连的地方"(按**意思**模糊匹配) | `services/code_index/`(本文档) | **P1.0–P1.3 已成** |
| **L2 LSP/clangd** | 大楼的"精确导航"(像 IDE) | "**谁调用**了 `disconnect_cb`"(精确到每一处,连宏/跨文件/系统头) | P1.5 新增 | 待做 |
| **L3 DAP/lldb·gdb** | 大楼的"现场勘查" | "进程崩在这,此刻这个变量值是多少、栈是什么" | P2 新增 | 待做 |

三层叠加:L1 先定位到大概哪个模块 → L2 精确串调用链 → L3 验证现场。**本服务当前覆盖 L1(已成);L2 在 P1.5 接入;L3 留 P2。**

### 退出标准
- **P1.3(已成)**:L2(语义查询)recall@5 ≥ 0.55 —— 实测 **0.650 达标**(见 §3)。
- **P1.4(已成)**:agent 能用导航工具(grep_symbol/read_function/search_code/grep)+ read BFS 摘要 + 二进制守卫 —— 6/6 验证通过(见 §4.9)。
- P1.5:精确 caller/callee 经 LSP 可查。

### 选型一览(P1 基础全部建在外部 SOTA 上)
| 能力 | 选型 |
|---|---|
| 解析 | tree-sitter + 每语言独立 grammar 包(`tree-sitter-python`,后续 `tree-sitter-c`);ctags 补宏(C 场景) |
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
    kind: str                 # function | method | class(C 再加 struct/macro/typedef/enum)
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
- ⚠️ **weakest-link**:RRF 不是越多路越好([Balancing the Blend, arXiv 2508.01405](https://arxiv.org/html/2508.01405v2));加第 3 路前必须单路 eval 消融,弱路径砍掉。TRF(ColBERT MaxSim 当融合器)作 P2 精度升级([backlog #10](../../.claude/memory/backlog-production-grade.md))。

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

### 落地
- **经 `multilspy`**(微软开源 Python LSP **client** 库,内部处理 stdio JSON-RPC + initialize 握手 + 文件同步)。**不要用 pygls**(那是写 server 的)。
- **三件套工具**:`lsp_references(file,line,symbol)`(精确调用点,带 2 次重试 + 250ms 退避防索引未完成)、`lsp_definition(file,line,symbol)`(跳定义,含系统头)、`lsp_hover(file,line,symbol)`(签名/宏展开/枚举值)。定位统一 `file+line+symbol`。
- `get_callers/get_callees` 由 `lsp_references` 提供;`CodeChunk.callers/callees` 字段不再回填(P1.4 清理时可移除,默认空 tuple 无害)。
- **硬前提**:`compile_commands.json`。bluez(autotools):`bear -- make`;systemd(cmake):`cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON`。没有它 clangd references 质量骤降。

**退出标准**:对带 compile_commands 的 C 仓,`lsp_references` 返回精确调用点(对比 L1 向量召回,漏召显著降低);demo agent 能串调用链。

> L3(DAP 调试器,attach/读栈/读变量)留 **P2** Bug-RCA(可复现 bug 现场深挖);TTSR 流式规则 / advisor 副驾 / Hashline 补丁等护栏也留 P2。详见 [后续设计演进报告](../调研/后续设计演进报告-oh-my-pi与最佳实践.md)。

---

## 6. 配置(`config.yaml`,P1.2–P1.3 已落地)

```yaml
code_index:
  repo: hyperion                       # P1.4:search_code 查的表名(须与 build_index 一致;缺省回退 workspace 目录名)
  embedding:                           # embed.py(P1.2 已落地:远端 OpenAI 兼容默认)
    provider: openai_compatible        # 远端默认(免下载/免 torch);本地可选 sentence_transformers
    base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
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
    base_url: https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank
    api_key: $DASHSCOPE_API_KEY        # 与 embedding 同一个 key
    model: qwen3-rerank
    rerank_top_n: 5

tools:                                 # P1.4 追加 4 条(grep_symbol/read_function/search_code + grep);read_file 已在(升级不改名)
  - { name: grep_symbol,  group: code,      use: hyperion.tools.code_nav:grep_symbol_tool }
  - { name: read_function,group: code,      use: hyperion.tools.code_nav:read_function_tool }
  - { name: search_code,  group: code,      use: hyperion.tools.code_nav:search_code_tool }
  - { name: grep,         group: file:read, use: hyperion.tools.sandbox:grep_tool }
```

---

## 7. 模块文件布局

```
src/hyperion/services/code_index/
├── __init__.py
├── parser.py        # [P1.0 已成]  tree-sitter:Symbol 抽取(含 .h)
├── chunker.py       # [P1.1 已成]  符号边界切块 + fts_text 分词
├── embed.py         # [P1.2 已成]  embedding(远端默认/本地可选)
├── store.py         # [P1.2 已成]  LanceDB 表(schema/BTree+FTS 索引/merge_insert/查询)+ VectorStore 接口
├── index.py         # [P1.2 已成]  编排:walk→parse→chunk→embed→store(+ 原子性 + 增量,§2.6)
├── retrieval.py     # [P1.3 已成]  混合检索(BM25+向量+RRF+rerank)
└── eval/            # [P1.3 已成]  scorer.py(指标)+ runner.py(harness)
# 待做:
├── outline.py       # [P1.4 已成]  tree-sitter BFS 摘要 + elision footer(复用 parser,§4.4)
└── (lsp)            # [P1.5]  src/hyperion/services/code_nav/lsp.py(暂定):clangd 经 multilspy
```

P1.4 还涉及另两处(不在 services/code_index/ 下):
```
src/hyperion/tools/code_nav.py            # [P1.4 已成] grep_symbol / read_function / search_code + _retrieval_bundle
src/hyperion/tools/sandbox.py             # [P1.4 已成] read_file 升级(BFS 摘要)+ 新增 grep_tool
src/hyperion/platform/sandbox/_search.py  # [P1.4 已成] 搜索内核(IGNORE_PATTERNS/is_probably_binary/find_grep_matches)
src/hyperion/platform/sandbox/local.py    # [P1.4 已成] grep() 用 _search 内核;read_file() 加二进制守卫
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
