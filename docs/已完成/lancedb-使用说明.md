# LanceDB 使用说明(Hyperion 视角)

> 这篇讲两件事:**怎么查看 Hyperion 存在 LanceDB 里的数据** + **LanceDB 在本项目怎么用**。
> 版本对照:LanceDB 0.34(已 live 核实,见 [设计文档 §6 决策 #8](../设计/p1-code-understanding-design.md))。选型理由见 [向量数据库设计分析报告](../调研/向量数据库设计分析报告.md)。

---

## 0. LanceDB 是什么(30 秒)

**嵌入式向量库**:进程内运行、零 server、表就是文件目录(随 git/rsync 跨机)。Hyperion 用它存代码 chunk 的向量 + 做 BM25/向量混合检索。同类对比(Qdrant/Milvus 要常驻 server;纯 SQLite-BLOB 线性扫大仓不可用)见向量库报告。

---

## 1. 数据存在哪

```
data/code_index/<repo>/
├── lancedb/                 # LanceDB 数据库目录(embedded)
│   ├── chunks.lance         # 「chunks」表(一个仓库一张表,table-per-repo)
│   └── __manifest           # LanceDB 自身的库清单
└── index_manifest.json      # Hyperion 的索引清单 sidecar(见下)
```

- **每个仓库一张表**(`chunks`),独立目录 → 物理隔离,可单独 rsync/删除/重建。
- `index_manifest.json` 是 Hyperion 自己写的 sidecar(`index.py` 落盘),内容:
  ```json
  {
    "repo_commit": "4e4fb26...",           // 建库时锁定的 git commit(评测基线/staleness)
    "model_fingerprint": "openai_compatible|text-embedding-v4|...|1024|l2",  // embedding 指纹,变→全量重建
    "schema_version": 1,                   // chunk schema 版本
    "file_manifest": { "buffer.py": "5cf9f1...", ... }  // {相对路径: sha256},增量对账
  }
  ```
- `data/` 在 `.gitignore` 里,**不入库**(随机器走,不进 GitHub)。

---

## 2. 怎么查看数据(常用配方)

打开一个 Python shell(`uv run python`),连库 → 看表。下面用 demo 索引(`data/code_index/demo/`)的真实输出演示。

### 2.1 连库 + 看表 + 行数 + schema

```python
import lancedb
db = lancedb.connect("data/code_index/demo/lancedb")
print(db.table_names())          # ['chunks']
tbl = db.open_table("chunks")
print(tbl.count_rows())          # 5
print(tbl.schema)
# id: string / symbol: string / kind: string / file: string / language: string
# start_line: int32 / end_line: int32 / text: string / content_hash: string
# fts_text: string / vector: fixed_size_list<item: float>[1024]   ← dim 建表时定死
```

### 2.2 看全部行(★ 别用 to_pandas,用 to_arrow().to_pylist())

> ⚠️ Hyperion 没装 pandas。`tbl.to_pandas()` 会报 `ModuleNotFoundError: No module named 'pandas'`。
> 浏览全行用 `tbl.to_arrow().to_pylist()`(返回 `list[dict]`,零依赖):

```python
for r in tbl.to_arrow().to_pylist():
    print(f"{r['kind']:8} {r['symbol']:20} {r['file']:14} L{r['start_line']}-{r['end_line']}")
# function allocate_buffer      buffer.py      L1-3
# function disconnect_cb        adapter.py     L1-3
# function init_adapter         adapter.py     L5-7
# function normalize_addr       utils.py       L1-3
# function free_buffer          buffer.py      L5-7
```

### 2.3 看一条完整行(含向量)

```python
row = [r for r in tbl.to_arrow().to_pylist() if r["symbol"] == "disconnect_cb"][0]
for k, v in row.items():
    if k == "vector":
        print(f"  {k}: [{v[0]:.3f}, {v[1]:.3f}, ... ] {len(v)} 维")
    else:
        print(f"  {k}: {v}")
# id: adapter.py:disconnect_cb      ← chunk 主键(决策 #8:不含行号)
# symbol: disconnect_cb
# kind: function
# file: adapter.py
# text: def disconnect_cb(dev): """device disconnect callback..."""
# content_hash: 7ac20d9a...         ← text 的 sha256,增量判变
# fts_text: disconnect cb disconnect cb ...   ← BM25 词袋(chunker 预拆 snake/camel)
# vector: [0.000, 0.000, ...] 1024 维
```

### 2.4 搜索(三种,分数字段名不同!)

> ⚠️ **三路搜索的分数字段名不一样,别混用**:

| 搜索 | 调用 | 分数字段 | 排序方向 |
|---|---|---|---|
| **FTS(BM25)** | `tbl.search("buffer", query_type="fts").limit(3).to_list()` | `_score` | **越大越相关** |
| **向量(ANN)** | `tbl.search(qvec).limit(3).to_list()` | `_distance` | **越小越相似** |
| **hybrid(BM25+向量+RRF)** | `tbl.search(query_type="hybrid", vector_column_name="vector", fts_columns="fts_text").vector(qvec).text("buffer").limit(50).rerank(RRFReranker(K=60)).to_list()` | `_relevance_score` | 越大越相关 |

```python
# FTS(关键词):搜 'buffer' 命中 free_buffer / allocate_buffer
for r in tbl.search("buffer", query_type="fts").limit(3).to_list():
    print(r["symbol"], r["_score"])     # free_buffer 1.69 / allocate_buffer 1.658

# 向量(语义):最近邻(注意 _distance 越小越像)
for r in tbl.search(qvec).limit(3).to_list():
    print(r["symbol"], r["_distance"])  # free_buffer 0.400 / allocate_buffer 0.658
```

### 2.5 看索引

```python
for idx in tbl.list_indices():       # ★ 方法是 list_indices(),不是 list_indexes()
    print(idx.name, idx.columns, idx.index_type)
# 预期:id 上的 BTree(merge_insert 必须)+ fts_text 上的 FTS(Tantivy BM25)
```

### 2.6 按 SQL 过滤 / 删行

```python
# 按 file/kind 过滤(0.34 起多次 .where() 叠加 = AND)
tbl.search(qvec).where("kind = 'function'", prefilter=True).limit(5).to_list()

# 删行(SQL 谓词)
tbl.delete("file = 'buffer.py'")     # 删某文件的所有 chunk

# upsert(条件:content_hash 变了才更新)
tbl.merge_insert("id") \
   .when_matched_update_all(where="target.content_hash <> source.content_hash") \
   .when_not_matched_insert_all() \
   .execute(rows)
```

---

## 3. Hyperion 怎么用 LanceDB(别手搓,走封装)

日常**不要直接写 lancedb 调用**,走 Hyperion 的封装(它们处理了 schema/索引/原子性/reranker):

| 想做什么 | 用什么 |
|---|---|
| **建/更新索引** | `index.py: build_index(repo_path, repo_name, embedder, base_dir)` —— 全量(temp 目录原子 swap)+ 增量(content_hash 短路)+ manifest,自动建 BTree/FTS 索引 |
| **检索** | `retrieval.py: retrieve(query, repo, embedder, store, reranker)` —— hybrid+RRF 候选 → cross-encoder 重排,返回 `RetrievalResult(hits, out_mode)` |
| **存/查句柄** | `store.py: LanceDBStore(base_dir)` —— `upsert / hybrid_search / count / optimize / drop_repo`;`VectorStore` Protocol 留 Qdrant 扩展性 |
| **embedding** | `embed.py: create_embedder(cfg)` —— DashScope text-embedding-v4(远端默认)/ 本地可选 |

一键建 demo + 查看(已在本机跑过):
```bash
# demo 已在 data/demo_repo + data/code_index/demo(假 embedder,免费)
uv run python -c "
import lancedb
t = lancedb.connect('data/code_index/demo/lancedb').open_table('chunks')
print('行数:', t.count_rows())
for r in t.to_arrow().to_pylist(): print(' ', r['kind'], r['symbol'], r['file'])
"
```

---

## 4. 配置

`config/config.yaml` 的 `code_index` 段(`base_dir` 默认 `data/code_index`):
```yaml
code_index:
  embedding: { provider: openai_compatible, model: text-embedding-v4, ... }   # embed.py
  retrieval: { rrf_k: 60, candidate_top_n: 50, final_top_k: 5, fts_stem: false, fts_remove_stop_words: false, query_boost: true }
  reranker:  { provider: dashscope, base_url: https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank, model: qwen3-rerank, ... }
```

---

## 5. 坑 / 注意(LanceDB 0.34 实测)

| 坑 | 正确做法 |
|---|---|
| `tbl.to_pandas()` 报无 pandas | 用 `tbl.to_arrow().to_pylist()`(本项目没装 pandas) |
| 找 `list_indexes` 找不到 | 方法是 **`list_indices()`** |
| FTS / 向量 / hybrid 分数字段混用 | FTS=`_score`(大好)、向量=`_distance`(小好)、hybrid=`_relevance_score`(大好) |
| `create_scalar_index` / `create_fts_index` 弃用警告(0.25+) | 新 API:`create_index("id", config=BTree())` / `create_index("fts_text", config=FTS(stem=False, remove_stop_words=False, with_position=True))`(见 `store.py`) |
| `merge_insert` 报 "unindexed rows > 10000" | 必须先在 id 建 BTree scalar 索引(`store.py` 已做) |
| upsert 后新行搜不到/慢 | bulk 写后调 `tbl.optimize()` 折叠进索引(`store.py` 已做) |
| 改 embedding 模型/维度 | 维度建表时定死,换模型 = 新建表全量迁移;`index.py` 靠 `model_fingerprint` 检测→自动触发全量重建 |
| FTS 代码场景参数 | `stem=False`(否则 malloc 被 stem)、`remove_stop_words=False`(否则 int/void/public/return 被删!) |
| 向量度量 | 已 L2 归一化 → 用 `dot`;建 IVF 时定 `metric="dot"` |
| 单写者 | LanceDB 写要串行;多进程 `spawn` 非 `fork` |

---

## 6. 清理 demo 数据

demo 是假 embedder 建的(只为本文档演示),可随时删:
```bash
rm -rf data/demo_repo data/code_index/demo
```

---

## 7. 参考

- LanceDB 官方文档:https://docs.lancedb.com/(hybrid search / reranking / merge_insert / FTS index)
- 本项目实现:`src/hyperion/services/code_index/{store,index,retrieval,embed}.py`
- 选型与设计:[向量数据库设计分析报告.md](../调研/向量数据库设计分析报告.md)、[P1 设计报告 §6/§14](../设计/p1-code-understanding-design.md)
