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
    id: str               # 稳定主键 f"{file}:{qualified_name}:{start_line}"(分段加 ":p{N}")
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
- **chunk_expansion(嵌入元数据头)**留到 P1.2 `embed.py`:嵌入时拼 `file|symbol|kind` 头 + 代码,让向量带上下文。
- **id 用 `qualified_name`** 消歧同类同名;**callers/callees** 留 P1.5 code_graph 回填。

---

## 5. Embedding(`embed.py`) [P1.2]

**决策:默认 `BAAI/bge-m3`(本地/免费,8K 上下文)**;`voyage-code-3` 作为付费可选(代码专用、更强)。

| 维度 | **bge-m3(默认)** | bge-large-en-v1.5(已否决) | voyage-code-3(可选) |
|---|---|---|---|
| 部署 | 本地(sentence-transformers) | 本地 | API(付费,需 voyage key) |
| 上下文 | **8192 token(整函数内)** | 512(长函数截断) | 32K |
| 检索模式 | dense + sparse + 多向量(P1 只用 dense;sparse 留作后续替/补 BM25) | dense only | dense only |
| query 前缀 | 不需要 | 需要 "Represent this..." 前缀 | 不需要 |
| 代码专用 | 否(通用多语,但 8K + 多功能) | 否 | 是(代码基准 +14~17%) |
| 代价 | 0 费用,但模型 ~2.3GB、**CPU 推理较慢**(批 15~20s,首建见 §10) | ~1.3GB、较快 | 按 token 付费 |

**选 bge-m3 的关键理由**:C 长函数(状态机、init 函数动辄 200+ 行)在 512 token 下会被截断、向量失准;bge-m3 的 **8K 上下文一次装下整函数**——这是 C 场景的硬需求,也是审查中否决 bge-large-en-v1.5 的主因。

**铁律:embedding 模型选定后不能换——换要全量重嵌**。`index.py` 存模型指纹(`model_name + 维度`),与 LanceDB 表的元数据比对,变更时触发重建。

依赖:新增 `sentence-transformers>=2.7`(bge-m3 + bge-reranker 都走它;`pyproject.toml` 里 tree-sitter / lancedb / rank-bm25 已声明)。

---

## 6. 向量库与混合检索(`store.py` + `retrieval.py`) [P1.2-P1.3]

**选 LanceDB,且用其原生混合检索**(避免自搓 BM25+RRF 胶水):

```python
# 伪代码 —— LanceDB 原生 hybrid
tbl.create_fts_index("fts_text")           # 显式建 FTS 索引(Tantivy BM25)
q = (tbl
     .search(query_text, query_type="hybrid")  # 同时跑 FTS(BM25) + 向量
     .rerank(RRFReranker(k=60))                 # 倒排融合,模型无关
     .limit(50))
# ⚠️ 默认【不】按 kind 预过滤——struct/宏定义常是关键上下文(如"设备状态结构体"),
#    一刀切 kind='function' 会丢掉它们。仅当查询显式带 type:function/struct 时才:
#       q = q.where("kind = 'function'", prefilter=True)
# reranker 的 metadata 里带 kind,由 cross-encoder 自己判断重要性。
# 再用 bge-reranker-v2-m3 把 top-50 重排到 top-5
```

- **BM25(FTS)**:对 C 的函数名/宏名/错误码(强词法信号)召回好——这是"必须混合"的根因。靠 §4 的 `fts_text` 预拆词绕过 Tantivy 默认 tokenizer 对 `snake_case` 的不友好。
- **向量**:语义相似(如"断连处理" ↔ `disconnect_cb`)。
- **RRF**:`score = Σ 1/(k + rank)`,`k=60`(架构 §5.1,业界默认)。
- **reranker**:`BAAI/bge-reranker-v2-m3` cross-encoder,只对 top-50,取 top-5。

