"""native 后端 · SQLite 知识项库(R1 backends/native/store.py)。

这是什么
--------
记忆核心的"物理存储"。把 KnowledgeItem(一条知识)存进 SQLite,并提供两路检索:
  - BM25 全文检索(FTS5):按"关键词"找 summary/detail/root_cause。
  - 向量检索(cosine):按"意思"找 —— 需要 KnowledgeItem 带 embedding(memorize 时算)。
两路是 recall.py 多路融合里的"memory 路";recall 还会混 code_index(代码路)+ crg(结构路)。

为什么用 SQLite(不另开 LanceDB)
  - KI ≠ 代码 chunk:需要关系操作(按 scope/kind 过滤、冲突软删 superseded_by IS NULL、
    access_count 累加),SQLite 最合适;LanceDB 留给 code_index 的代码 chunk,不造第三套检索栈。
  - 同类参考实现全是 SQLite:deer-flow deermem、mnemopi beam、code-review-graph graph.db。
  - 向量:存 float32 blob,cosine 在 Python 里算(v1 知识项量级小,几百条 O(N) 无感;
    千万级再上 ANN,记 backlog)。

bi-temporal(借 graphiti):矛盾/失效时设 invalid_at + superseded_by(软删),永不物理删除 ——
能回答"这个 bug 在 X 时点还存不存在"(系统考古关键)。检索默认只看 active 的。

并发:WAL + busy_timeout(借 deermem/crg);写用进程内 Lock 串行,读可并发。

dumb CRUD:本文件只存/取/查;智能(合并/冲突/巩固)在 memorize.py / consolidate.py。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hyperion.services.memory.schema import Evidence, KnowledgeItem, Scope, SourceTier

_SCHEMA_VERSION = 1

# 知识项的所有列(单表查询用 _KI_COLS;多表 JOIN 用 _cols("ki") 加别名前缀)。
_KI_FIELD_LIST = [
    "id", "kind", "repo", "owner", "codebase", "summary", "detail", "symptom", "root_cause", "fix_patch",
    "blast_radius_files", "kind_detail", "commit_sha", "evidence", "source", "source_tier", "confidence",
    "access_count", "last_recalled", "valid_at", "invalid_at", "created_at", "related", "tags",
    "superseded_by", "embedding", "updated_at",
]
_KI_COLS = ", ".join(_KI_FIELD_LIST)


def _cols(alias: str) -> str:
    """带表别名前缀的列清单(JOIN 时避免歧义),如 _cols('ki') → 'ki.id, ki.kind, ...'。"""
    return ", ".join(f"{alias}.{c}" for c in _KI_FIELD_LIST)


# ──────────────────────────────────────────────────────────────────────────
# §1 建库 DDL(表 + 索引 + FTS5 external-content + 同步触发器)
# ──────────────────────────────────────────────────────────────────────────

_SCHEMA = """
-- 知识项主表。rowid 是 SQLite 隐式整数主键(FTS5 用它做 content_rowid 映射)。
CREATE TABLE IF NOT EXISTS knowledge_items (
    id                 TEXT PRIMARY KEY,   -- 稳定 id(sha256(scope+kind+content_key)[:16])
    kind               TEXT NOT NULL,      -- codebase_fact | bug_lesson | mental_model
    repo               TEXT NOT NULL,
    owner              TEXT NOT NULL,      -- 租户(scope 一半)
    codebase           TEXT NOT NULL,      -- 租户(scope 一半)
    summary            TEXT NOT NULL,      -- 人读摘要(检索+注入核心)
    detail             TEXT NOT NULL DEFAULT '',
    symptom            TEXT NOT NULL DEFAULT '',   -- bug_lesson
    root_cause         TEXT NOT NULL DEFAULT '',   -- bug_lesson
    fix_patch          TEXT NOT NULL DEFAULT '',   -- bug_lesson
    blast_radius_files TEXT NOT NULL DEFAULT '[]',  -- JSON
    kind_detail        TEXT NOT NULL DEFAULT 'module',  -- codebase_fact
    commit_sha         TEXT,
    evidence           TEXT NOT NULL DEFAULT '[]',  -- JSON [Evidence]
    source             TEXT NOT NULL DEFAULT '',
    source_tier        TEXT NOT NULL DEFAULT 'unknown',  -- SourceTier.value
    confidence         REAL NOT NULL DEFAULT 0,
    access_count       INTEGER NOT NULL DEFAULT 0,  -- 被召回次数(升级 mental_model 依据)
    last_recalled      TEXT,
    valid_at           TEXT NOT NULL,      -- bi-temporal:在真起点
    invalid_at         TEXT,               -- bi-temporal:失效点(NULL=仍有效)
    created_at         TEXT NOT NULL,
    related            TEXT NOT NULL DEFAULT '[]',  -- JSON [ki_id]
    tags               TEXT NOT NULL DEFAULT '[]',  -- JSON [str]
    superseded_by      TEXT,               -- 被哪条取代(NULL=当前版本)
    embedding          BLOB,               -- float32 向量(NULL=没算)
    updated_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ki_scope  ON knowledge_items(owner, codebase);
CREATE INDEX IF NOT EXISTS idx_ki_active ON knowledge_items(codebase, invalid_at, superseded_by);
CREATE INDEX IF NOT EXISTS idx_ki_kind   ON knowledge_items(kind);

-- FTS5 全文索引(external-content + 触发器同步,借 deermem retrieval / CRG migrations v5)。
-- content='knowledge_items' → FTS 不另存全文,按 rowid 回主表取(省空间,单一数据源)。
CREATE VIRTUAL TABLE IF NOT EXISTS ki_fts USING fts5(
    summary, detail, root_cause,
    content='knowledge_items', content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'   -- ⚠️ 中文分词弱(无空格);向量路补语义,jieba 记 backlog
);
-- 三触发器保持 FTS 与主表一致(显式传 old.*/new.* 值,避 external-content"主表已更新"的坑)。
-- AFTER UPDATE 仅在三个文本列变更时触发 → access_count 自增不会重排 FTS。
CREATE TRIGGER IF NOT EXISTS ki_fts_ai AFTER INSERT ON knowledge_items BEGIN
    INSERT INTO ki_fts(rowid, summary, detail, root_cause)
    VALUES (new.rowid, new.summary, new.detail, new.root_cause);
END;
CREATE TRIGGER IF NOT EXISTS ki_fts_ad AFTER DELETE ON knowledge_items BEGIN
    INSERT INTO ki_fts(ki_fts, rowid, summary, detail, root_cause)
    VALUES ('delete', old.rowid, old.summary, old.detail, old.root_cause);
END;
CREATE TRIGGER IF NOT EXISTS ki_fts_au AFTER UPDATE OF summary, detail, root_cause ON knowledge_items BEGIN
    INSERT INTO ki_fts(ki_fts, rowid, summary, detail, root_cause)
    VALUES ('delete', old.rowid, old.summary, old.detail, old.root_cause);
    INSERT INTO ki_fts(rowid, summary, detail, root_cause)
    VALUES (new.rowid, new.summary, new.detail, new.root_cause);
END;

CREATE TABLE IF NOT EXISTS ki_meta(key TEXT PRIMARY KEY, value TEXT);
"""


# ──────────────────────────────────────────────────────────────────────────
# §2 行 ↔ KnowledgeItem 序列化
# ──────────────────────────────────────────────────────────────────────────


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _vec_to_blob(vec: list[float] | None) -> bytes | None:
    """向量 → float32 bytes(numpy)。None/空 → None(没算向量的 KI)。"""
    if not vec:
        return None
    import numpy as np

    return np.asarray(vec, dtype=np.float32).tobytes()


def _blob_to_vec(blob: Any) -> list[float] | None:
    """float32 bytes → list[float]。None → None。"""
    if blob is None:
        return None
    import numpy as np

    return np.frombuffer(bytes(blob), dtype=np.float32).tolist()


def _ki_to_row(ki: KnowledgeItem) -> dict[str, Any]:
    """KnowledgeItem → 可绑定到 SQL 的 dict(list/dict 字段 JSON 编码,时间 ISO 编码)。"""
    return {
        "id": ki.id,
        "kind": ki.kind,
        "repo": ki.repo,
        "owner": ki.scope.owner,
        "codebase": ki.scope.codebase,
        "summary": ki.summary,
        "detail": ki.detail,
        "symptom": ki.symptom,
        "root_cause": ki.root_cause,
        "fix_patch": ki.fix_patch,
        "blast_radius_files": json.dumps(ki.blast_radius_files, ensure_ascii=False),
        "kind_detail": ki.kind_detail,
        "commit_sha": ki.commit_sha,
        "evidence": json.dumps([e.model_dump() for e in ki.evidence], ensure_ascii=False),
        "source": ki.source,
        "source_tier": ki.source_tier.value,
        "confidence": ki.confidence,
        "access_count": ki.access_count,
        "last_recalled": ki.last_recalled.isoformat() if ki.last_recalled else None,
        "valid_at": ki.valid_at.isoformat(),
        "invalid_at": ki.invalid_at.isoformat() if ki.invalid_at else None,
        "created_at": ki.created_at.isoformat(),
        "related": json.dumps(ki.related, ensure_ascii=False),
        "tags": json.dumps(ki.tags, ensure_ascii=False),
        "superseded_by": ki.superseded_by,
        "embedding": _vec_to_blob(ki.embedding),
        "updated_at": _utcnow_iso(),
    }


def _row_to_ki(row: sqlite3.Row | dict[str, Any]) -> KnowledgeItem:
    """SQL 行 → KnowledgeItem。row 用 [key] 取值(sqlite3.Row 与 dict 都支持)。"""
    g = row.__getitem__
    last = g("last_recalled")
    return KnowledgeItem(
        id=g("id"),
        kind=g("kind"),
        repo=g("repo"),
        scope=Scope(owner=g("owner"), codebase=g("codebase")),
        summary=g("summary"),
        detail=g("detail") or "",
        symptom=g("symptom") or "",
        root_cause=g("root_cause") or "",
        fix_patch=g("fix_patch") or "",
        blast_radius_files=json.loads(g("blast_radius_files") or "[]"),
        kind_detail=g("kind_detail") or "module",
        commit_sha=g("commit_sha"),
        evidence=[Evidence(**e) for e in json.loads(g("evidence") or "[]")],
        source=g("source") or "",
        source_tier=SourceTier(g("source_tier") or "unknown"),
        confidence=float(g("confidence") or 0.0),
        access_count=int(g("access_count") or 0),
        last_recalled=datetime.fromisoformat(last) if last else None,
        valid_at=datetime.fromisoformat(g("valid_at")),
        invalid_at=datetime.fromisoformat(g("invalid_at")) if g("invalid_at") else None,
        created_at=datetime.fromisoformat(g("created_at")),
        related=json.loads(g("related") or "[]"),
        tags=json.loads(g("tags") or "[]"),
        superseded_by=g("superseded_by"),
        embedding=_blob_to_vec(g("embedding")),
    )


def _fts_query(query: str) -> str:
    """查询 → FTS5「OR-of-terms」(每个 term 引号转义,避 *, : 被当语法)。

    为什么用 OR 不用短语/AND:BM25 在多路召回里当"撒大网"角色(宽召回,rerank/向量再精筛)。
    OR 不会因为某个 term(尤其 CJK)不匹配而整体返空 —— 漏一个 term 也能把命中的召回来。

    ⚠️ CJK 弱点:unicode61 不切中文(整段汉字当 1 个 token),"扫描" ≠ "阻塞所有站点扫描"。
    纯中文查询靠【向量路】补语义(Qwen3 embedding 中文强);英文/混合查询 OR 正常召回。
    jieba 分词 / CJK 逐字切分 记 backlog(提升纯中文的 BM25 召回,减少对向量 API 的依赖)。
    """
    terms = [t for t in (query or "").split() if t]
    if not terms:
        return ""

    def _q(t: str) -> str:
        return '"' + t.replace('"', '""') + '"'  # 引号转义:字面量 term,不当 FTS 语法

    return " OR ".join(_q(t) for t in terms)


def _scope_filter(scope: Scope, repo: str | None = None, *, alias: str | None = None) -> tuple[list[str], list[Any]]:
    """生成 scope 过滤子句(owner+codebase[+repo]),alias 给多表 JOIN 加前缀。"""
    p = f"{alias}." if alias else ""
    clauses = [f"{p}owner=?", f"{p}codebase=?"]
    params: list[Any] = [scope.owner, scope.codebase]
    if repo:
        clauses.append(f"{p}repo=?")
        params.append(repo)
    return clauses, params


# ──────────────────────────────────────────────────────────────────────────
# §3 MemoryStore:存储 + 两路检索
# ──────────────────────────────────────────────────────────────────────────


class MemoryStore:
    """SQLite 知识项库(canonical 数据 + FTS5 + 向量 blob)。

    线程:单连接 check_same_thread=False + 写锁;LangGraph 多线程读安全、写串行。
    """

    _UPSERT = f"""
    INSERT INTO knowledge_items ({_KI_COLS})
    VALUES (@id,@kind,@repo,@owner,@codebase,@summary,@detail,@symptom,@root_cause,@fix_patch,
            @blast_radius_files,@kind_detail,@commit_sha,@evidence,@source,@source_tier,@confidence,
            @access_count,@last_recalled,@valid_at,@invalid_at,@created_at,@related,@tags,
            @superseded_by,@embedding,@updated_at)
    ON CONFLICT(id) DO UPDATE SET
        kind=excluded.kind, repo=excluded.repo, summary=excluded.summary, detail=excluded.detail,
        symptom=excluded.symptom, root_cause=excluded.root_cause, fix_patch=excluded.fix_patch,
        blast_radius_files=excluded.blast_radius_files, kind_detail=excluded.kind_detail,
        commit_sha=excluded.commit_sha, evidence=excluded.evidence, source=excluded.source,
        source_tier=excluded.source_tier, confidence=excluded.confidence,
        access_count=excluded.access_count, last_recalled=excluded.last_recalled,
        valid_at=excluded.valid_at, invalid_at=excluded.invalid_at,
        related=excluded.related, tags=excluded.tags, superseded_by=excluded.superseded_by,
        embedding=excluded.embedding, updated_at=excluded.updated_at
    """  # created_at/owner/codebase 不在 SET —— upsert 保持原创建时间与租户身份

    def __init__(self, store_path: str | Path, *, db_name: str = "memory.db"):
        self._path = Path(store_path)
        self._path.mkdir(parents=True, exist_ok=True)
        self._db_file = self._path / db_name
        self._wl = threading.Lock()  # 写串行(WAL 下读可并发)
        # isolation_level=None → 自动提交模式,事务由我们显式 BEGIN/COMMIT 控制(借 crg)。
        self._conn = sqlite3.connect(str(self._db_file), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._conn.execute("INSERT OR IGNORE INTO ki_meta(key,value) VALUES ('schema_version', ?)", (str(_SCHEMA_VERSION),))

    # —— 写 ——

    def upsert(self, items: list[KnowledgeItem]) -> int:
        """批量 upsert(按 id;存在则更新除 created_at/owner/codebase 外字段)。返回条数。

        ON CONFLICT 原地更新 → rowid 稳定 → FTS rowid 映射不乱。整批一个 BEGIN IMMEDIATE 事务。
        """
        if not items:
            return 0
        rows = [_ki_to_row(it) for it in items]
        with self._wl:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.executemany(self._UPSERT, rows)
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        return len(rows)

    def bump_access(self, item_id: str) -> None:
        """被召回命中:access_count+1, last_recalled=now(升级 mental_model 的依据)。

        只动这两列 → 不触发 FTS 重排(AFTER UPDATE OF summary,detail,root_cause)。
        """
        with self._wl:
            self._conn.execute(
                "UPDATE knowledge_items SET access_count=access_count+1, last_recalled=?, updated_at=? WHERE id=?",
                (_utcnow_iso(), _utcnow_iso(), item_id),
            )

    def set_invalid(self, item_id: str, *, superseded_by: str | None = None, invalid_at: datetime | None = None) -> bool:
        """bi-temporal 软删:设 invalid_at(+可选 superseded_by)。返回是否真的改了。"""
        ts = (invalid_at or datetime.now(UTC)).isoformat()
        with self._wl:
            cur = self._conn.execute(
                "UPDATE knowledge_items SET invalid_at=?, superseded_by=COALESCE(?, superseded_by), updated_at=? "
                "WHERE id=? AND invalid_at IS NULL",
                (ts, superseded_by, _utcnow_iso(), item_id),
            )
        return cur.rowcount > 0

    def set_kind(self, item_id: str, kind: str) -> bool:
        """改 kind(consolidate 升级 mental_model 用)。"""
        with self._wl:
            cur = self._conn.execute("UPDATE knowledge_items SET kind=?, updated_at=? WHERE id=?", (kind, _utcnow_iso(), item_id))
        return cur.rowcount > 0

    def set_confidence(self, item_id: str, confidence: float) -> None:
        with self._wl:
            self._conn.execute("UPDATE knowledge_items SET confidence=?, updated_at=? WHERE id=?", (confidence, _utcnow_iso(), item_id))

    # —— 读 ——

    def get(self, item_id: str) -> KnowledgeItem | None:
        """按 id 取一条(含已失效的)。"""
        row = self._conn.execute(f"SELECT {_KI_COLS} FROM knowledge_items WHERE id=?", (item_id,)).fetchone()
        return _row_to_ki(row) if row else None

    def list_items(self, scope: Scope, *, repo: str | None = None, kind: str | None = None, include_invalid: bool = False) -> list[KnowledgeItem]:
        """列某 scope 的知识项(可按 repo/kind 过滤;默认只看 active)。"""
        clauses, params = _scope_filter(scope, repo)
        if kind:
            clauses.append("kind=?")
            params.append(kind)
        if not include_invalid:
            clauses.append("invalid_at IS NULL AND superseded_by IS NULL")
        sql = f"SELECT {_KI_COLS} FROM knowledge_items WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC"
        return [_row_to_ki(r) for r in self._conn.execute(sql, params).fetchall()]

    def count(self, scope: Scope, *, include_invalid: bool = False) -> int:
        clauses, params = _scope_filter(scope)
        if not include_invalid:
            clauses.append("invalid_at IS NULL AND superseded_by IS NULL")
        return int(self._conn.execute(f"SELECT COUNT(*) FROM knowledge_items WHERE {' AND '.join(clauses)}", params).fetchone()[0])

    def search_bm25(self, query: str, scope: Scope, *, repo: str | None = None, limit: int = 20) -> list[tuple[KnowledgeItem, float]]:
        """BM25 全文检索(FTS5):返回 [(item, score)],score 越大越相关(bm25 取负归一)。

        无词/查询异常 → 返 [](不抛;recall 走其他路)。
        """
        fq = _fts_query(query)
        if not fq:
            return []
        clauses, params = _scope_filter(scope, repo, alias="ki")
        sql = (
            f"SELECT {_cols('ki')}, -bm25(ki_fts) AS score "
            "FROM ki_fts JOIN knowledge_items ki ON ki.rowid = ki_fts.rowid "
            f"WHERE ki_fts MATCH ? AND {' AND '.join(clauses)} "
            "AND ki.invalid_at IS NULL AND ki.superseded_by IS NULL "
            "ORDER BY score DESC LIMIT ?"
        )
        try:
            rows = self._conn.execute(sql, [fq, *params, limit]).fetchall()
        except sqlite3.OperationalError:
            return []  # FTS 查询语法异常(极端输入)→ 不崩 recall
        return [(_row_to_ki(r), float(r["score"])) for r in rows]

    def search_vector(self, query_vec: Any, scope: Scope, *, repo: str | None = None, limit: int = 20) -> list[tuple[KnowledgeItem, float]]:
        """向量检索(cosine):返回 [(item, cosine)],越大越相关。

        v1:scope 内所有带向量的 active 项 load 出来,Python 里算 cosine(O(N),几百条无感;
        千万级上 ANN,记 backlog)。维度不匹配的项跳过(防配置改后脏数据崩)。
        """
        import numpy as np

        if query_vec is None:
            return []
        clauses, params = _scope_filter(scope, repo)
        clauses += ["embedding IS NOT NULL", "invalid_at IS NULL", "superseded_by IS NULL"]
        rows = self._conn.execute(f"SELECT {_KI_COLS} FROM knowledge_items WHERE {' AND '.join(clauses)}", params).fetchall()
        if not rows:
            return []
        q = np.asarray(query_vec, dtype=np.float32)
        qn = float(np.linalg.norm(q)) + 1e-12
        scored: list[tuple[KnowledgeItem, float]] = []
        for r in rows:
            v = np.frombuffer(bytes(r["embedding"]), dtype=np.float32)
            if v.shape[0] != q.shape[0]:
                continue  # 维度不匹配 → 跳过
            sim = float(np.dot(q, v) / (qn * (float(np.linalg.norm(v)) + 1e-12)))
            scored.append((_row_to_ki(r), sim))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    def close(self) -> None:
        self._conn.close()
