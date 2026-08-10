# 运维 · 查看 LanceDB 代码索引数据

> 代码索引(code_index)的数据落在磁盘上的 **LanceDB** 嵌入式向量库。这篇讲**怎么直接连库看里面存了什么**(排查检索结果、确认索引是否建好、手动删 / 重建某个仓库的索引)。
> 版本:LanceDB 0.34。日常建索引 / 检索**走封装**(见末节),别手搓 lancedb 调用;本文仅用于运维查看。

## LanceDB 是什么(30 秒)

**嵌入式向量库**:进程内运行、零 server、表就是文件目录(可随 git/rsync 跨机)。Hyperion 用它存代码 chunk 的向量 + 做 BM25 / 向量混合检索。对比:Qdrant / Milvus 要常驻 server;纯 SQLite-BLOB 线性扫大仓不可用。

## 数据存在哪

```
data/code_index/<repo>/
├── lancedb/                 # LanceDB 数据库目录(embedded)
│   ├── chunks.lance         # 「chunks」表(一个仓库一张,table-per-repo)
│   └── __manifest           # LanceDB 库清单
└── index_manifest.json      # Hyperion 的索引 sidecar(见下)
```

- **每个仓库一张表**(`chunks`),独立目录 → 物理隔离,可单独 rsync / 删除 / 重建。
- `index_manifest.json` 是 Hyperion 自己写的 sidecar(`index.py` 落盘):

```json
{
  "repo_commit": "4e4fb26...",
  "model_fingerprint": "openai_compatible|text-embedding-v4|...|1024|l2",
  "schema_version": 1,
  "file_manifest": { "buffer.py": "5cf9f1...", "...": "..." }
}
```

  `repo_commit`(建库时锁定的 git commit,评测基线 / staleness)、`model_fingerprint`(embedding 指纹,变 → 全量重建)、`file_manifest`(`{相对路径: sha256}`,增量对账)。

- `data/` 在 `.gitignore`,**不入库**(随机器走,不进 GitHub)。

## 查看配方(`uv run python` 起一个 shell)

### 连库 + 看表 + 行数 + schema

```python
import lancedb
db = lancedb.connect("data/code_index/<repo>/lancedb")
print(db.table_names())          # ['chunks']
tbl = db.open_table("chunks")
print(tbl.count_rows())          # 表行数
print(tbl.schema)
# id: string / symbol: string / kind: string / file: string / language: string
# start_line: int32 / end_line: int32 / text: string / content_hash: string
# fts_text: string / vector: fixed_size_list<item: float>[1024]   ← dim 建表时定死
```

### 浏览全部行(★ 别用 to_pandas,用 to_arrow().to_pylist())

> [!WARNING]
> Hyperion **没装 pandas**。`tbl.to_pandas()` 会报 `ModuleNotFoundError: No module named 'pandas'`。
> 浏览全行用 `tbl.to_arrow().to_pylist()`(返回 `list[dict]`,零依赖):

```python
for r in tbl.to_arrow().to_pylist():
    print(f"{r['kind']:8} {r['symbol']:20} {r['file']:14} L{r['start_line']}-{r['end_line']}")
```

### 看一条完整行(含向量)

```python
row = [r for r in tbl.to_arrow().to_pylist() if r["symbol"] == "my_func"][0]
for k, v in row.items():
    if k == "vector":
        print(f"  {k}: [{v[0]:.3f}, {v[1]:.3f}, ... ] {len(v)} 维")
    else:
        print(f"  {k}: {v}")
```

### 搜索(三种,分数字段名不同!)

> [!WARNING]
> 三路搜索的分数字段名**不一样,别混用**:

| 搜索 | 调用 | 分数字段 | 排序方向 |
|---|---|---|---|
| **FTS(BM25)** | `tbl.search("buffer", query_type="fts").limit(3).to_list()` | `_score` | **越大越相关** |
| **向量(ANN)** | `tbl.search(qvec).limit(3).to_list()` | `_distance` | **越小越相似** |
| **hybrid**(BM25+向量+RRF) | `.search(query_type="hybrid", vector_column_name="vector", fts_columns="fts_text").vector(qvec).text("buffer").limit(50).rerank(RRFReranker(K=60)).to_list()` | `_relevance_score` | 越大越相关 |