> 备用:若 LanceDB FTS 对 C 标识符召回仍不足,再引入 `rank_bm25`(已在依赖)做二级精排,或启用 bge-m3 的 sparse 输出。P1 先用原生,简化到单库。

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
  embedding:
    provider: sentence_transformers    # 或 voyageai
    model: BAAI/bge-m3                 # 选定不换;换则触发全量重建
  vector_store:
    path: data/code_index/lancedb
  retrieval:
    rrf_k: 60
    rerank_top_n: 50
    final_top_k: 5
    reranker: BAAI/bge-reranker-v2-m3
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

LanceDB 写入**不是跨批次原子**的:建库中途崩(磁盘满 / 进程被杀)会留半截脏表,检索直接不可用。生产级必须解决:

- **temp 表 + 原子 rename**:`index build` 先写影子表 `lancedb_tmp`,**全部成功**后原子 rename 成正式表,失败即丢弃 tmp——库里要么是上一份完整旧索引、要么是新索引,无半成品。
- **`index_manifest.json`**(建库成功后落盘,检索前校验):
  - `repo_commit`:建库时仓库锁定的 commit(评测基线 / staleness 判定)。
  - `model_fingerprint`:`embedding.model + 维度`(变 → 全量重建)。
  - `schema_version`:chunk schema 版本(改 → 全量重建)。
  - `file_manifest`:`{相对路径: content_hash}` —— 增量更新的对账依据。
- **重命名 / 移动**:chunk id 含 `file`,改名让旧 id 孤儿。对账时:消失路径删、新增嵌、hash 变重嵌;或用 `git diff --name-status` 的 `R`(rename)状态显式搬运。

---

## 11. 评测(`eval/`,P1.3 起)

- **Ground truth**:从 bluez git 历史用 `git log --grep="Fixes:"` + CVE 批量提取 fix commit。
- **fix → 符号映射(关键,行级而非符号列表 diff)**:⚠️ **不要**对父/子提交各跑一次 parser 再 diff 符号列表——若某 fix 只改了函数体内几行,父子提交的**符号集合完全相同**,symbol-diff 抓不到被修的函数。正确做法是**行级映射**:① `git diff <parent> <fix>` 拿到改动文件 + 行范围(hunks);② 对**子提交**跑一次 parser;③ 把改动行映射到**包住它的最内层符号**(函数/struct/全局变量/宏)。这组符号 = ground truth。
- **难度分层 + 负例**(让 0.6 recall 有说服力):查询分三档分别报 recall——
  - **L1**:查询含正确函数名(精确,基线)。
  - **L2**:只描述行为 / 日志 / 错误现象(语义检索真正考的档)。
  - **L3**:跨模块 / 多跳调用链影响(需 code_graph,P1.5 后评)。
  另配**负例查询**(与 bug 无关的提问),报 precision,防过拟合"修复型语义"。
- **指标**:Top-1/3/5 Accuracy、MFR(首正确排名)、MAR(平均排名);**P1.3 退出量化标准:L2 档 top-5 recall ≥ 0.6**(L1 应更高,L3 待 P1.5)。
- **做法**:给 agent 某 bug 的症状/日志 → 收集 `search_code` 返回的 top-N → 算是否命中 fix 改动行的所属符号。

---

## 12. 风险与对策

| 风险 | 对策 |
|---|---|
| C 宏/条件编译/头文件解析不全 | tree-sitter 容错 + 一并索引 .h + ctags 补宏;必要时 clangd |
| **C 调用/符号解析不准(P1.5)** | static 同名带文件作用域;宏包装/函数指针可能要 clangd;**P1.5 前出子调研**(见 §3) |
| embedding 模型锁定后想换 | config 锁定 + 模型指纹检测 → 变更触发全量重建 |
| **bge-m3 CPU 推理慢(首建几十分钟)** | 分批 embed + 进度 + 可选 GPU;日常只增量更新(§10) |
| 全量索引慢 / 日常 stale | 增量更新:按 `content_hash` 只重嵌变化 chunk(§10) |
| LanceDB FTS 对 C 标识符召回弱 | `fts_text` 子串拆词(snake/camel/SCREAMING);不足则启用 bge-m3 sparse 或 rank_bm25 |
| 调用图同名函数冲突 | 文件作用域消歧 + static/extern 标注;P1 先粗后细 |
| LanceDB 运行时兼容(ABI / glibc / tantivy) | wheel 已覆盖 cp39-abi3(含 3.12)+ macOS arm64,无需编译;`scripts/setup.sh` 加 `import lancedb` 冒烟测早暴露运行时问题 |
| tree-sitter 并发解析 | parser 单实例非线程安全;并发建索引走 `multiprocessing`(非 threading),每 worker 各持一个 Parser。P1 单进程顺序建索引即可 |

