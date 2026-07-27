# P1 设计:代码理解服务(`services/code_index/`)

> **状态**:P1.0–P1.3 已成(主退出标准 **L2 recall@5 = 0.65 达标**);P1.4 / P1.5 待做。本文档随进展更新。
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
- P1.4:agent 能用导航工具(grep_symbol/read_function/search_code)定位函数。
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

## 4. 待做 · P1.4:导航工具 + 平台护栏

> **面向小白**:L1 检索是个函数 `retrieve(query)`,但 agent 需要几个**工具**来用它(像 IDE 的"转到定义""查找引用")。同时把 P0 的 read/grep 升级一下(read 别 dump 全文、grep 支持正则)。这些都是半天到几天的小改,ROI 高,不卡阶段。

| 子任务 | 目标 | 接口要点 | 退出标准 |
|---|---|---|---|
| **`code_nav` 三工具** | 把 L1 检索包成 agent 可调用的 @tool | `grep_symbol(name)`→`file:line` 列表;`read_function(symbol,file=None)`→代码+元数据;`search_code(query)`→top-k chunk | demo agent 能用导航工具定位函数 |
| **read = tree-sitter 摘要** | read 不 dump 全文,折叠函数体 | 显式 selector(`:N`/`:N-M`)走 verbatim 绕过摘要;无 selector 才 BFS unfold 摘要(目标可见行 50,硬上限 100);**elision footer 必加**(末尾给真实 selector 举例捞回正文) | read 大文件信息密度提升、不丢可恢复性 |
| **grep 升级** | 字面子串→正则 + ignore + 守卫 | 正则(`re`)+ ignore(`pathspec`+内建 skip)+ **二进制守卫**(8192B sniff)+ FS 扫描缓存(TTL 1s + 空结果 200ms 重检 + 写后失效) | grep 不被二进制/缓存拖累,对应 [backlog #1](../../.claude/memory/backlog-production-grade.md) |
| **二进制守卫** | read/grep 共用,挡二进制文件 | `is_probably_binary(path)`:前 8192 字节含 NUL 或非 UTF-8 即判二进制,拒绝并提示 `:raw` | 二进制文件不再毁终端/烧 context |

**声明式接入**:`config.yaml -> tools` 加 3 条 `use: hyperion.tools.code_nav:xxx_tool`,registry 自动加载(P0 已验证)。借鉴 oh-my-pi read/grep + [backlog #1](../../.claude/memory/backlog-production-grade.md)。

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

tools:                                 # P1.4 追加 3 条导航工具
  - { name: grep_symbol,  group: code, use: hyperion.tools.code_nav:grep_symbol_tool }
  - { name: read_function, group: code, use: hyperion.tools.code_nav:read_function_tool }
  - { name: search_code,  group: code, use: hyperion.tools.code_nav:search_code_tool }
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
├── (code_nav 工具)  # [P1.4]  src/hyperion/tools/code_nav.py:grep_symbol/read_function/search_code
└── (lsp)            # [P1.5]  src/hyperion/services/code_nav/lsp.py(暂定):clangd 经 multilspy
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

---

## 10. 参考

- 切块:[cAST (EMNLP 2025)](https://arxiv.org/html/2506.15655v1)
- 混合检索:[Hybrid Search Reference 2026](https://www.digitalapplied.com/blog/hybrid-search-bm25-vector-reranking-reference-2026) · [RRF explained](https://blog.serghei.pl/posts/reciprocal-rank-fusion-explained/) · [Balancing the Blend (arXiv 2508.01405)](https://arxiv.org/html/2508.01405v2)(weakest-link + TRF)
- embedding/rerank:[Qwen3-Embedding](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) · [bge-reranker-v2-m3](https://huggingface.co/BAAI/bge-reranker-v2-m3) · [CoIR 代码检索榜](https://github.com/coir-team/coir) · [Anthropic Contextual Retrieval](https://www.anthropic.com/engineering/contextual-retrieval)
- 向量库:[LanceDB Hybrid Search](https://docs.lancedb.com/search/hybrid-search) · [RRF Reranker](https://docs.lancedb.com/reranking/rrf) · 完整分析见 [向量数据库设计分析报告](../调研/向量数据库设计分析报告.md)
- 精确导航(P1.5):[multilspy](https://github.com/microsoft/multilspy) · [ChatDBG](https://github.com/plasma-umass/chatdbg)(LSP/调试器驱动 RCA 先验)
- 评测:[SWE-bench](https://www.swebench.com/) · [SWE-Bench Illusion (NeurIPS 2025)](https://arxiv.org/abs/2506.12286)
- 演进依据(三层栈 / Hashline / TTSR / advisor / 记忆 / scheme FS):[后续设计演进报告(oh-my-pi 与最佳实践)](../调研/后续设计演进报告-oh-my-pi与最佳实践.md) · [code-review-graph 调研](../调研/code-review-graph-调研与借鉴.md)
