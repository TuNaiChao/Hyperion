# 运维 · 查看记忆库数据(SQLite)

> 记忆(P3)的 native 后端落在磁盘上的 **SQLite** 库。这篇讲**怎么连库看记忆里存了什么**(排查召回结果、确认 ingest 是否生效、看 confidence / bi-temporal、手动失效一条)。
> 数据库是标准 SQLite,**任何 SQLite 工具**都能开(`sqlite3` CLI / DB Browser / TablePlus / Python `sqlite3`)。

## 数据存在哪

```
data/memory/memory.db        # SQLite(默认;config.memory.store_path / db_name)
```

- 配置:`config/config.yaml` → `memory.store_path: data/memory`(目录)+ `MemoryStore(store_path, db_name="memory.db")`。
- `data/` 在 `.gitignore`,**不入库**。
- 一个进程复用一条连接(`check_same_thread=False, isolation_level=None`,自动提交)。

## schema(知识项主表 `knowledge_items`)

```sql
CREATE TABLE knowledge_items (
    id          TEXT PRIMARY KEY,        -- 稳定 id(sha256(scope+kind+content_key)[:16])
    kind        TEXT NOT NULL,           -- codebase_fact | bug_lesson | mental_model
    repo        TEXT NOT NULL,
    owner       TEXT NOT NULL,           -- scope 一半(租户)
    codebase    TEXT NOT NULL,           -- scope 一半(租户)
    summary     TEXT NOT NULL,           -- 人读摘要(检索 + 注入核心)
    detail      TEXT NOT NULL DEFAULT '',
    symptom     TEXT NOT NULL DEFAULT '',        -- bug_lesson:症状
    root_cause  TEXT NOT NULL DEFAULT '',        -- bug_lesson:根因
    fix_patch   TEXT NOT NULL DEFAULT '',        -- bug_lesson:修法
    blast_radius_files TEXT DEFAULT '[]',        -- JSON
    kind_detail TEXT NOT NULL DEFAULT 'module',  -- codebase_fact
    commit_sha  TEXT,
    evidence    TEXT NOT NULL DEFAULT '[]',      -- JSON [Evidence]
    source      TEXT NOT NULL DEFAULT '',
    source_tier TEXT NOT NULL DEFAULT 'unknown', -- imported | stated | inferred | unknown
    confidence  REAL NOT NULL DEFAULT 0,
    access_count INTEGER NOT NULL DEFAULT 0,     -- 被召回次数(升级 mental_model 依据)
    last_recalled TEXT,
    valid_at    TEXT NOT NULL,           -- bi-temporal:事实真起点
    invalid_at  TEXT,                    -- bi-temporal:失效点(NULL=仍有效)
    created_at  TEXT NOT NULL,
    related     TEXT NOT NULL DEFAULT '[]',      -- JSON [ki_id]
    tags        TEXT NOT NULL DEFAULT '[]',      -- JSON [str]
    superseded_by TEXT,                  -- 被哪条取代(NULL=当前版本)
    embedding   BLOB,                    -- float32 向量(NULL=没算)
    updated_at  TEXT NOT NULL
);
-- 索引:idx_ki_scope(owner, codebase) / idx_ki_active(codebase, invalid_at, superseded_by) / idx_ki_kind(kind)
```

外加 **FTS5 全文索引**(external-content,省空间单一数据源):

```sql
CREATE VIRTUAL TABLE ki_fts USING fts5(
    summary, detail, root_cause,
    content='knowledge_items', content_rowid='rowid',
    tokenize = 'unicode61'
);
-- 三触发器(AFTER INSERT/DELETE/UPDATE OF summary,detail,root_cause)保持 FTS 与主表一致。
-- access_count 自增不触发 FTS 重排(只在三个文本列变时触发)。
```

- `rowid` 是 SQLite 隐式整数主键,FTS5 拿它做映射。
- 向量是 `float32` BLOB(Python 端 cosine),**不是**专门的向量库 —— 记忆量级小,线性扫够用。

## 查看配方(`sqlite3` CLI 或 Python)

### 用 sqlite3 CLI

