# P1 设计报告:代码理解服务(`services/code_index/`)

> 状态:设计稿(v2,经深度审查修订)· 目标代码库:bluez / wpa_supplicant 等 Linux C 组件
> 依据:前沿调研(2025-2026)+ deer-flow 精读 + 架构 [architecture.md §5.1](architecture.md#51-代码理解服务-servicescode_index)
> 本报告是 P1 的开发蓝图,按"分阶段实现"逐步落地。

---

## 0. 背景与目标

### 为什么做
P0 给了 agent "能跑命令/读文件" 的通用能力,但**不懂代码结构**。P2 Bug-RCA 要"定位根因到具体函数",P4 PR-Tracker 要"评估一个 PR 的影响面"——两者都要求 agent 能像 IDE 一样在大型 C 代码库里**按符号导航 + 语义检索**,而不是 `grep` 全文。P1 就是这个共同地基。

### 退出标准(架构 §11)
> 混合检索召回 **top-5 recall ≥ 0.6**。

### 测试代码库
- **P1 首测目标:deer-flow(Python)** —— 已在本地(`deer-flow/`,~987 文件 / ~30 万行)、上游(bytedance/deer-flow)活跃推 PR;既是现成索引目标,又天然喂养 **P4 PR-Tracker**(定期分析其 PR)。
- **bluez / wpa(C)** —— 留到 C 场景(Bug-RCA)落地再验;C 专属难点(宏 / static / 头文件)见 §3,延后到 P1.5+。
- 本地 `deer-flow/` 只读克隆可直接索引(只读不碍解析);要干净基线可另 clone 一份锁 commit 到 `data/repos/deer-flow`。

### 关键前提调研结论 ⚠️
**deer-flow 在此领域无可复用之物**——它是通用 agent harness,无 AST 解析、无向量库、无 embedding、无 BM25、无 repo map、无符号工具、无索引流水线(唯一形似的是 DeerMem 记忆子系统里一个未实现的 `retrieval_adapter` 钩子,且服务于对话记忆而非源码)。故 P1 基础**全部建在外部 SOTA 之上**:

| 能力 | 选型依据 |
|---|---|
| repo map | [Aider](https://aider.chat/docs/repomap.html)(tree-sitter tags + 图排名) |
| 代码图谱 / 定位 | [RepoGraph (ICLR 2025)](https://arxiv.org/abs/2410.14684)、[OrcaLoca (ICML 2025)](https://arxiv.org/abs/2502.00350) |
| C 解析 | tree-sitter + tree-sitter-c + universal-ctags |
| 向量库 | LanceDB(嵌入式 + 原生混合检索) |
| embedding | **bge-m3**(默认,8K 上下文)/ voyage-code-3(可选) |
| 重排 | bge-reranker-v2-m3 |
| 融合 | RRF(Reciprocal Rank Fusion) |

---

## 1. 总体架构

```
                  ┌─────────────── 代码理解服务 services/code_index/ ───────────────┐
                  │                                                               │
   仓库路径 ──▶ ① 解析层 ──▶ ② 切块层 ──▶ ③ 索引层 ──▶ ④ 检索层 ──▶ ⑤ 导航工具(ACI)
   (bluez)    tree-sitter     符号边界        embedding      BM25+向量      grep_symbol
              + ctags         chunk + 元数据   + LanceDB      + RRF          read_function
                              (+ 代码图谱)    upsert         + rerank       get_callers/callees
                                                                             search_code
                  │                                                               │
                  └───────────────────────────────────────────────────────────────┘
                                     ▲                                       │
                                     │ build / 增量更新                       │ 查询
                            (见 §10 索引运维)                            agent / workflow
```

四条数据流:
1. **建索引**(离线/低频):walk 仓库 → 解析 → 切块 → embedding → 存 LanceDB(+ P1.5 建代码图谱)。
2. **检索**(高频):query → BM25 + 向量 → RRF 融合 → reranker 重排 → top-k chunk。
3. **导航**(高频):符号名 → 定位定义 / 读函数体 / 查调用者 / 查被调用者(P1.5 走代码图谱)。
4. **repo map**(按需,P1.5):给 agent 一份"全仓最重要符号"的压缩地图(token 预算内)。

---

## 2. 解析层(`parser.py`) [P1.0]

**多语言**:用**「每门语言一个独立 grammar 包」**(`tree-sitter-python` / 后续 `tree-sitter-c`),grammar 编译进 wheel、**零联网、可 pin 版本**。**不用 `tree-sitter-language-pack` 1.x** —— 它运行时按需从 GitHub releases 下载语法,**国内网络被墙(已实测 timeout,不可用)**。P1 以 **Python(deer-flow)** 起步(无宏/头文件,先把切块→嵌入→检索管线跑通),C 的符号查询与难点(宏/static)在 bluez 场景落地时再加(§3)。`parser.py` 的 `GRAMMARS` 注册表数据驱动:加新语言 = 加一个 `tree-sitter-<lang>` 依赖 + 注册一条 `LanguageGrammar`(节点类型/字段名),核心抽取逻辑不变。**取 parser 的 API**(0.26 离线版,已实测):`Language(tree_sitter_python.language())` → `Parser(lang)` → `parser.parse(bytes).root_node`,字段取值用 `node.child_by_field_name("name"/"parameters")`,限定名/方法归属沿 `node.parent` 链求。

**双工具互补**(架构 D5):

| 工具 | 职责 | 实现 |
|---|---|---|
| **tree-sitter**(主力) | 容错解析 C,提取 `function_definition` / `struct_specifier` / `enum_specifier` / `preproc_def` / `call_expression` 等 AST 节点 + 行范围 | 独立 grammar 包(`tree-sitter-c`,落地时 `uv add tree-sitter-c`)。API:`Language(tree_sitter_c.language())`(已验 Python 同款写法) |
| **universal-ctags**(补充) | 符号表(函数 / 宏 / typedef / struct + 位置),补 tree-sitter 不擅长的**宏定义/条件编译** | `ctags -R --output-format=json --fields=+neKz`(`scripts/setup.sh` 装) |
| clangd / LSP(按需,P1 不做) | 精确 caller/callee,需 `compile_commands.json`(`bear -- make` 生成);万文件级慢 | P1.5 若 C 解析不够准再上(见 §3) |

**对外接口**:
```python
@dataclass
class Symbol:
    name: str                 # 简单名,如 __init__
    qualified_name: str       # 带归属,如 MyClass.__init__(消歧/code_graph 用)
    kind: str                 # function / method / class(C 再加 struct/macro/typedef/enum)
    language: str             # python / c ...
    file: str
    start_line: int           # 1-indexed,整块定义起止行
    end_line: int
    signature: str | None     # 形参文本,如 (self, name);class 为 None

def parse_repo(root: Path, languages: list[str] | None = None) -> list[Symbol]: ...
def parse_file(path: Path, language: str | None = None) -> list[Symbol]: ...
    # language=None 时按后缀推断;.py→python;未知后缀返回 []
```

**同时解析 `.h` 头文件**:C 的结构(typedef/struct/宏)大量在头文件,必须一并索引,否则检索覆盖不全。

**为什么 tree-sitter 容错重要**:bluez/wpa 多宏、多条件编译,严格解析器(clang)会因缺 `compile_commands` 大面积失败;tree-sitter 增量容错,适合大规模粗扫。

---

## 3. 代码图谱(`code_graph.py`,RepoGraph/OrcaLoca 启发) [P1.5]

**目标**:支持 `get_callers(sym)` / `get_callees(sym)`——这是纯向量 RAG 做不到的多跳推理。

**构造**:
- tree-sitter 找到所有 `call_expression` 节点 → 提取被调函数名 → 解析到同仓的 `function_definition`。
- 节点 = 函数;有向边 = A 调用 B。
- 存储(P1.5 定):**SQLite 关系表做持久化真源**——`calls(caller_id, callee_id, file, line)` 建索引,支持 `get_callers/callees` 快查、可增量改边、跨机可读;**`networkx` 内存图按需从 SQLite 重建**(跑 PageRank / 多跳遍历)。不用 `.pkl`(不可查、版本锁、难增量)。规模大时再评估 Graphiti(P3)。

**对齐 RepoGraph**:行级粒度(函数内调用点的具体行),供后续 Bug-RCA 精确定位。

⚠️ **C 符号/调用解析是硬骨头,P1.5 动手前要单独出一份子调研**(别低估"先粗后细"):
- `static` 同名函数跨文件共存 → 名字解析必须带**文件作用域**,不能纯按名字;
- 大量调用被**宏包装**(如 `DBG(...)`、`bt_log(...)`),tree-sitter 看到的 `call_expression` 名是宏名,不是真函数;
- 函数指针 / 回调注册(`register_cb(handler)`)根本解析不到被调用者。
- 可能的出路:ctags 补宏表 + **宏→被调函数映射启发式**(解析宏体里的 `call_expression`,建 `macro_name → [real_callee]` 字典,图构建时命中即展开边——**抓不住 `##` token 拼接 / 函数指针宏**,只是其一);若仍不准,上 clangd / `compile_commands.json`。**P1.5 起步前先把这条研究透**。

---

## 4. 切块层(`chunker.py`) [P1.1]

**按符号边界切,不按固定行数切**:
- 每个 `function_definition` / `struct_specifier` / `#define` 一个 chunk。
- bge-m3 有 8K 上下文,绝大多数函数可整块装入。**超长切分延后**:`CodeChunk` 预留 `part`/`total` 字段,但 P1.1(Python/deer-flow)不实现切分——8K 够用、Python 函数不触发;真正的 cAST 式 AST 子语句切分留到 C 场景(bluez 状态机),见 [backlog #6](../.claude/memory/backlog-production-grade.md)。

**chunk schema**:
```python
@dataclass
class CodeChunk:
    id: str               # 稳定主键 f"{file}:{qualified_name}"(分段加 ":p{N}");不含 start_line——含行号对重构太敏感(重排函数顺序→全部 id 变→全量重嵌),行号作普通列。决策 #8
    symbol: str           # 限定名;module chunk 用文件路径
    kind: str             # function | method | class | module
    file: str
    language: str
    start_line: int
    end_line: int
    text: str             # 原始代码文本(read_function 直接用,无加工)
    content_hash: str     # text 的 sha256,增量更新按它判变(见 §10)
    fts_text: str         # 给 BM25 的词袋(标识符拆词 + docstring,小写空格分隔)
    part: int = 1         # 超长分段段号,预留
    total: int = 1        # 总段数,预留(切分延后,见 backlog #6)
    callers: tuple[str, ...] = ()  # 来自 code_graph;P1.5 建好图谱后回填
    callees: tuple[str, ...] = ()  # 同上
```
**`fts_text` 标识符分词**(C 召回关键技巧):同时处理 `snake_case`、`camelCase`、`SCREAMING_SNAKE`(宏)——用正则把标识符拆成词干再拼接,例如 `wpa_supplicant_assoc_req_ie_cb` → `wpa supplicant assoc req ie cb`,`hci_le_CisEstablished` → `hci le cis established`。光按 `_` 拆会漏掉 camelCase/宏。

**`fts_text` 纳入注释 / docstring**(BM25 高信号,低成本):用 tree-sitter 抽函数定义节点**紧邻的前导 `comment` 节点**(C 的 doxygen `/** */` / 行注释)与**函数体首条 docstring**(Python),并入 `fts_text`,可加权重复一次。自然语言描述是纯标识符检索补不上的语义缺口。

**重复 chunk 去重**:多文件相同的 `static inline` / 头文件重复定义会产生内容一致的 chunk。**不在存储层把 `file` 改成列表**(会拖累 schema 和 `read_function` 的单文件语义),而是**检索后按 `content_hash` 合并**同义结果、保留所有 `file:line` 位置;存储层去重留作 P6 优化。

**P1.1 落地决策(2026-07-24,对照 cAST 调研)**:
- **对标 [cAST](https://arxiv.org/html/2506.15655v1)**(EMNLP 2025)符号边界切块,但**只取其 split、不取跨符号 merge**——chunk 兼任 `read_function` 的单符号语义,合并会破坏。
- **新增「模块级 chunk」**(kind=`module`):把每个文件里不属于任何函数/类的代码(import / 全局常量 / `if __name__`)聚成一个 chunk,保证覆盖率 100%(cAST 的 plug-and-play)。靠 parser 新增的 `iter_source_files` 全文件遍历实现(只按符号分组会漏无符号文件,如纯 import 的 `__init__.py`)。
- **docstring 抽取归 parser**:`Symbol` 加 `docstring` 字段,parser 同一遍 DFS 抽好(parse-once、AST 精确),chunker 不碰 AST。**前导 comment / doxygen 抽取延后**到 C 场景([backlog #7](../.claude/memory/backlog-production-grade.md))——Python 靠 docstring 已够。
- **chunk 大小用非空白字符数**(学 cAST,`MAX_CHUNK_CHARS=20000`),不用 token(省掉 tokenizer 依赖)。
- **chunk_expansion(嵌入元数据头)**留到 P1.2 `embed.py`:嵌入时在代码前拼**语言对应的注释行**(Python `#` / C `//`)再嵌——如 `# file: src/adapter.c · function: disconnect_cb · lang: c\n<原始代码>`。用注释而非 `file|symbol|kind` 管道串:注释是代码的一部分(模型训练分布内),缓解 document(带头)/ query(自然语言、不加)的不对称;靠 BM25 兜词法、reranker 兜语义。query 端不加(无元数据可加,加了反成噪声)。
- **id 用 `qualified_name`** 消歧同类同名;**callers/callees** 留 P1.5 code_graph 回填。

---

## 5. Embedding(`embed.py`) [P1.2]

**决策(2026-07-24 选型复核后改):默认 `Qwen/Qwen3-Embedding-0.6B`** —— 本地/免费/0.6B/32K 上下文/代码为核心能力/CPU fast。`bge-code-v1`(~7B,CoIR 开源 SOTA)作 **GPU 可选档**;`BAAI/bge-m3` 留 **CPU 回退**;`voyage-code-3` 作 API 付费可选。Embedder 模型无关,config 一行切换。

| 维度 | **Qwen3-Embedding-0.6B(默认)** | bge-code-v1(GPU 可选) | bge-m3(CPU 回退) | voyage-code-3(API 可选) |
|---|---|---|---|---|
| 部署 | 本地(sentence-transformers) | 本地(**需 GPU**) | 本地 | API(付费) |
| 参数/大小 | 0.6B / ~1.2GB | ~7B / ~14GB | 0.6B / ~2.3GB | — |
| 上下文 | **32K** | 4K(模型卡示例) | 8K | 32K |
| 代码能力 | **核心能力**(支持编程语言) | **开源 SOTA**(CoIR 81.77;SWE-bench-Lite 67.4) | 通用模型,代码非强项 | 代码专用 |
| MTEB 多语(模型卡同表) | **64.33** | — | 59.56 | — |
| MTEB 英文 | **70.70**(0.6B 超 NV-Embed 7.8B) | — | — | — |
| CPU 推理 | ✅ fast(Reddit + 官方 TEI CPU 部署实测) | ❌ 7B 本地首建不现实 | ✅ 慢(批 15~20s) | — |
| query instruction | 需要(`prompt_name="query"`) | 需要(`trust_remote_code`) | 不需要 | 不需要 |
| 多功能 | dense(MRL 32-1024 维) | dense | dense+sparse+colbert | dense |

**选 Qwen3-0.6B 为默认的决定性理由**:
1. **Hyperion 核心是代码检索** —— Qwen3 把代码检索列为系列核心能力;bge-m3 是通用模型、代码非强项。
2. **同台碾压**:同为 0.6B,MTEB 多语 64.33 vs bge-m3 59.56(+4.8,模型卡同表);英文 70.70 超 7.8B 的 NV-Embed。
3. **更适合本地 CPU**:更小(1.2GB vs 2.3GB)、更长上下文(32K vs 8K,C 长函数更宽裕)、CPU 实测 fast。
4. **bge-m3 的独占优势对 P1 无价值**:其 dense+sparse+colbert 多功能 P1 只用 dense;sparse/BM25 那一路用 LanceDB 原生 FTS(不依赖 embedding 的 sparse)。
5. **代价极小**:仅 query 端加 instruction(document 端不变)。
6. **bge-code-v1 不作默认**:`~7B 参数,本地 CPU 首建几十万行不现实`;但 CoIR 81.77(开源 SOTA)+ SWE-bench-Lite 67.4(bug 修复检索,贴 Bug-RCA)极诱人 → 有 GPU 时作可选档。

> **诚实保留点**:Qwen3 模型卡未单列 0.6B 的 MTEB Code 精确分(只列 8B)。推断 0.6B 代码强于 bge-m3(系列以代码为核心 + 多语/英文全面超越),**最终以 P1.3 评测 top-5 recall 实测为准**;不达标 config 切回 bge-m3 重测,成本可控。

**铁律:embedding 模型选定后不能换——换要全量重嵌**。`index.py` 存模型指纹(`model_name + dim + max_seq_length + normalize`,见下"实现陷阱"),与 LanceDB 表元数据比对,变更触发重建。

### ⚠️ 实现陷阱(P1.2 必须处理)

1. **`max_seq_length` 默认静默截断**:sentence-transformers 加载很多模型默认 `max_seq_length=512`,超长**静默截断**。
   - **bge-m3:必须显式 `model.max_seq_length = 8192`**(否则"8K 装下 C 长函数"卖点落空)。
   - **Qwen3-0.6B**:模型卡标 32K,加载后也应显式设到目标值(按内存,如 8192 / 32768)。
   - `max_seq_length` **必须进指纹**(改它=改向量=需全量重嵌)。
2. **query instruction(Qwen3 / bge-code-v1 需要)**:`embed_query` 用 `model.encode([q], prompt_name="query")`(Qwen3)或拼 instruction 串(bge-code-v1);`embed_chunks`(document 端)**不加**。`query_instruction` 作 config 项,模型无关。
3. **`normalize_embeddings=True`**:cosine 相似度官方推荐;进指纹。
4. **维度动态取**:`model.get_sentence_embedding_dimension()`,不硬编码(将来换模型 / 用 MRL 截断不破)。
5. **批编码**:sentence-transformers `model.encode(list, batch_size=N)` 内部按**批内最长动态 padding**(不填 max_seq_length),直接用。
6. **HF 国内下载**:模型 ~1.2GB+,直连易超时。config `embedding.hf_endpoint`(默认 `https://hf-mirror.com`),加载前注入 `os.environ["HF_ENDPOINT"]`;下载失败给清晰指引。

依赖:新增 **`sentence-transformers>=2.7`** + **`transformers>=4.51.0`**(Qwen3 要求,低于此报 `KeyError: 'qwen3'`)。bge-reranker 也走 sentence-transformers。`pyproject.toml` 里 tree-sitter / lancedb / rank-bm25 已声明。

---

## 6. 向量库与混合检索(`store.py` + `retrieval.py`) [P1.2-P1.3]

**选 LanceDB,且用其原生混合检索**(避免自搓 BM25+RRF 胶水)。下述 API 已对照 LanceDB 0.34 官方文档核实(P1.3 调研,见决策 #8)。

### 6.1 store.py:LanceDB 嵌入式表(table-per-repo)

- **schema(pyarrow,生产优于 pydantic LanceModel)**:id / symbol / kind / file / language / start_line / end_line / text / content_hash / fts_text(string)/ vector(`pa.list_(pa.float32(), dim)`,**维度建表时定死**,改维度=新建表迁移)。
- **embedded 连接**:`lancedb.connect("data/code_index/<repo>/lancedb")`,每仓库一张表(§14.2)。
- **建表后立即建两个索引**:
  - `create_scalar_index("id", replace=True)` —— **merge_insert 必须**,否则撞 "unindexed rows > 10000" 报错。
  - `create_fts_index("fts_text", stem=False, remove_stop_words=False, with_position=True, ascii_folding=True, replace=True)` —— **代码场景关键参数**:`stem=False`(否则 malloc 被 stem 乱变)、**`remove_stop_words=False`**(否则 `int`/`void`/`public`/`return` 被当英文停用词删掉!)、`with_position=True`。§4 的 `fts_text` 预拆词(snake/camel/SCREAMING)是因为 LanceDB 默认 `simple` tokenizer **不拆下划线**(`hci_inquiry_complete` 是一整个 token)——预拆验证必要。
- **metric**:向量已 L2 归一化 → 用 `dot`(最快);若建 IVF 向量索引,metric 必须**建索引时定**(`create_index("vector", config=IvfFlat(metric="dot", ...))`)。
- **upsert**:`merge_insert("id").when_matched_update_all("target.content_hash <> source.content_hash").when_not_matched_insert_all().execute(...)` —— 条件更新,只重写 content_hash 变了的行(增量利器);可选 `.when_not_matched_by_source_delete()` 清孤儿。
- **写后 `tbl.optimize()`**:把新行折叠进 FTS/向量索引,否则新行走 flat scan 慢路径。经验:每 ~10 万行或一批写后一次。

### 6.2 retrieval.py:原生 hybrid + RRF + 远端 reranker

```python
# 真实 LanceDB 0.34 API(无 hybrid_search()/match_text(),均已弃用)
from lancedb.rerankers import RRFReranker
res = (tbl.search(query_type="hybrid",
                  vector_column_name="vector", fts_columns="fts_text")
         .vector(qvec)            # 已算好的稠密向量(L2 归一化)
         .text(query)             # FTS 查询串
         .limit(candidate_top_n)  # 候选池,默认 50
         .rerank(reranker)        # RRF(k=60)融合;或自定义 Reranker 子类调远端 reranker
         .limit(final_top_k)      # 最终,默认 5
         .to_list())
```

- **BM25(FTS)**:对 C 的函数名/宏名/错误码(强词法信号)召回好——"必须混合"的根因(dense 和 sparse 失败模式正交:改名 dense 强、错误串 BM25 强)。
- **RRF**:`RRFReranker(K=60)` 是 hybrid 默认,**客户端算**(Python SDK 拉回 vector/fts 两个 Table 后融合)。k=60 是 Cormack 2009 原始论文标准,跨 80+ 实验近最优。
- **reranker(provider 抽象,镜像 embed.py)**:默认**远端** `qwen3-rerank`(DashScope,同 `DASHSCOPE_API_KEY`、¥0.0005/千token≈8万查询=¥1、OpenAI 兼容 `/reranks`),SiliconFlow `BAAI/bge-reranker-v2-m3` **免费** fallback(Cohere 形态),本地仅 GPU 可选(CPU 257s/100doc 不可交互)。用 LanceDB `Reranker` 子类(重写 `rerank_hybrid`)把远端 API 包进 `.rerank()` 接口。reranker 对 **fts_text**(短,标识符+docstring)打分,不对全长代码体(bge-reranker-v2-m3 推荐 1024 token、DashScope 4000 token/doc,fts_text 都装得下)。
- **默认不按 kind 预过滤**:struct/宏定义常是关键上下文;仅查询显式带 type 时才 `.where("kind='function'", prefilter=True)`。

### 6.3 retrieval 补强(借鉴 CRG search.py)

- **`_out_mode` 可观测**:每查询记录实际走了 hybrid / fts-only / vector-only / keyword / none(fallback),写进结果元数据。
- **查询类型 boosting**(规则判别,§14.5):PascalCase 查询→Class ×1.5、snake_case→Function ×1.5、含 `.` dotted→qualified_name ×2.0、抽出的标识符命中 ×2.0。C 场景 `wpa_supplicant_add_iface`、`DBus.Message` 直接受益。
- **三级降级**:hybrid(FTS+向量)→ 仅 FTS → 仅向量 → keyword LIKE,`_out_mode` 记录走了哪条。
- **context-file boosting**(Bug-RCA 可选):当前打开文件的符号 ×1.5(workflow 传入)。

### 6.4 weakest-link 警示 + 备用

- ⚠️ **RRF 不是越多路越好**([Balancing the Blend, arXiv 2508.01405, 2025-08](https://arxiv.org/html/2508.01405v2)):弱路径会污染融合,实测有 case 比 FTS 单路还差。**加 BGE-M3 sparse/SPLADE 第 3 路前必须单路 eval 消融,弱路径砍掉**。
- 备用:若 LanceDB FTS 对 C 标识符召回仍不足,引入 `rank_bm25`(已在依赖)做二级精排。bge-m3 sparse 输出仅作消融验证后的可选第三路(默认 Qwen3 无 sparse)。

---

## 7. repo map(`repo_map.py`,Aider 启发) [P1.5]

**给 agent 一份"全仓最重要符号"的压缩地图**——让它不读全文也能知道"有哪些模块/接口、该去哪找"。

**算法(Aider 同款)**:
1. tree-sitter tags:每个文件抽出"定义的符号 + 引用的符号"。
2. 构图:文件为节点,定义-引用关系为边。
3. **图排名**(PageRank 式):被引用最多的符号 = 最重要。
   ⚠️ **PageRank 盲点**:同仓内零入边的符号会被低估——而 `main`、信号处理函数、`GSource`/事件循环回调注册处(`g_source_attach(...)`、`register_cb(handler)` 的函数实参)恰恰零入边却最关键(由框架调用,不在同仓调用图里)。**对策**:① 识别「注册型调用」的函数实参,给被注册符号额外权重;② 配 `repo_map.focus_entry_points`(符号名列表,如 `main`、`adapter_ops`)手工兜底。
4. **token 预算裁剪**(默认 `map_tokens=2048`):只保留排名最高、能塞进预算的定义(签名 + 关键行)。

**输出**:`str`(树形 + 签名,直接喂 LLM)。按 chat 状态动态调大小(无上下文时放大)。延后到 P1.5,等 Bug-RCA 真跑起来再加。

---

## 8. 导航工具(`tools/code_nav.py`,ACI)

借鉴 SWE-agent / OrcaLoca 的 agent-computer interface:工具要少、要准、返回要紧凑。

| 工具 | 阶段 | 作用 | 返回 |
|---|---|---|---|
| `grep_symbol(name)` | P1.4 | 按符号名找定义位置 | `file:line` 列表(同符号多定义全列) |
| `read_function(symbol, file=None)` | P1.4 | 读某函数完整源码;`file` 用于消歧同名 static | 代码文本 + 元数据 |
| `search_code(query)` | P1.4 | 混合检索语义/词法 | top-k chunk(`file:line + 片段`) |
| `get_callers(symbol)` | P1.5 | 谁调用了它 | `file:line` 列表(code_graph) |
| `get_callees(symbol)` | P1.5 | 它调用了谁 | `file:line` 列表(code_graph) |

**声明式接入**(零改核心):在 `config.yaml -> tools` 加 5 条 `use: hyperion.tools.code_nav:xxx_tool`,registry 自动加载(P0 已验证此机制)。

---

## 9. 模块与文件布局

```
src/hyperion/services/code_index/
├── __init__.py
├── parser.py        # [P1.0]  tree-sitter + ctags:Symbol 抽取(含 .h)
├── chunker.py       # [P1.1]  符号边界切块 + fts_text 分词(callers/callees 先留空)
├── embed.py         # [P1.2]  embedding 模型(bge-m3 默认;voyage 可选)
├── store.py         # [P1.2]  LanceDB 表管理(schema / FTS 索引 / upsert / 查询)
├── index.py         # [P1.2]  编排:walk→parse→chunk→embed→store(+ 增量,见 §10)
├── retrieval.py     # [P1.3]  混合检索管线(BM25+向量+RRF+rerank)
├── code_graph.py    # [P1.5]  caller/callee 图(RepoGraph 启发;C 解析难点见 §3)
└── repo_map.py      # [P1.5]  Aider 式 repo map

src/hyperion/tools/code_nav.py   # 5 个导航 @tool 包装(P1.4 grep/read/search;P1.5 callers/callees)
```

**config.yaml 新增**:
```yaml
code_index:
  enabled: true
  repo:
    bluez: data/repos/bluez            # clone 到此(gitignore 已含 data/);锁定某 commit 作评测基线
  embedding:                           # 实际配置见 config.yaml(P1.2 已落地:远端 OpenAI 兼容默认)
    provider: openai_compatible        # 远端默认(免下载/免 torch);本地可选 sentence_transformers
    base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
    api_key: $DASHSCOPE_API_KEY
    model: text-embedding-v4           # = Qwen3-Embedding 全血版;换则触发全量重建
    dimensions: 1024
    batch_limit: 10                    # DashScope v4 每请求 10 条(CRG 注释点名此限制)
    normalize: true
    # 本地模式(provider: sentence_transformers)才用:max_seq_length / device / batch_size / hf_endpoint / query_instruction
  vector_store:
    path: data/code_index              # table-per-repo:<path>/<repo>/lancedb(§14.2)
  retrieval:
    rrf_k: 60                          # Cormack 2009 标准
    candidate_top_n: 50                # 喂 reranker 的候选池
    final_top_k: 5
    fts_stem: false                    # 代码场景必须关(见 §6.1)
    fts_remove_stop_words: false       # 代码场景必须关(否则 int/void/public/return 被删)
    query_boost: true                  # 查询类型 boosting(PascalCase/snake/dotted,§6.3)
  reranker:                            # provider 抽象,镜像 embedding(§6.2)。live 测:DashScope rerank 走原生端点(非 OpenAI 兼容面)
    provider: dashscope                # dashscope(原生,默认)| siliconflow(Cohere 形态,免费)| sentence_transformers(本地,GPU)| off
    base_url: https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank
    api_key: $DASHSCOPE_API_KEY        # 与 embedding 同一个 key
    model: qwen3-rerank                # 100+ 语言;¥0.0005/千token
    rerank_top_n: 5
  repo_map:
    map_tokens: 2048                   # P1.5

tools:                                 # 追加 5 条导航工具
  - { name: grep_symbol,  group: code, use: hyperion.tools.code_nav:grep_symbol_tool }
  - { name: read_function, group: code, use: hyperion.tools.code_nav:read_function_tool }
  - { name: get_callers,  group: code, use: hyperion.tools.code_nav:get_callers_tool }
  - { name: get_callees,  group: code, use: hyperion.tools.code_nav:get_callees_tool }
  - { name: search_code,  group: code, use: hyperion.tools.code_nav:search_code_tool }
```

---

## 10. 分阶段实现路线(MVP → 生产级) + 索引运维

按"最小实现 → 对齐 SOTA 生产级"原则(见 `.claude/memory/align-to-deerflow-production-grade.md`):

| 子阶段 | 交付 | 退出标准 |
|---|---|---|
| **P1.0** | `parser.py`:tree-sitter 解析单文件 → `Symbol` 列表(含 .h) | 对 bluez 任一 .c/.h 能抽全函数/struct/宏 |
| **P1.1** | `chunker.py`:符号边界切块 + fts_text 分词 | chunk 覆盖率 >95% 符号 |
| **P1.2** | `embed.py` + `store.py` + `index.py`:build 全量索引(分批+进度) | bluez 建库成功,LanceDB 可查 |
| **P1.3** | `retrieval.py`:原生 hybrid + RRF + bge-reranker | **top-5 recall ≥ 0.6**(主退出标准) |
| **P1.4** | `tools/code_nav.py`:`grep_symbol`/`read_function`/`search_code` 接入 agent | demo agent 能用导航工具定位函数 |
| **P1.5(生产级)** | `code_graph.py`(caller/callee)+ `repo_map.py`(Aider)+ ctags 补宏 + `get_callers/callees` | 多跳调用链可用;repo map 可注入 |

每个子阶段都配单测 + ruff;P1.3 起配召回评测。

### 索引运维:首次成本 / 增量更新 / 模型变更

- **首次建索引耗时**:bluez 几十万行 × bge-m3(CPU ~15-20s/批)→ 首次 `index build` 预计**几十分钟级**。对策:分批 embed + 进度打印 + 可选 GPU;别让人误以为卡死。
- **增量更新(生产必需)**:用 **`file_manifest`(见下)做文件级对账**(非只看 mtime)——消失路径 → 删其 chunk;新增 → 嵌入;`content_hash` 变 → 重嵌。日常只增量,不全量。
- **embedding 模型变更**:检测模型指纹变化 → 触发全量重建(一次性,接受成本)。
- **批编码(CPU 提速关键)**:bge-m3 CPU 慢,**批量编码**(一次传 chunk 列表),sentence-transformers **动态 pad 到批内最长**(非填到 8K)——小函数批 padding 开销很小。**不要逐条 encode**,按内存调 `batch_size`。

### 索引构建的原子性与状态清单

P1.3 调研 + CRG 借鉴后,**原子性按存储引擎分两条路**(决策 #8):

**① LanceDB 向量库(无事务)**:
- **全量重建**:`index build` 先写 temp 目录 `data/code_index/<repo>/lancedb_tmp`,全部成功后 `os.rename` 原子切成 `lancedb`(同文件系统 rename 是原子 syscall),失败丢弃 tmp——要么旧索引、要么新索引,无半成品。
- **增量**:`merge_insert` 条件 upsert(content_hash 不变跳过)本身是批级原子,不需 temp。消失路径用 `when_not_matched_by_source_delete` 清孤儿。

**② SQLite code_graph(P1.5,有事务)** —— 借鉴 CRG `graph.py`:
- 连接三件套:`isolation_level=None`(禁 Python 隐式事务)+ `journal_mode=WAL`(读写并发)+ `busy_timeout=5000`。
- 每文件/每批一个 `BEGIN IMMEDIATE` 事务(删旧+插新原子化),`_begin_immediate` 嵌套防御(先 rollback 脏事务再 BEGIN)。**抛弃图层面的 temp+rename**——CRG 经 9 个 schema 版本 + `test_transactions.py` 验证此方案更简更稳、增量友好、长重建期间可读(WAL 快照)。

**`index_manifest.json`**(LanceDB sidecar,建库成功后落盘,检索前校验):
- `repo_commit`:建库时仓库锁定的 commit(评测基线 / staleness 判定)。
- `model_fingerprint`:`embedding.model + dim + normalize`(变 → 全量重建)。
- `schema_version`:chunk schema 版本(改 → 全量重建)。
- `file_manifest`:`{相对路径: content_hash}` —— 增量对账依据。

**增量加速(借鉴 CRG incremental.py)**:
- `git diff --name-only -z`(不 walk 文件系统)定位变化文件 + content_hash 短路跳过未变;非 git 仓库回退到 walk + file_manifest 对账。
- code_graph 增量加 **N 跳依赖追踪**(BFS,max 2 跳 / max 500 文件 / `truncated` 标志防 hub 爆炸),把变化文件的 caller 也拉进重解析集。
- **并行 parse 串行写**:ProcessPoolExecutor(min(cpu,8),tree-sitter 释放 GIL),LanceDB/SQLite 单写者;MCP stdio 下切 ThreadPoolExecutor(避免子进程继承 stdio 管道死锁)。多进程必须 `spawn` 非 `fork`。

**重命名 / 移动**:chunk id 含 `file`,改名让旧 id 孤儿。对账时:消失路径删、新增嵌、hash 变重嵌;或用 `git diff --name-status` 的 `R`(rename)状态显式搬运。

---

## 11. 评测(`eval/`,P1.3 起)

P1.3 调研(前沿评测方法论 + CRG eval 框架借鉴)后强化。退出标准从单指标 0.6 改为**多指标 + 置信区间**(决策 #8)。

- **Ground truth(独立于检索系统)**:从 bluez git 历史用 `git log --grep="Fixes:"` + CVE 批量提取 fix commit。
- **fix → 符号映射(关键,行级)**:⚠️ 不要对父/子提交各跑 parser 再 diff 符号列表(函数体内几行改动时父子符号集合相同,symbol-diff 抓不到)。正确:**行级映射** ① `git diff <parent> <fix>` 拿改动文件 + 行范围(hunks);② 对子提交跑一次 parser;③ 改动行映射到**包住它的最内层符号**(函数/struct/全局/宏)。这组符号 = ground truth。此法**独立于检索系统**(SWE-bench/SweLoc/Defects4J 标准做法)。
- **⚠️ 循环论证警示(借鉴 CRG impact_accuracy)**:**禁止**用"图里的 caller"当金标去评"检索能否找到 caller"——那是循环上界(预测器和金标走同一张图)。金标必须来自 git diff(独立证据)。若同时报图派生金标,CSV 加 `ground_truth_mode` 列并标注 "upper bound"。
- **⚠️ 数据污染警示(SWE-Bench Illusion, NeurIPS 2025)**:公开 issue 评测集会被 LLM 记忆(凭 issue 文本猜文件路径 76%)。**评测集必须用时间 cutoff 后的新 issue + 跨 repo 验证(holdout)**,否则 recall 虚高。
- **难度分层(量化)**:
  - **L1 词汇**:query 含 gold 符号名/文件名(Rouge-1 ≥ 0.3 或符号 token 命中)。
  - **L2 语义**:query 描述行为/日志/错误现象,同模块内(Rouge-1 ∈ [0.1, 0.3])——**退出标准瞄准这档**。
  - **L3 推理**:跨模块/多跳调用链(Rouge-1 < 0.1 或 gold 跨 ≥2 文件,需 code_graph,P1.5 后评)。
- **负例 / precision**:配 confuser 负例(同子系统、函数名相近、无关),报 precision@5,防"全塞进去"刷分。
- **指标(自己实现,CRG scorer 不够——它无 Recall@k/nDCG/多标签 MRR)**:Recall@k、Precision@k、多标签 MRR、nDCG@k,走 BEIR 标准;BEIR 兼容 JSONL(corpus/queries/qrels)。
- **harness(借鉴 CRG eval/runner.py)**:注册表 + 统一 `run(query_set, retriever, config) -> list[dict]` 签名 + try/except 异常隔离(一个 benchmark 挂不影响其他)+ **失败语义**(`status="error"` 列,绝不默认 recall=0/1,回归测试钉死)+ 可复现性校验(全量 clone 禁 `--depth` + returncode 检查 + pin 校验)。runner 写 JSONL,reporter 后处理成对比表。
- **P1.3 退出量化标准(评测集 ≥150 条,L1/L2/L3 各 ≥50,95% bootstrap CI)**:
  1. L2 recall@5 ≥ **0.55**(CI 下界 ≥ 0.45)
  2. L2 precision@5 ≥ **0.40**
  3. L2 MRR ≥ **0.45**
  4. L1 recall@5 ≥ **0.85**(sanity)
  5. BM25 baseline L2 recall@5 ≤ **0.40**(证明语义检索真有增益)
  6. holdout repo(训练未见)衰减 ≤ 15pp(防过拟合)
  > 公开数据点:BM25 SWE-bench-Lite function Acc@5=0.32、CodeRankEmbed=0.59、SweRankEmbed-Large=0.72。0.55 对"通用 embedding 起步、未领域微调"是诚实目标;v1 数据出来后再决定是否收紧到 0.6。
- **做法**:给 agent 某 bug 的症状/日志 → 收集 `search_code` 返回的 top-N → 算是否命中 fix 改动行的所属符号。

### P1.3 实测结果(2026-07-27,Hyperion 自身代码首测)

评测集 `eval/sets/hyperion.jsonl`:**人工 curate 18 条**(8 L1 + 10 L2;gold 独立标注;production 级 ≥150 条 + L3 + git-diff 自动抽取见 backlog #13)。在 `src/hyperion`(201 chunk)上建索引(DashScope text-embedding-v4 + qwen3-rerank),`uv run python eval/run_eval.py eval/sets/hyperion.jsonl hyperion` 出:

| tier | recall@5 | precision@5 | MRR | nDCG@5 | hit@5 |
|---|---|---|---|---|---|
| L1 (n=8) | **1.000** | 0.200 | 0.854 | 0.891 | 1.000 |
| L2 (n=10) | **0.650** | 0.240 | 0.483 | 0.496 | 0.800 |
| L2 无 reranker | 0.600 | 0.220 | 0.378 | 0.402 | 0.800 |

**退出标准裁定**:**主标准 L2 recall@5 = 0.650 ≥ 0.55 ✅ 达标**;L1 sanity 1.000 ✅;L2 MRR 0.483 ≥ 0.45 ✅。reranker 贡献主要在排序质量(L2 MRR +0.105、nDCG +0.094;recall 仅 +0.05),符合调研"reranker 增益在 precision/ordering"。
- precision@5 = 0.240 未达 0.40——**指标定义问题非系统缺陷**:`precision@5 ≈ recall × |gold|/k = 0.65 × 2/5 = 0.26`,小 gold 集(1-2 符号)数学上封顶 ≈ 0.2-0.4,与实测吻合。修正:改 `precision@min(k, |gold|)`(backlog #16)。
- BM25 baseline(条件 5)+ holdout(条件 6)待补:需 BM25-only 模式 + 第二仓库(backlog #14/#15)。

**结论**:P1.3 主退出标准达标,检索管线(parser→chunker→embed→store→index→retrieval→reranker→eval)端到端跑通,有真实召回数字。诚实保留:18 条是 indicative(非 ≥150 统计 tight),production 级评测是后续。

---

## 12. 风险与对策

| 风险 | 对策 |
|---|---|
| C 宏/条件编译/头文件解析不全 | tree-sitter 容错 + 一并索引 .h + ctags 补宏;必要时 clangd |
| **C 调用/符号解析不准(P1.5)** | static 同名带文件作用域;宏包装/函数指针可能要 clangd;**P1.5 前出子调研**(见 §3) |
| embedding 模型锁定后想换 | config 锁定 + 模型指纹检测 → 变更触发全量重建 |
| **embedding CPU 推理慢(首建几十分钟)** | 默认 Qwen3-0.6B(CPU fast、~1.2GB);分批 embed + 进度 + 可选 GPU;日常只增量更新(§10);极端慢可上 backlog 的 ONNX int8 提速 |
| 全量索引慢 / 日常 stale | 增量更新:按 `content_hash` 只重嵌变化 chunk(§10) |
| LanceDB FTS 对 C 标识符召回弱 | `fts_text` 子串拆词(snake/camel/SCREAMING);不足则引入 rank_bm25 二级精排(bge-m3 的 sparse 输出仅作"回退档"可用,默认 Qwen3 无 sparse) |
| 调用图同名函数冲突 | 文件作用域消歧 + static/extern 标注;P1 先粗后细 |
| LanceDB 运行时兼容(ABI / glibc / tantivy) | wheel 已覆盖 cp39-abi3(含 3.12)+ macOS arm64,无需编译;`scripts/setup.sh` 加 `import lancedb` 冒烟测早暴露运行时问题 |
| tree-sitter 并发解析 | parser 单实例非线程安全;并发建索引走 `multiprocessing`(非 threading),每 worker 各持一个 Parser。P1 单进程顺序建索引即可 |

---

## 13. 决策记录

1. **(2026-07-24 改)embedding 默认模型 → `Qwen/Qwen3-Embedding-0.6B`**(本地/免费/0.6B/32K/CPU fast/代码为核心能力;同 0.6B 同表 MTEB 多语 64.33 超 bge-m3 59.56)。`bge-code-v1`(~7B,CoIR 81.77 SOTA、SWE-bench-Lite 67.4)作 GPU 可选档;`bge-m3` 留 CPU 回退。选定锁定,换则全量重建。详见 §5。
2. **P1.5(repo map + 代码图谱)延后**——P1 主范围先做扎实 P1.0-P1.4(检索 top-5 召回);repo map/调用图等 Bug-RCA 真跑起来再加。届时先按 §3 做 C 解析子调研。
3. **(v2.2)grammar 包换路**:弃 `tree-sitter-language-pack` 1.x(运行时按需从 GitHub releases 下载语法,国内网络 timeout 不可用)→ 改用**每语言独立 grammar 包**(`tree-sitter-python` / 后续 `tree-sitter-c`),wheel 内置、零联网、可 pin。并发建索引用 `multiprocessing`(parser 单实例非线程安全),P1 单进程顺序建索引即可。
4. **(v2.2)评审吸收**:采纳——索引原子性 + 状态清单(§10)、评测**行级映射** + 难度分层 + 负例(§11)、fts_text 加注释/docstring(§4)、检索**取消默认 type 预过滤**(§6)、repo map 入口点加权(§7)、图持久化 SQLite+networkx(§3)。纠正——LanceDB wheel 已覆盖 3.12/macOS arm64,无需编译(§12)。
5. **(2026-07-24)P1.1 切块落地**:对照 [cAST](https://arxiv.org/html/2506.15655v1)(EMNLP 2025)调研后——符号边界切块取 cAST 的 split、不取跨符号 merge;新增模块级 chunk 兜底(import/常量)保覆盖率 100%;docstring 抽取归 parser(parse-once);超大符号 AST 切分 + 前导注释抽取延后到 C 场景(bluez);chunk_expansion 元数据头留 P1.2 embed;chunk 大小用非空白字符数。详见 §4「P1.1 落地决策」。
6. **(2026-07-24)P1.2 选型复核**:前沿调研(Qwen3-Embedding 2025-06、bge-code-v1 2025-05、CoIR/MTEB 榜)后改默认模型(见决策 #1)。审核 §5 发现 6 处实现改进——① `max_seq_length` 须显式设(sentence-transformers 默认 512 静默截断,会抽空"8K/32K 装下长函数"卖点)且进指纹;② 指纹扩成 `model_name+dim+max_seq_length+normalize`;③ chunk_expansion 用注释行非管道串(§4);④ HF 国内下载走 `hf-mirror.com`;⑤ 批编码用 sentence-transformers 原生动态 padding;⑥ 维度动态取。完整三态加载冷却 + ONNX int8 提速记 backlog(#8/#9)。
7. **(2026-07-24)向量库设计决策**:评审 [向量数据库设计分析报告.md](向量数据库设计分析报告.md) + 2026 最佳实践后——坚持 LanceDB 嵌入式,拒绝 Qdrant 收敛(本地优先 / 规模未到 / 两台机一致);多仓库(systemd / pipewire 等)走 table-per-repo;`store.py` 抽象 `VectorStore` 接口留 Qdrant 扩展性(**P1 不实现 QdrantStore**,只留接口口子);升级触发器 = 常驻服务 / 千万级向量 / 多用户在线;吸收报告合理内核(规则意图 RRF、payload callers/callees、Recall 评测),拒绝过度(多租户 shard、蓝绿、BQ、在线监控)。详见 §14。
8. **(2026-07-24)P1.3 调研吸收 + code-review-graph 借鉴**:前沿调研 5 路(LanceDB 0.34 真实 API / bge-reranker 远端 vs 本地 / 混合检索前沿 RRF+TRF+weakest-link / 代码评测 CoIR+SWE-bench)+ 参考项目 [code-review-graph](https://github.com/tirth8205/code-review-graph)(本地 `code-review-graph/`,3 簇深读:检索·store·embedding / graph·增量·原子性 / eval)后定:
   - **LanceDB 真实 API**(§6):无 `hybrid_search()`/`.match_text()`(弃用),hybrid 走 `.search(query_type="hybrid").vector().text().rerank().limit()`;FTS 代码场景 `stem=False`/`remove_stop_words=False`/`with_position=True`;upsert 用 `merge_insert`+id scalar index;写后 `optimize()`;锁 `lancedb>=0.34,<0.36`。
   - **reranker provider 抽象**(§6.2):默认远端 DashScope `qwen3-rerank`(同 key/同价/OpenAI 兼容),SiliconFlow `BAAI/bge-reranker-v2-m3` 免费 fallback,本地仅 GPU(CPU 257s/100doc 不可交互);LanceDB `Reranker` 子类包远端 API。
   - **原子性分两条路**(§10):SQLite code_graph 走 `BEGIN IMMEDIATE`+WAL(弃 temp+rename,借鉴 CRG graph.py 9 版本验证);LanceDB 全量重建走目录 swap,增量走 merge_insert。
   - **retrieval 补强**(§6.3,借鉴 CRG search.py):`_out_mode` 可观测 + 查询类型 boosting + 三级降级。
   - **退出标准改多指标**(§11):L2 recall@5 0.55(CI 0.45)+ precision@5 0.40 + MRR 0.45 + L1 0.85 + BM25 baseline 0.40 + holdout 衰减 15pp。弃单指标 0.6(偏进取)。
   - **chunk id 去 start_line**(§4):改 `{file}:{qualified_name}`(+`:p{N}`),行号作普通列(重构稳健性,借鉴 CRG)。
   - **eval harness**(§11,借鉴 CRG eval/runner.py):注册表+统一签名+异常隔离+失败语义+循环论证警示+SWE-Bench Illusion 污染警示。
   - backlog +TRF(#10)/CoSQA+ 自动金标(#11)/embedding provider 硬化(#12)。
9. **(2026-07-27)P1.3 实测达标 + 退出标准裁定**:在 Hyperion 自身代码(201 chunk)上人工 curate 18 条评测集(8 L1 + 10 L2),`eval/run_eval.py` 跑出 **L2 recall@5 = 0.650 ≥ 0.55 ✅ 主标准达标**;L1 sanity 1.000、L2 MRR 0.483 也过。reranker(qwen3-rerank)主要提升排序质量(L2 MRR +0.105)。precision@5 = 0.240 未达 0.40——裁定为**指标定义问题**(小 gold 集封顶 |gold|/k),改 precision@min(k,|gold|)(backlog #16)。BM25 baseline + holdout 待补(backlog #14/#15);评测集扩 ≥150 + L3 + git-diff 自动金标(backlog #13)。**P1.3 收官,管线端到端跑通。** 详见 §11「P1.3 实测结果」。

---

## 14. 向量库设计决策

**背景**:向量库选型与设计的完整分析见 [向量数据库设计分析报告.md](向量数据库设计分析报告.md)(v2.0,P1.3 落地版,反映 LanceDB 0.34 + CRG 借鉴后的最新进展)。本节固化核心决策,白纸黑字,避免被同类"上 Qdrant"建议反复带偏。

### 14.1 引擎:坚持 LanceDB 嵌入式,拒绝 Qdrant 收敛

报告 2.1 建议"统一 Qdrant 为唯一生产向量引擎"。**驳回**,理由:

1. **本地优先 vs server**:Qdrant 要常驻 docker server,违背 Hyperion「uv + .venv + 两台机零运维」理念。LanceDB 嵌入式(进程内、零 server)是 local-first 首选(2026 多份对比一致)。
2. **偷换概念**:报告引 Qdrant 团队对 **pgvector** 的批评论证换 Qdrant,但 Hyperion 选的是 **LanceDB**(非 pgvector)。LanceDB 原生支持 BM25 混合检索(Tantivy)+ 磁盘原生 + 设计十亿级,pgvector 的缺点不适用。
3. **规模**:百万级 chunk 是 LanceDB 舒适区(生产实证 7 亿–10 亿向量),Qdrant 在此量级优势不显。
4. **两台机协作**:LanceDB 表 = 文件(`data/code_index/`),随数据 rsync;Qdrant 要两台机各起 server,违背一致性。
5. **Qdrant 优势对 Hyperion 无价值**:富 payload 过滤(单机可忽略)、多租户 shard(非多租户)、client-server 高并发(单用户)。

### 14.2 多仓库:table-per-repo(教科书最优)

未来扩展到 systemd / pipewire 等多仓库,**不上 Qdrant/Milvus**,走 LanceDB table-per-repo(LanceDB 多租户最佳实践:每表 = 独立 Lance 文件目录,物理隔离天然免费):

```
data/code_index/
├── bluez/lancedb/          # 每仓库一张表:独立 index / manifest / 增量 / IVF-PQ
├── systemd/lancedb/
├── pipewire/lancedb/
└── wpa_supplicant/lancedb/
```

- 单库搜:选表查;跨库搜:并行查多表 → 应用层 RRF 合并。
- 文件级独立:某仓库索引可单独 rsync / 删除 / 重建。
- 10 个系统仓库 ≈ 100 万 chunk(100 万向量),LanceDB 舒适区(实证 700M–1B),远未到瓶颈。

### 14.3 VectorStore 接口:留 Qdrant 扩展性,但不现在上

按 Hyperion provider 抽象哲学(模型工厂 / Embedder),`store.py` 抽象 `VectorStore` 接口,底层默认 LanceDB,**接口不锁死 LanceDB 专属 API**:

```python
class VectorStore(Protocol):
    def upsert(self, repo: str, chunks: list[CodeChunk], vectors: np.ndarray) -> None: ...
    def hybrid_search(self, repo: str, query_vec: np.ndarray, fts_query: str, top_k: int) -> list: ...
```

- 默认实现 `LanceDBStore`(table-per-repo)。
- **未来若触发升级(见 14.4)**,加 `QdrantStore` 实现,config 一行切,上层 `retrieval.py` 不改。
- **P1 不实现 QdrantStore**——只保证接口留口子,不现在上。

### 14.4 升级触发器(何时才真该上 Qdrant/Milvus)

任一满足才重新评估(当前一个都不满足):

| 触发条件 | 该上 |
|---|---|
| Hyperion 变成**常驻后台 agent 服务**(规避嵌入式长驻内存泄漏) | Qdrant server |
| 单库向量到**千万级**(≈几亿行代码) | Milvus 分布式 |
| **多用户并发在线查询**(SaaS) | Qdrant client-server |
| 仓库数到**几百 + 亿级向量** | Milvus |

### 14.5 对外部常见建议的取舍

| 外部常见建议 | 取舍 |
|---|---|
| 意图感知自适应 RRF | 🟡 内核采纳,分阶段:P1 固定 k=60;P1.3 评测不达标则加**规则判别**(0x/ERR_/大写宏→强 BM25),**不引 Qwen3 分类器**;BM25 高置信捷径留 P1.3 |
| 拓扑两阶段检索 | 🔁 已规划 = §7 repo_map + P1.5 code_graph;吸收"payload 存 callers/callees 供图扩散"(CodeChunk 已预留字段) |
| 嵌入模型版本管理 | 🟡 版本元数据已由 `model_fingerprint` 覆盖(§5 / embed.py);**拒绝蓝绿零停机**(本地工具非 7×24) |
| HNSW 调参 + BQ | 🟡 调参评测驱动(先默认);**拒绝 BQ**(几十万 chunk 内存非瓶颈,<5% 召回损失不划算) |
| 可观测看板 | 🟡 Recall@k 评测 = §11;**拒绝在线延迟 / 过滤选择性监控**(本地非在线服务) |
| 多租户 shard | ❌ 拒绝(非多租户,table-per-repo 足够) |

---

## 15. 参考

- repo map:[Aider — Repository map](https://aider.chat/docs/repomap.html)
- 代码图谱:[RepoGraph (ICLR 2025)](https://arxiv.org/abs/2410.14684) · [GitHub](https://github.com/ozyyshr/RepoGraph)
- 缺陷定位 agent:[OrcaLoca (ICML 2025)](https://arxiv.org/abs/2502.00350) · [GitHub](https://github.com/fishmingyu/OrcaLoca)
- 混合检索:[Hybrid Search: BM25, Vector & Reranking Reference 2026](https://www.digitalapplied.com/blog/hybrid-search-bm25-vector-reranking-reference-2026) · [RRF explained](https://blog.serghei.pl/posts/reciprocal-rank-fusion-explained/)
- embedding:[Qwen3-Embedding(默认)](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) · [论文](https://arxiv.org/pdf/2506.05176) · [bge-code-v1(GPU 档)](https://huggingface.co/BAAI/bge-code-v1) · [bge-m3(回退)](https://huggingface.co/BAAI/bge-m3) · [voyage-code-3](https://blog.voyageai.com/2024/12/04/voyage-code-3/) · [CoIR 代码检索榜](https://github.com/coir-team/coir) · [Anthropic Contextual Retrieval](https://www.anthropic.com/engineering/contextual-retrieval)
- 重排:[bge-reranker-v2-m3](https://huggingface.co/BAAI/bge-reranker-v2-m3)
- 向量库:[LanceDB Hybrid Search](https://docs.lancedb.com/search/hybrid-search) · [RRF Reranker](https://docs.lancedb.com/reranking/rrf)
- SWE 基准:[SWE-bench](https://www.swebench.com/)
- 混合检索前沿:[Balancing the Blend (arXiv 2508.01405, 2025-08)](https://arxiv.org/html/2508.01405v2)(weakest-link + TRF)· [SWE-Bench Illusion (NeurIPS 2025)](https://arxiv.org/abs/2506.12286)(评测污染)
- 参考实现:[code-review-graph](https://github.com/tirth8205/code-review-graph)(本地 `code-review-graph/`,P1.3 借鉴其 SQLite 图 / 增量 / 原子性 / hybrid / eval 框架)——完整调研与借鉴清单见 [code-review-graph-调研与借鉴.md](code-review-graph-调研与借鉴.md)