```python
from lancedb.rerankers import RRFReranker
# FTS(关键词):搜 'buffer'
for r in tbl.search("buffer", query_type="fts").limit(3).to_list():
    print(r["symbol"], r["_score"])

# 向量(语义):最近邻(_distance 越小越像)
for r in tbl.search(qvec).limit(3).to_list():
    print(r["symbol"], r["_distance"])
```

### 看索引 / 过滤 / 删行

```python
# 看索引(★ 方法是 list_indices(),不是 list_indexes())
for idx in tbl.list_indices():
    print(idx.name, idx.columns, idx.index_type)
# 预期:id 上 BTree(merge_insert 必须)+ fts_text 上 FTS(Tantivy BM25)

# 按 SQL 过滤(0.34 起多次 .where() 叠加 = AND)
tbl.search(qvec).where("kind = 'function'", prefilter=True).limit(5).to_list()

# 删行(SQL 谓词)
tbl.delete("file = 'buffer.py'")     # 删某文件的所有 chunk
```

## 日常走封装(别手搓)

| 想做什么 | 用什么 |
|---|---|
| **建/更新索引** | `index.py: build_index(repo_path, repo_name, embedder, base_dir)` —— 全量(temp 目录原子 swap)+ 增量(content_hash 短路)+ manifest,自动建 BTree/FTS 索引 |
| **检索** | `retrieval.py: retrieve(query, repo, embedder, store, reranker)` —— hybrid+RRF 候选 → cross-encoder 重排 |
| **存/查句柄** | `store.py: LanceDBStore(base_dir)` —— `upsert / hybrid_search / count / optimize / drop_repo` |
| **CLI 建索引** | `uv run hyperion index <repo_path> <name>` |

## 常见坑(LanceDB 0.34 实测)

| 坑 | 正确做法 |
|---|---|
| `tbl.to_pandas()` 报无 pandas | 用 `tbl.to_arrow().to_pylist()`(本项目没装 pandas) |
| 找 `list_indices` 找不到 | 方法是 **`list_indices()`** |
| FTS / 向量 / hybrid 分数字段混用 | FTS=`_score`(大好)、向量=`_distance`(小好)、hybrid=`_relevance_score`(大好) |
| `merge_insert` 报 "unindexed rows > 10000" | 必须先在 id 建 BTree scalar 索引(`store.py` 已做) |
| upsert 后新行搜不到 / 慢 | bulk 写后调 `tbl.optimize()` 折叠进索引(`store.py` 已做) |
| 改 embedding 模型 / 维度 | 维度建表时定死,换模型 = 全量迁移;`index.py` 靠 `model_fingerprint` 检测 → 自动触发全量重建 |
| FTS 代码场景参数 | `stem=False`(否则 malloc 被 stem)、`remove_stop_words=False`(否则 int/void/public/return 被删) |
| 单写者 | LanceDB 写要串行;多进程 `spawn` 非 `fork` |

## 删 / 重建某个仓库的索引

```bash
# 删一个仓库的全部索引数据
uv run python -c "
from hyperion.services.code_index.store import LanceDBStore
LanceDBStore('data/code_index').drop_repo('wpa')   # 或直接 rm -rf data/code_index/wpa
"
# 重建
uv run hyperion index example/demo2/wpa wpa
```

## 参考

- LanceDB 官方文档:<https://docs.lancedb.com/>(hybrid search / reranking / merge_insert / FTS index)
- 实现:[../../../src/hyperion/services/code_index/store.py](../../../src/hyperion/services/code_index/store.py)

## See Also

- [../services/code-index.md](../services/code-index.md) — code_index 模块全貌
- [inspect-memory.md](inspect-memory.md) — 查看记忆库(对比)
