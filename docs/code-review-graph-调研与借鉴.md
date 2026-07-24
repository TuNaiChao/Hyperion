# code-review-graph(CRG)调研与借鉴报告

> 状态:调研稿(v1,2026-07-24)· 调研对象:[tirth8205/code-review-graph](https://github.com/tirth8205/code-review-graph) v2.3.7(本地只读克隆 `code-review-graph/`,已 `.gitignore`)
> 关联:[P1 设计报告](p1-code-understanding-design.md) §6/§10/§11/§13 决策 #8、[backlog](../.claude/memory/backlog-production-grade.md) #10/#11/#12
> 目的:白纸黑字记录「调研过 CRG、借鉴了什么、没借鉴什么及为什么」,避免重复造轮子,也避免盲目照搬。

---

## 0. TL;DR(先给结论)

CRG 是 Hyperion P1.3–P1.5 + P2/P4 的「未来镜像」——它把 Hyperion 规划要做的事(SQLite 知识图谱、SHA-256 增量更新、blast-radius 影响分析、hybrid 检索、评测框架)**用 9 个 schema 版本打磨成了生产级**。但它和 Hyperion 的**取向不同**:CRG 面向「code review 省 token」(符号级、中小仓),Hyperion 面向「系统软件 Bug 定位 + 深度研究」(大 C 仓、多次检索、低延迟)。

| 层 | 借不借 | 一句话 |
|---|---|---|
| **向量库** | ❌ **不借** | CRG 用纯 SQLite+BLOB 线性扫,O(N),大仓不可用;Hyperion 用 LanceDB ANN 是对的 |
| **embedding provider** | 🟡 **部分借** | CRG 的 provider 抽象比我们更硬(index 校验/retryable/UA/身份),补进 backlog #12 |
| **retrieval(hybrid+RRF)** | 🟢 **借结构** | LanceDB 原生 hybrid 已覆盖;借 CRG 的 `_out_mode`/查询 boosting/三级降级 |
| **SQLite 图 + 增量 + 原子性** | 🟢 **大量借** | CRG 是生产级模板:WAL+BEGIN IMMEDIATE、schema、SHA-256 短路、N 跳依赖、networkx 缓存 |
| **eval 框架** | 🟢 **借 harness** | 注册表/统一签名/失败语义/循环论证警示;指标(CRg 太弱)自己写 |
| **工程惯例** | 🟢 **借** | parameterized SQL、`_sanitize_name` 防 prompt 注入、TOCTOU-safe 读文件、并发模型 |
| **parser 多语言** | ⏳ **待补** | 第 4 簇调研因 API 用量上限中断,留 P1.5 C 子调研补 |

---

## 1. CRG 是什么 · 技术栈 · 架构

**定位**:local-first、增量更新、为 token 高效 code review 服务的**知识图谱**,通过 MCP + CLI 暴露给 AI 编码工具(Claude Code / Cursor / Codex / Copilot 等)。宣称「6 个真实仓库 38x–528x token 缩减」「2900 文件 <2 秒增量重索引」「0.71 average impact F1」。

**技术栈**(`pyproject.toml`):
- 解析:Tree-sitter + **`tree-sitter-language-pack`**(40+ 语言,含 Jupyter/Databricks notebook)
- 存储:**单一 SQLite**(`.code-review-graph/graph.db`,WAL 模式)——图 + FTS5 + 向量 BLOB 全在一个文件
- 图:`networkx`(内存,PageRank/BFS)
- 检索:`search.py` FTS5 hybrid(keyword + vector)+ RRF
- embedding:可选(`sentence-transformers` / OpenAI 兼容 / Google Gemini / MiniMax)
- 社区:`igraph`(Leiden 算法)
- 服务:`fastmcp`(30 个 MCP 工具 + 5 个 prompt)
- 分发:PyPI 包 + GitHub Action(本地跑,不发源码到外部)

**核心架构**(来自 `CLAUDE.md`):

| 文件 | 职责 |
|---|---|
| `parser.py` | Tree-sitter 多语言 AST 解析 + 针对性 fallback |
| `custom_languages.py` | `languages.toml` 配置驱动加语言(免 fork) |
| `graph.py` | SQLite 图存储(nodes/edges/BFS 影响分析) |
| `incremental.py` | git 变更检测 + 文件 watch + SHA-256 增量 |
| `embeddings.py` | 多 provider 向量(可选) |
| `search.py` | FTS5 hybrid 检索 |
| `flows.py` | 执行流检测 + 关键性打分 |
| `communities.py` | Leiden 社区检测 + 架构概览 |
| `changes.py` | risk-scored 变更影响分析 |
| `refactor.py` | rename 预览 / 死代码 / 重构建议 |
| `eval/` | 评测框架(7 个 benchmark) |
| `migrations.py` | schema 迁移 v1–v9 |

**调研范围**:4 簇并行深读——① 检索/store/embedding ② graph+增量+原子性 ③ eval 框架 ④ parser+多语言+flows。前 3 簇完成;第 4 簇因 API 5 小时用量上限(429)中断,留 P1.5 C 解析子调研补。

---

## 2. 簇 1:检索 / 存储 / embedding

### 2.1 CRG 怎么存向量——纯 SQLite + BLOB,**无向量库**

- schema(`embeddings.py:763-770`):`embeddings(qualified_name TEXT PK, vector BLOB, text_hash, provider)`。
- 编解码:`struct.pack(f"{n}f", *vec)` ↔ `struct.unpack`,float32 成 bytes。
- 检索(`embeddings.py:964-989`)= **全表线性扫 + 手写 Python cosine**(`_cosine_similarity` 逐元素 sum/zip,连 numpy 都不用)。
- FTS5(`search.py:48-54`):`nodes_fts USING fts5(name, qualified_name, file_path, signature, content='nodes', content_rowid='rowid', tokenize='porter unicode61')`——外部内容表共享 rowid,不重复存。
- **原子重建**(`search.py:41-63`):`BEGIN IMMEDIATE` 包 DROP+CREATE+`INSERT ... VALUES('rebuild')`(FTS5 原生重建),防崩溃后 FTS 表丢失。
- **FTS 注入防护**(`search.py:191-192`):用户查询用 `"..."` 包裹、内部 `"` 转义为 `""`,挡 `OR`/`NOT`/`*` 操作符注入。
- hybrid(`search.py:308-466`)= FTS5 BM25 + 向量 + **RRF k=60**(`search.py:150-173`,与 Hyperion 设计同构)+ **三级降级**(hybrid→仅 FTS→仅向量→keyword LIKE,`_out_mode` 记录走了哪条)。
- **查询类型 boosting**(`search.py:75-142`):PascalCase→Class ×1.5、snake_case→Function ×1.5、dotted→qualified ×2.0、标识符命中 ×2.0。
- **context-file boosting**(`search.py:431-432`):当前编辑文件的节点 ×1.5。

### 2.2 CRG 的 embedding provider 抽象(**比 Hyperion 更生产级**)

`EmbeddingProvider` ABC(`embeddings.py:46-64`):`embed` / `embed_query`(query/document 分离)/ `dimension` / `name`。四个实现:Local(sentence-transformers)、Google Gemini、MiniMax、**OpenAIEmbeddingProvider**(最重要)。

`OpenAIEmbeddingProvider`(`embeddings.py:337-600`)的 **7 个生产级细节**,Hyperion `RemoteEmbedder` 应全盘吸收(已记 backlog #12):
1. **provider 身份含 endpoint+model**(`embeddings.py:380-417`):`name=f"openai:{model}@{host_key}"`,host_key 规范化 userinfo/默认端口/trailing slash 但保留 scheme+path。不同 endpoint 同名模型权重不同,混用静默污染向量空间。
2. **响应 index 三分支校验**(`embeddings.py:506-534`):全有 index→0..N-1 置换校验;全无→仅校验 count;混合→拒绝。DashScope/LiteLLM 网关乱序/丢项硬防御。
3. **精确 retryable 分类**(`embeddings.py:541-571`):RemoteDisconnected/IncompleteRead/BadStatusLine/ssl.SSLError/socket.timeout。
4. **4xx body 透传**(`embeddings.py:454-483`):HTTP 400 解析 JSON error 抛真实原因。
5. **可配 batch size**(`embeddings.py:357`):测试注释**点名 "text-embedding-v4 caps batch at 10"**——正是 DashScope 限制。
6. **CRG_OPENAI_DIMENSION**:仅显式指定才转发 `dimensions`,否则不传。
7. **自定义 User-Agent**(`embeddings.py:36-39`):Cloudflare 后端 403 拒 `Python-urllib`。

**失败处理**:本地模型懒加载 + 进程级缓存 + RLock(`embeddings.py:81-159`),失败不污染缓存;未知 provider 硬错(`embeddings.py:677-682`);云 provider 首次用走 **stderr 警告**(不污染 MCP stdio)。

### 2.3 嵌入粒度 + 嵌入文本构造

- **粒度**:每个符号节点(函数/类/方法),**排除 File 节点**(`embeddings.py:929-931`)。
- **嵌文本构造 `_node_to_text`**(`embeddings.py:818-878`):parent 点号名 + 裸名 + `_split_identifier` 拆 snake/camel/dotted + kind + "in Parent" + params + docstring(**400 字符上限**)+ 父目录 + 语言。
- docstring 是跨语言一等公民(`test_docstring_embeddings.py` 验证 9 种语言提取规则:Python 运行时字符串首段、JSDoc 紧贴 export、Go `//` 拼接剔 `//go:noinline`、Javadoc 剔 `@param`、Doxygen `@brief` 等)。

### 2.4 簇 1 可借鉴(带文件:行)

| # | 借鉴项 | CRG 源 | 对 Hyperion |
|---|---|---|---|
| 1 | provider 身份含 endpoint+model | `embeddings.py:380-417` | 进 fingerprint,切 provider 自动重嵌 |
| 2 | 响应 index 三分支校验 | `embeddings.py:506-534` | DashScope 网关乱序/丢项防御 |
| 3 | retryable 精确分类 + 4xx 透传 + UA | `embeddings.py:541-571,454-483,36-39` | backlog #12 |
| 4 | RRF k=60 简洁实现 | `search.py:150-173` | 与设计同构 |
| 5 | `_out_mode` 可观测字段 | `search.py:326-337,373-389` | retrieval.py 每个 query 知道走了哪条路 |
| 6 | 查询类型 boosting | `search.py:75-142` | C 场景 `wpa_supplicant_add_iface` 受益 |
| 7 | context-file boosting ×1.5 | `search.py:431-432` | Bug-RCA「刚打开的崩溃文件」 |
| 8 | 三级降级 + 失败语义 | `search.py:366-389` | 优雅降级 |
| 9 | `embed_query` vs `embed` 分离(task_type) | `embeddings.py:48-54` | DashScope v4 支持 task_type |
| 10 | `_split_identifier` + 400 字 docstring 上限 | `embeddings.py:800-815,861` | fts_text/嵌文本构造 |

### 2.5 簇 1 **不**借鉴

- **SQLite BLOB + Python cosine 线性扫**:十万级 C chunk 不可用;Hyperion 用 LanceDB ANN。
- **每次 search 重开 EmbeddingStore**(`search.py:229-244`):CRG short-lived 连接;Hyperion 长持 LanceDB table 句柄。
- **`memory.py`**:只是 markdown+YAML 文件,**不是向量记忆**(别误读成 Hyperion 的 Memorize)。
- **MiniMax / Gemini provider**:过度工程,Hyperion 走 DashScope+本地两条已够。

---

## 3. 簇 2:graph + 增量 + 原子性(**最值得借的一簇**)

### 3.1 SQLite 图 schema(`graph.py:42-88`)

```sql
-- nodes:业务主键 qualified_name UNIQUE;file_hash 每节点冗余(增量跳过整文件);extra JSON 扩展列
CREATE TABLE nodes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, name TEXT,
  qualified_name TEXT NOT NULL UNIQUE, file_path TEXT NOT NULL,
  line_start INTEGER, line_end INTEGER, language TEXT, parent_name TEXT,
  params TEXT, return_type TEXT, modifiers TEXT, is_test INTEGER DEFAULT 0,
  file_hash TEXT, extra TEXT DEFAULT '{}', updated_at REAL NOT NULL);
-- edges:9 种 kind(CALLS/IMPORTS_FROM/INHERITS/REFERENCES/IMPLEMENTS/CONTAINS/TESTED_BY/DEPENDS_ON/...)+ confidence(v9)
CREATE TABLE edges (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
  source_qualified TEXT NOT NULL, target_qualified TEXT NOT NULL,
  file_path TEXT NOT NULL, line INTEGER DEFAULT 0, extra TEXT,
  confidence REAL DEFAULT 1.0, confidence_tier TEXT DEFAULT 'EXTRACTED', updated_at REAL);
```
索引:file/kind/qualified(source/target/kind 复合/file + v8 `(kind,source,target,file,line)` 复合加速 upsert 存在性检查)。比 Hyperion 规划的 `calls(caller_id,callee_id,file,line)` **丰富得多**(整型 PK 外键、9 种边、confidence、JSON 扩展列)。

### 3.2 增量更新算法(`incremental.py`)

- **SHA-256 短路**(`incremental.py:1058-1066`):读 bytes 一次拿 hash 再 parse(TOCTOU-safe),hash 存每个 node 行的 `file_hash` 列,`existing_nodes[0].file_hash == fhash` 即跳过整文件。
- **N 跳依赖追踪**(`incremental.py:799-875`):BFS,max 2 跳 / max 500 文件 / `DependentList.truncated` 标志(防 hub 节点爆炸,issue #261)。
- **「2900 文件 <2 秒」四招**:① `git diff --name-only -z` + `git ls-files`(不 walk FS,NUL 分隔)② SHA-256 短路 ③ **并行 parse 串行写**(ProcessPoolExecutor min(cpu,8),tree-sitter 释放 GIL;SQLite 单写者)④ 每文件 `BEGIN IMMEDIATE` 原子事务(删旧+插新)。
- **MCP stdio 切 ThreadPoolExecutor**(`incremental.py:34-58`):避免子进程继承 stdio 管道死锁(issue #46/#136)。

### 3.3 原子性 / 事务(**改 Hyperion §10 的关键依据**)

三层保证(`graph.py`):
- **连接级**(`graph.py:158-164`):`isolation_level=None`(禁 Python 隐式事务)+ `journal_mode=WAL` + `busy_timeout=5000`。
- **操作级**(`graph.py:281-296`):`store_file_nodes_edges` 用 `BEGIN IMMEDIATE` 删旧+插新原子化,`try/except BaseException: rollback`。
- **嵌套防御**(`graph.py:272-279`):`_begin_immediate` 若 `in_transaction` 先 rollback 脏事务再 BEGIN。

> **对 Hyperion 的影响**:Hyperion §10 原规划「temp 表 + 原子 rename」。CRG 用 BEGIN IMMEDIATE+WAL 更适合增量(粒度单文件/批次、增量友好、长重建期间可读 via WAL、复杂度低),经 9 个 schema 版本 + `test_transactions.py` 验证。**决策 #8:P1.5 SQLite code_graph 抛弃 temp+rename,改用 CRG 方案**;P1.3 LanceDB(无事务)全量重建用目录 swap,增量用 merge_insert。

### 3.4 节点 ID 稳定性

- CRG `qualified_name`(`graph.py:1556-1561`)= `{file_path}::{parent}.{name}`,**不含行号**。改名/移动靠 file_hash+file_path 双重身份 + 反向依赖追踪回填,不留孤儿。
- **对 Hyperion**:原 chunk id `f"{file}:{qualified_name}:{start_line}"` 含行号,**对重构太敏感**(重排函数→全 id 变→全量重嵌)。**决策 #8:改 `{file}:{qualified_name}`(+`:p{N}`),行号作普通列。**

### 3.5 其他可借鉴

- **networkx 缓存 + 每次写后 `_invalidate_cache()`**(`graph.py:1531-1554,188-191`)。
- **`load_flow_adjacency()` 批量 load 邻接表进内存**(`graph.py:1493-1527`):50 万节点/300 万边 ~几百 MB,避免亿级点查(PageRank 用)。
- **schema 迁移框架**(`migrations.py:16-66`):`get_schema_version`/`_has_column`/`_table_exists` + 逐版本 commit + `IF NOT EXISTS` 幂等。
- **`metadata(key,value)` 表**:存 schema_version/last_updated/git_head_sha。
- **impact_radius SQL BFS**(`graph.py:771-967`):SQLite temp 表 + batch 450(避 999 变量上限),有界 BFS——P2 Bug-RCA 直接相关。
- **`changes.py` PR 影响流水线**(`parse_diff_ranges→map_changes_to_nodes→compute_risk_score→analyze_changes`)——P4 PR-Tracker 可几乎原样移植。

### 3.6 簇 2 不借鉴 / 改造

- `flows.py` + `communities.py`(Leiden):推后(Hyperion 规模/场景不需要)。
- `risk_index`/`community_summaries`/`flow_snapshots` 物化视图:规模未到。
- Spring/ReScript/Temporal/HCL resolver:非 Hyperion 领域(但「post-pass 修正跨文件解析」架构模式可借鉴)。
- 边端点用字符串而非整型外键:允许 target 暂未解析(bare name),代价是 JOIN 慢——Hyperion C 项目弱符号/宏解析不全,可保留字符串端点 + `target_resolved` 标志。
- **存绝对路径**:Hyperion 多机(Linux+macOS)应**存相对路径** + 单独 repo_root。

---

## 4. 簇 3:eval 框架

### 4.1 整体设计(`eval/runner.py`)

- **注册表 + 统一签名**(`runner.py:28-36`):`BENCHMARK_REGISTRY` dict,每个 benchmark 是 `run(repo_path, store, config) -> list[dict]` 函数(非类)。加 benchmark = 写 `run` + 注册一行。
- **异常隔离**(`runner.py:205-221`):try/except 包每个 bench,挂了写空 list 不影响其他。
- **可复现性校验**(`runner.py:48-59`):`config["commit"] == test_commits[-1]["sha"]`,防 shallow clone 漏 SHA 静默 fallback。
- **全量 clone 禁 `--depth`**(`runner.py:80-132`)+ 每个 subprocess 校验 returncode。
- **scorer.py 是纯函数库**(不维护状态),reporter.py 只读 CSV 后处理成 markdown 表(runner→CSV→reporter 分离)。

### 4.2 各 benchmark

| benchmark | 评什么 | ground truth | 对 Hyperion |
|---|---|---|---|
| **search_quality**(`search_quality.py:1-59`) | top-20 能否找到 expected,记 rank/RR | 人工 YAML `query/expected` | 形状可借,但**只单标签 MRR + 模糊匹配**(易虚高),Hyperion 多符号行级映射更先进 |
| **impact_accuracy**(`impact_accuracy.py:1-221`)★★★ | blast-radius 预测精度 | **双模式**:graph-derived(循环上界)/ co-change(同 commit 其他文件,独立) | **设计最成熟,首要参考** |
| **multi_hop_retrieval**(`multi_hop_retrieval.py:1-126`) | NL query→anchor→traversal 两步 | 人工 `multi_hop_tasks`,`score=anchor_found*neighbor_recall` | L3 跨模块档 task schema 可借 |
| flow_completeness | 入口点检出率 | 人工 `entry_points` | 不适用 |
| token_efficiency / build_performance / agent_baseline | token 比 / 构建耗时 / grep 基线对比 | — | agent_baseline 的「多 baseline 并列」范式可借 |

### 4.3 关键方法论教训(最值得抄)

1. **失败语义 + 回归测试**(`impact_accuracy.py:14-18` docstring + `tests/test_eval.py:634-719`):挂了 `status="error"`,**绝不默认 recall=0/1**(历史 bug 是失败时 `predicted=set(changed)` 保 recall=1.0 假胜利,已用回归测试钉死)。在 docstring 记历史 bug + 钉死回归 = 工程纪律。
2. **循环论证警示**(README/REPRODUCING 反复强调):graph-derived 金标是「circular upper bound,不是 100% recall」。**Hyperion §11 禁止用「图 caller」当金标去评「检索找 caller」**;金标必须来自 git diff(独立)。CSV 加 `ground_truth_mode` 列。
3. **不引用未测数字**:co-change 模式没测就明说「we do not quote them before measuring」。

### 4.4 簇 3 **不**借鉴(CRG 太弱,Hyperion 自己写)

- **scorer 缺 Recall@k / Precision@k / nDCG / 多标签 MRR**(只有 set-based P/R + 单标签 MRR)——Hyperion §11 自己实现(BEIR 标准)。
- **无 L1/L2/L3 难度分档**——Hyperion 自己设计(Rouge-1 分桶 + 描述方式)。
- **无负例 / precision 测试**——Hyperion 的 confuser 负例是补强。
- search_quality 模糊匹配 `exp in qn or qn in exp` 易虚高(`"Client"` 命中 `"BaseClient"`)——Hyperion 用精确符号匹配。

---

## 5. 簇 4:parser + 多语言 + flows(**⏳ 待补**)

调研因 API 用量上限(429)中断。已知值得后续看的:
- CRG 用 `tree-sitter-language-pack`(Hyperion 弃用,国内下载被墙)——看它怎么解决下载问题,以及 `languages.toml` 数据驱动加语言机制是否值得借鉴。
- 各语言(尤其 C/C++)的 tree-sitter 节点类型表(function/class/import/call)——Hyperion parser 的 GRAMMARS 注册表能抄哪些。
- 调用解析:Python 用 `jedi`(`jedi_resolver.py`)比纯 tree-sitter call_expression 准在哪;C 的 call 抽取(对 P1.5 宏包装/函数指针难点有启发)。
- blast-radius 算法(`analysis.py`/`flows.py`)。

**留 P1.5 C 解析子调研补**(架构 §3 本就规划了 P1.5 前出 C 解析子调研)。

---

## 6. 向量库对比:CRG SQLite-BLOB vs Hyperion LanceDB

> 回应「要不要改用 CRG 的向量库设计」——**不改**。

| 维度 | CRG(纯 SQLite + BLOB) | Hyperion(LanceDB 嵌入式) |
|---|---|---|
| 向量检索复杂度 | **O(N) 全表扫 + Python cosine** | **O(log N) IVF/HNSW ANN** |
| 大仓(几万~几十万 chunk) | 单查询几秒,交互式 agent 不可用 | 亚秒级 |
| 设计取向 | code review 省 token(符号级,中小仓) | Bug 定位/深度研究(大 C 仓,多次检索,低延迟) |
| hybrid | 自搓 FTS5+向量+RRF+降级 | 原生 hybrid+RRF+reranker 集成(少造轮子) |
| 运维 | 单文件、零额外依赖 | 嵌入式(文件化、可 rsync),多一个库 |
| local-first | ✅ | ✅(同为嵌入式,未失) |

**结论**:CRG 的向量层是为「中小仓 code review」做的取舍(线性扫换简化),不适合 Hyperion 的「大系统软件 + 低延迟多次检索」。**向量库保持 LanceDB**(store.py 已 live 验证);CRG 值得抄的是 **SQLite 图层**(P1.5)和工程惯例,不是向量存储。

---

## 7. 可借鉴总清单(按 Hyperion 阶段 + 优先级)

| # | 借鉴项 | CRG 源 | 阶段 | 优先级 |
|---|---|---|---|---|
| 1 | 原子性:SQLite 用 BEGIN IMMEDIATE+WAL(弃 temp+rename) | `graph.py:158-164,272-296` | P1.5 图 / 重审 §10 | ⭐⭐⭐ |
| 2 | SQLite 图 schema(nodes qualified UNIQUE+file_hash+extra;edges 9 kind+confidence) | `graph.py:42-88` | P1.5 | ⭐⭐⭐ |
| 3 | embedding/reranker provider 硬化(index 校验/retryable/UA/身份) | `embeddings.py:380-600` | backlog #12 | ⭐⭐⭐ |
| 4 | retrieval 三件补强(`_out_mode` + 查询 boosting + 三级降级) | `search.py:75-142,308-466` | P1.3 retrieval | ⭐⭐⭐ |
| 5 | eval harness 骨架(注册表+统一签名+异常隔离+失败语义+回归测试) | `eval/runner.py`,`tests/test_eval.py` | P1.3 eval | ⭐⭐⭐ |
| 6 | eval 循环论证警示(金标独立于被测系统 + 双模式并列) | `eval/benchmarks/impact_accuracy.py` | P1.3 eval | ⭐⭐⭐ |
| 7 | 增量:`git diff --name-only` + SHA-256 短路 + N 跳依赖(≤2跳/≤500/truncated) | `incremental.py:799-875,1058` | P1.3 index / P1.5 | ⭐⭐ |
| 8 | 并行 parse 串行写(ProcessPool;MCP stdio 切 ThreadPool) | `incremental.py:25,34-58,951-977` | P1.3 index | ⭐⭐ |
| 9 | `_split_identifier` + 400 字 docstring 上限 | `embeddings.py:800-815,861` | P1.3 fts_text | ⭐⭐ |
| 10 | networkx 缓存 + 写后失效 + 批量 load 邻接表(PageRank) | `graph.py:1531-1554,1493-1527` | P1.5 | ⭐⭐ |
| 11 | schema 迁移框架(get/_has_column/_table_exists + IF NOT EXISTS 幂等) | `migrations.py:16-66` | P1.5 | ⭐⭐ |
| 12 | PR 影响分析流水线(parse_diff_ranges→map→risk→analyze) | `changes.py` | P4 PR-Tracker | ⭐⭐ |
| 13 | impact_radius SQL BFS(temp 表 batch 450) | `graph.py:771-967` | P2 Bug-RCA | ⭐⭐ |
| 14 | chunk id 去 start_line(重构稳健性) | `graph.py:1556-1561` | P1.1 回填 | ⭐⭐ |
| 15 | 工程惯例(parameterized SQL / `_sanitize_name` 防 prompt 注入 / TOCTOU-safe 读文件) | `CLAUDE.md` 安全不变式 | 全局 | ⭐⭐ |

---

## 8. 已落地到哪里

- **设计文档**([p1-code-understanding-design.md](p1-code-understanding-design.md)):§4(chunk id)、§6(store/retrieval 真实 API + 补强)、§9(config retrieval/reranker)、§10(原子性重审)、§11(eval 多指标 + 循环论证 + 污染警示 + harness)、§13 决策 #8、§15 参考。
- **backlog**([backlog-production-grade.md](../.claude/memory/backlog-production-grade.md)):#10 TRF、#11 CoSQA+ 自动金标、#12 provider 硬化。
- **代码**:P1.3 store.py(已 live 验证 LanceDB 0.34 API);index.py / retrieval.py / eval 待敲。
- **待补**:簇 4(parser/多语言/flows)→ P1.5 C 子调研。

---

## 9. 参考文件路径速查(本地 `code-review-graph/`)

| 主题 | 文件 |
|---|---|
| 向量存储(纯 SQLite BLOB) | `code_review_graph/embeddings.py:763-770,964-989` |
| embedding provider 抽象 | `code_review_graph/embeddings.py:46-64,337-600` |
| hybrid 检索 + RRF + boosting | `code_review_graph/search.py:48-54,75-142,150-173,308-466` |
| SQLite 图 schema | `code_review_graph/graph.py:42-88` |
| 原子性(WAL+BEGIN IMMEDIATE) | `code_review_graph/graph.py:158-164,272-296` |
| 增量(SHA-256 + N 跳依赖) | `code_review_graph/incremental.py:799-875,1003-1147` |
| schema 迁移框架 | `code_review_graph/migrations.py:16-66` |
| impact_radius BFS | `code_review_graph/graph.py:771-967` |
| eval runner(注册表/失败语义) | `code_review_graph/eval/runner.py`,`tests/test_eval.py:634-719` |
| eval 双 GT 模式(循环论证) | `code_review_graph/eval/benchmarks/impact_accuracy.py` |
| PR 影响分析流水线 | `code_review_graph/changes.py` |
| 架构总览 | `CLAUDE.md`、`docs/LLM-OPTIMIZED-REFERENCE.md` |
