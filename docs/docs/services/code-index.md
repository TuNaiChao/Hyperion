# 服务 · 代码理解(code_index)

> `services/code_index/` —— **P1 代码情报地基**。tree-sitter 解析 → 符号边界切块 → embedding → LanceDB 存储 → 混合检索 + cross-encoder 重排 → 索引编排 + 评测 + LSP 精确导航 + 结构图。
> 给 `search_codebase` MCP 工具、deep_research workflow、memory(复用 embed/rerank)共用。

## 概览

一条 L1 管线把源码变成可语义检索的向量库,再叠 LSP(clangd)做精确跳转 / 找引用,叠 CRG(code-review-graph)做结构图(社区 / hub / 影响面)。检索是**两阶段**:hybrid 召回(BM25 + 向量 + RRF)→ cross-encoder 重排。

## 源码

| 文件 | 阶段 | 职责 |
|---|---|---|
| `parser.py` | P1.0 | tree-sitter 符号抽取(`Symbol` / `GRAMMARS`,已实现 `python` + `c`) |
| `chunker.py` | P1.1 | 符号边界切块(`CodeChunk`,超长按行区间二次切) |
| `embed.py` | P1.2 | embedding(`Embedder` Protocol + 远端 / 本地两种) |
| `store.py` | P1.3 | LanceDB 存储 + 原生 hybrid(`LanceDBStore`,table-per-repo) |
| `retrieval.py` | P1.3 | 两阶段检索(`retrieve`)+ reranker 抽象 |
| `index.py` | P1.3 | 索引编排(`build_index`,全量原子 swap / 增量对账) |
| `lsp.py` | P1.5 | L2 精确导航(clangd via multilspy,自写 `ClangdServer`) |
| `code_graph.py` | R3.2 | 结构图(wrap code-review-graph) |
| `outline.py` | — | read_file 折叠摘要(BFS unfold) |
| `loc_translate.py` | — | 行区间翻译 + sticky_scroll 行号化(Agentless 复刻) |
| `skeleton.py` | — | 骨架渲染(file / function level) |
| `eval/` | P1.3 | 评测(`scorer.py` 纯函数 + `runner.py`) |

> [!NOTE]
> `__init__.py` 只 re-export `parser` / `chunker` / `embed`。其余子模块要完整路径 import,如 `from hyperion.services.code_index.retrieval import retrieve`。

## API

### 解析(parser.py)

```python
@dataclass(frozen=True)
class Symbol: name; qualified_name; kind; language; file; start_line; end_line; signature; docstring

def detect_language(path) -> str | None
def parse_file(path, language=None) -> list[Symbol]
def parse_repo(root, languages=None) -> list[Symbol]
def iter_source_files(root, languages=None)  # yields (abs_path, rel_str, lang)
```

`GRAMMARS` 是语言注册表;**加语言只动 `GRAMMARS`**(注册 tree-sitter grammar)。

### 切块(chunker.py)

```python
@dataclass
class CodeChunk: id; symbol; kind; file; language; start_line; end_line; text; content_hash; fts_text; part; total; callers; callees

MAX_CHUNK_CHARS = 16000   # 超长符号 / 模块按行区间二次切
def chunk_file(path, symbols=None) -> list[CodeChunk]
def chunk_repo(root, symbols=None) -> list[CodeChunk]
```

### embedding(embed.py)

```python
class Embedder(Protocol): dim; fingerprint; embed_chunks; embed_query; warm
class RemoteEmbedder(*, base_url, api_key, model=DEFAULT_REMOTE_MODEL, dimensions=None, batch_limit=10, normalize=True)
class LocalEmbedder(*, model="Qwen/Qwen3-Embedding-0.6B", max_seq_length=8192, device=None, batch_size=16, normalize=True, query_instruction="query", hf_endpoint="https://hf-mirror.com")
def create_embedder(cfg) -> Embedder
```

### 存储(store.py)

```python
class LanceDBStore(base_dir="data/code_index", *, db_name="lancedb"):  # table-per-repo
    def upsert(repo, chunks, vectors) -> None
    def hybrid_search(repo, query_vec, fts_query, limit, where=None) -> list[dict]
    def optimize(repo); drop_repo(repo); count(repo) -> int
```

**存储栈**:LanceDB 嵌入式 + Tantivy FTS(`stem=False, remove_stop_words=False` 专为代码)+ 向量 ANN + `RRFReranker(K=60)`。

### 检索(retrieval.py)★两阶段

```python
def retrieve(query, repo, embedder, store, reranker=None, *, top_k=5, candidate_top_n=50, where=None) -> RetrievalResult
```

```
Stage 1 hybrid 召回:  BM25(FTS) + 向量 ANN  ──RRF(K=60)──▶  top-50 候选
Stage 2 精排:         cross-encoder rerank  ──▶  top-k
                         └─ reranker 失败 → 降级用 hybrid 序(out_mode=rerank-failed:hybrid)
```

`RetrievalResult` 带 `out_mode`:`hybrid+rerank` / `hybrid` / `rerank-failed:hybrid` / `empty`。

```python
class Reranker(Protocol): rerank(query, documents, top_n) -> list[tuple[int, float]]
class RemoteReranker(*, base_url, api_key, model, shape="dashscope", top_n=5, timeout=60.0)   # dashscope 嵌套 / cohere 扁平
class LocalReranker(*, model="BAAI/bge-reranker-v2-m3", device=None, hf_endpoint=...)
def create_reranker(cfg) -> Reranker | None   # provider: dashscope/siliconflow/cohere/jina/sentence_transformers/off
```