```bash
sqlite3 data/memory/memory.db
.headers on
.mode column

-- 总条数 + 按 kind 分布
SELECT kind, COUNT(*) FROM knowledge_items GROUP BY kind;

-- 某仓库的有效条目(未失效、未被取代)
SELECT id, kind, substr(summary,1,60) AS summary, confidence, valid_at
FROM knowledge_items
WHERE codebase='wpa' AND invalid_at IS NULL AND superseded_by IS NULL
ORDER BY created_at DESC LIMIT 20;

-- 一条完整记录(含 JSON 字段)
SELECT * FROM knowledge_items WHERE id='<ki_id>';

-- 退出
.quit
```

### 用 Python(可直接反序列化 JSON 字段、解 embedding)

```python
import sqlite3, json, struct
conn = sqlite3.connect("data/memory/memory.db")
conn.row_factory = sqlite3.Row

# 按 scope + kind 列出
for r in conn.execute(
    "SELECT id, kind, summary, confidence, valid_at, invalid_at, superseded_by "
    "FROM knowledge_items WHERE codebase=? ORDER BY created_at DESC", ("wpa",)):
    print(dict(r))

# 看 bug 教训的根因 / 修法
for r in conn.execute(
    "SELECT summary, symptom, root_cause, fix_patch, blast_radius_files "
    "FROM knowledge_items WHERE kind='bug_lesson' AND codebase=?"):
    print(r["summary"], "|", r["root_cause"][:80])
    print("  blast:", json.loads(r["blast_radius_files"]))

# 解 embedding(float32 BLOB → 维度)
row = conn.execute("SELECT embedding FROM knowledge_items WHERE id=?",
                   ("<ki_id>",)).fetchone()
if row and row["embedding"]:
    dim = len(row["embedding"]) // 4
    vec = struct.unpack(f"{dim}f", row["embedding"])
    print("vector dim:", dim)

# FTS5 关键词检索(BM25,score 越大越相关 —— 代码取了 -bm25)
for r in conn.execute(
    "SELECT ki.id, -bm25(ki_fts) AS score, ki.summary "
    "FROM ki_fts JOIN knowledge_items ki ON ki.rowid = ki_fts.rowid "
    "WHERE ki_fts MATCH ? ORDER BY score DESC LIMIT 5", ("scan disconnect",)):
    print(f"{r['score']:.3f}  {r['summary']}")
```

> [!NOTE]
> BM25 分数在 Hyperion 代码里取的是 **`-bm25(ki_fts)`**(bm25 原生越小越相关,取负归一成越大越相关)。手查时记得带负号,否则排序方向反。

## 手动运维操作

### 失效一条(软删,保留历史)

只追加记忆(对标 mem0 v3)→ 不物理删,标 `invalid_at`:

```sql
UPDATE knowledge_items
SET invalid_at = datetime('now'), updated_at = datetime('now')
WHERE id = '<ki_id>';
```

或走封装 / CLI(推荐,会维护一致性与召回过滤):

```bash
uv run hyperion memory invalidate <ki_id>
```

### 彻底清空某仓库的记忆(谨慎)

```bash
sqlite3 data/memory/memory.db "DELETE FROM knowledge_items WHERE codebase='wpa';"
# FTS 由触发器自动跟删(external-content)
```

### 备份

```bash
cp data/memory/memory.db data/memory/memory.db.bak
```

## 常见坑

| 坑 | 正确做法 |
|---|---|
| BM25 排序方向反 | 代码取 `-bm25()`(越大越相关);手查记得带负号 |
| FTS 查特殊字符报 `OperationalError` | 代码 `_fts_query()` 已转义;手查把 term 用双引号包成字面量:`"scan"` |
| 改了行但召回没变 | recall 有**衰减 × 置信**加权 + 过滤(`invalid_at`/`superseded_by`);手改后确认这两列与 `confidence` |
| JSON 字段读出来是字符串 | `json.loads()` 反序列化(`evidence` / `blast_radius_files` / `tags` / `related`) |
| 想看"当前版本" | `WHERE invalid_at IS NULL AND superseded_by IS NULL`(只追加:旧版本仍存,检索时最新为主、旧作参考) |

## 参考

- 实现:[../../../src/hyperion/services/memory/backends/native/store.py](../../../src/hyperion/services/memory/backends/native/store.py)(DDL + `MemoryStore`)

## See Also

- [../services/memory.md](../services/memory.md) — 记忆模块全貌(schema / 召回 / ingest / bi-temporal)
- [inspect-code-index.md](inspect-code-index.md) — 查看 LanceDB 代码索引(对比)
- [../cli-reference.md](../cli-reference.md) §`hyperion memory`