---

## 13. 决策记录

1. **embedding 默认模型 → `BAAI/bge-m3`**(本地/免费/8K 上下文,解决 C 长函数截断)。选定锁定,换则全量重建。
2. **P1.5(repo map + 代码图谱)延后**——P1 主范围先做扎实 P1.0-P1.4(检索 top-5 召回);repo map/调用图等 Bug-RCA 真跑起来再加。届时先按 §3 做 C 解析子调研。
3. **(v2.2)grammar 包换路**:弃 `tree-sitter-language-pack` 1.x(运行时按需从 GitHub releases 下载语法,国内网络 timeout 不可用)→ 改用**每语言独立 grammar 包**(`tree-sitter-python` / 后续 `tree-sitter-c`),wheel 内置、零联网、可 pin。并发建索引用 `multiprocessing`(parser 单实例非线程安全),P1 单进程顺序建索引即可。
4. **(v2.2)评审吸收**:采纳——索引原子性 + 状态清单(§10)、评测**行级映射** + 难度分层 + 负例(§11)、fts_text 加注释/docstring(§4)、检索**取消默认 type 预过滤**(§6)、repo map 入口点加权(§7)、图持久化 SQLite+networkx(§3)。纠正——LanceDB wheel 已覆盖 3.12/macOS arm64,无需编译(§12)。
5. **(2026-07-24)P1.1 切块落地**:对照 [cAST](https://arxiv.org/html/2506.15655v1)(EMNLP 2025)调研后——符号边界切块取 cAST 的 split、不取跨符号 merge;新增模块级 chunk 兜底(import/常量)保覆盖率 100%;docstring 抽取归 parser(parse-once);超大符号 AST 切分 + 前导注释抽取延后到 C 场景(bluez);chunk_expansion 元数据头留 P1.2 embed;chunk 大小用非空白字符数。详见 §4「P1.1 落地决策」。

---

## 14. 参考

- repo map:[Aider — Repository map](https://aider.chat/docs/repomap.html)
- 代码图谱:[RepoGraph (ICLR 2025)](https://arxiv.org/abs/2410.14684) · [GitHub](https://github.com/ozyyshr/RepoGraph)
- 缺陷定位 agent:[OrcaLoca (ICML 2025)](https://arxiv.org/abs/2502.00350) · [GitHub](https://github.com/fishmingyu/OrcaLoca)
- 混合检索:[Hybrid Search: BM25, Vector & Reranking Reference 2026](https://www.digitalapplied.com/blog/hybrid-search-bm25-vector-reranking-reference-2026) · [RRF explained](https://blog.serghei.pl/posts/reciprocal-rank-fusion-explained/)
- embedding:[bge-m3](https://huggingface.co/BAAI/bge-m3) · [bge-m3 论文](https://arxiv.org/html/2402.03216v3) · [voyage-code-3](https://blog.voyageai.com/2024/12/04/voyage-code-3/)
- 重排:[bge-reranker-v2-m3](https://huggingface.co/BAAI/bge-reranker-v2-m3)
- 向量库:[LanceDB Hybrid Search](https://docs.lancedb.com/search/hybrid-search) · [RRF Reranker](https://docs.lancedb.com/reranking/rrf)
- SWE 基准:[SWE-bench](https://www.swebench.com/)