### 索引编排(index.py)

```python
SCHEMA_VERSION = 1
def build_index(repo_path, repo_name, embedder, base_dir="data/code_index", *, force=False, batch_size=64) -> dict
```

全量(temp 目录原子 swap + 崩溃恢复)或增量(sha256 对账;model / schema 指纹变触发全量)。返回 `{mode, indexed/total_chunks, repo_commit, ...}`。

### L2 导航(lsp.py)

```python
def find_clangd(config=None) -> str | None
def lsp_health(repo_root) -> LSPHealth        # LSPHealth.ok / .render()
class ClangdServer(LanguageServer)            # 自写 clangd 适配器(multilspy 0.0.15 无 clangd)
def get_lsp_server(repo_root) -> SyncLanguageServer   # 进程级常驻单例
def reset_lsp_server(repo_root=None)
```

主要操作:`request_references` / `request_definition` / hover(经 `SyncLanguageServer`)。

### 结构图(code_graph.py)

```python
class CodeGraph:
    @classmethod
    def build(cls, repo_root, repo_name, *, base_dir="data/structgraph", min_community_size=2) -> CodeGraph
    @classmethod
    def open(cls, repo_name, *, base_dir="data/structgraph") -> CodeGraph
    def architecture_overview() -> dict
    def communities() -> list[dict]
    def hub_nodes(top_n=15); bridge_nodes(top_n=15)
    def impact_radius(changed_files) -> dict
    def analyze_changes(changed_files, *, changed_ranges=None, repo_root=None, base="HEAD~1", include_churn=False) -> dict  # 六因子 risk_score
    def community_ids_for(qualified_names) -> dict
    def stats() -> dict
```

db 落 `data/structgraph/<repo>/graph.db`。**可选依赖**:`uv sync --extra code-review-graph`(没装 `_require_crg` 抛清晰 ImportError)。

### 导航辅助(outline / loc_translate / skeleton)

```python
def summarize_file(path) -> Summary | None          # outline.py,折叠摘要(BFS unfold)
def transfer_locs(loc_lines, symbols, *, context_window=10) -> list[tuple[int,int,str]]   # loc_translate.py
def line_wrap_content(source, intervals, *, sticky_scroll=True, symbols=None) -> str       # VSCode 式 sticky_scroll
def render_file_tree(files, *, max_files=None) -> str    # skeleton.py
def render_skeleton(symbols, *, max_sig_chars=120) -> str
```

### 评测(eval/)

`scorer.py`:`recall_at_k` / `precision_at_k` / `hit_rate_at_k` / `reciprocal_rank` / `ndcg_at_k` / `acc_at_k` / `mean_metrics` / `group_metrics`(纯函数,多标签 BEIR 标准)。
`runner.py`:`load_eval_set(path)` / `run_eval(...)` / `format_report(report)`(失败语义:单条 error 排除,空 gold 排除)。

## 流程(建索引)

```
parse_repo ──▶ chunk_repo ──▶ embed_chunks(分批)──▶ LanceDBStore.upsert
                                                      + 写 IndexManifest(指纹 / sha256 / commit)
```

## 流程(检索)

见上「两阶段」图。`search_codebase` MCP 工具调 `retrieve`,只回索引里真实存在的符号(emit-concept 防幻觉)。

## 配置

见 [configuration.md](../configuration.md) §code_index(embedding / retrieval / reranker / lsp)。

## 边界与限制

- **已实现语言**:`python` + `c`。加语言改 `GRAMMARS`。
- **LSP 强依赖 `compile_commands.json`**:没有它 references 质量骤降(autotools 用 `bear -- make V=1`;cmuse 用 `cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON`)。
- **`fts_stem` / `fts_remove_stop_words` 必须关**:代码场景否则 `malloc` / `int` / `void` 会被破坏。
- reranker 失败会降级用 hybrid 序(不报错,看 `out_mode`)。
- CRG 是可选 extra,不装时 `code_graph` 相关功能不可用(`blast_radius` 工具会返回提示)。

## 示例

```python
from hyperion.services.code_index.embed import create_embedder
from hyperion.services.code_index.index import build_index
from hyperion.services.code_index.retrieval import retrieve, create_reranker
from hyperion.services.code_index.store import LanceDBStore

embedder = create_embedder(cfg.code_index.embedding)
build_index("~/src/wpa_supplicant", "wpa_supplicant", embedder, force=True)

store = LanceDBStore()
reranker = create_reranker(cfg.code_index.reranker)
res = retrieve("scan result handler", "wpa_supplicant", embedder, store, reranker, top_k=5)
```

## See Also

- [../tools/mcp-tools.md](../tools/mcp-tools.md) — `search_codebase` / `blast_radius`
- [../workflows/deep-research.md](../workflows/deep-research.md) — 用 code_index + CRG 做模块调研
- [../operations/inspect-code-index.md](../operations/inspect-code-index.md) — 查看 LanceDB 数据
- [../configuration.md](../configuration.md) §code_index
- 上级 [../调研/向量数据库设计分析报告.md](../../调研/向量数据库设计分析报告.md) — 选型决策
