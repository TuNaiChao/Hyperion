"""store.delete_by_file + index 增量清幽灵行测试(LanceDB 本地,零网络)。

2026-08-18 真 bug 回归:tests/test_mcp_tools.py 4 个测试函数各自定义 class _FakeGraph,
旧 parser 限定名不带函数前缀 → 4 条 chunk 同 id 进一个 upsert 批,LanceDB 拒绝
("Ambiguous merge inserts are prohibited")。parser 修复后,这里再守两件事:
① 同名局部类文件全量建索引不再炸(撞 id 根治);
② 增量时符号改名换 id,旧行要被 delete_by_file 清掉,不留内容重复的幽灵行。

embedder 用假的(确定性哈希向量),不花钱不联网;LanceDB 写 tmp_path,测试完即弃。
"""

from __future__ import annotations

import hashlib

import numpy as np

from rootrecall.services.code_index.chunker import CodeChunk
from rootrecall.services.code_index.index import build_index
from rootrecall.services.code_index.store import LanceDBStore

_DIM = 8


class _FakeEmbedder:
    """假 embedder:按 chunk id 哈希出确定性向量。维度固定,满足 Embedder 协议。"""

    @property
    def fingerprint(self) -> str:
        return "fake-embedder-v1"

    def embed_chunks(self, chunks: list[CodeChunk]) -> np.ndarray:
        return np.stack([self._vec(c.id) for c in chunks])

    def embed_query(self, query: str) -> np.ndarray:
        return self._vec(query)

    @staticmethod
    def _vec(key: str) -> np.ndarray:
        h = hashlib.sha256(key.encode()).digest()
        v = np.frombuffer(h[:_DIM], dtype=np.uint8).astype(np.float32)
        return v / (np.linalg.norm(v) + 1e-9)


def _mk_chunk(cid: str, file: str, text: str = "int x(void) { return 0; }") -> CodeChunk:
    """造一条最小可入库的 chunk(id/file 之外的字段够 schema 即可)。"""
    return CodeChunk(
        id=cid,
        symbol=cid.split(":", 1)[-1],
        kind="function",
        file=file,
        language="c",
        start_line=1,
        end_line=1,
        part=1,
        total=1,
        text=text,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
        fts_text=f"{cid} {text}",
    )


# ── LanceDBStore.delete_by_file ──────────────────────────────────────────────


def test_delete_by_file_removes_rows(tmp_path):
    """删指定文件的行,别的文件不动;返回删前行数;空清单是 no-op。"""
    store = LanceDBStore(tmp_path)
    chunks = [_mk_chunk("a.c:foo", "a.c"), _mk_chunk("b.c:bar", "b.c")]
    vecs = _FakeEmbedder().embed_chunks(chunks)
    store.upsert("repo", chunks, vecs)
    store.optimize("repo")  # 折叠进索引再删,走的是真实路径(不靠未索引新行的捷径)
    assert store.count("repo") == 2

    before = store.delete_by_file("repo", ["a.c"])
    assert before == 2  # 返回删前行数
    assert store.count("repo") == 1
    remaining = store._open_or_create("repo").to_arrow()["file"].to_pylist()
    assert remaining == ["b.c"]  # 只剩没被删的文件

    assert store.delete_by_file("repo", []) == 0  # 空清单 no-op,不炸
    assert store.delete_by_file("repo", ["不存在.c"]) == 1  # 匹配不到也安全(返回当前行数)


def test_delete_by_file_handles_quote_in_filename(tmp_path):
    """文件名带单引号(SQL 谓词注入形状):转义后不炸、按字面匹配。"""
    store = LanceDBStore(tmp_path)
    evil = "x'; DROP TABLE chunks;--.c"
    chunks = [_mk_chunk(f"{evil}:f", evil), _mk_chunk("ok.c:g", "ok.c")]
    store.upsert("repo", chunks, _FakeEmbedder().embed_chunks(chunks))
    store.delete_by_file("repo", [evil])
    assert store.count("repo") == 1  # ok.c 活着,evil 被字面删除


# ── build_index:撞 id 根治 + 增量清幽灵行 ─────────────────────────────────────

# 事故形状的源文件:两个测试函数各自定义同名局部类(旧 parser 下 4 条 chunk 撞成 2 个 id)。
_SAME_NAME_LOCAL = '''\
def test_a():
    class _FakeGraph:
        def m(self):
            pass

def test_b():
    class _FakeGraph:
        pass
'''


def test_full_build_with_same_name_local_classes(tmp_path):
    """撞 id 根治:同名局部类文件全量建索引不再触发 "Ambiguous merge inserts",
    且两个 _FakeGraph 各自成行(限定名带函数前缀)。"""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "t.py").write_text(_SAME_NAME_LOCAL, encoding="utf-8")

    stats = build_index(tmp_path, "repo", _FakeEmbedder(), tmp_path / "index")

    assert stats["mode"] == "full"
    ids = set(
        LanceDBStore(tmp_path / "index")._open_or_create("repo").to_arrow()["id"].to_pylist()
    )
    assert "src/t.py:test_a._FakeGraph" in ids
    assert "src/t.py:test_b._FakeGraph" in ids
    assert "src/t.py:_FakeGraph" not in ids  # 旧的裸名(撞车源)不该再出现


def test_incremental_rename_leaves_no_ghost_rows(tmp_path):
    """增量 + 符号改名:foo 改名 bar 后 id 变,旧行必须被清 —— 只剩 bar,无幽灵 foo。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    f = repo / "m.py"
    f.write_text("def foo():\n    pass\n", encoding="utf-8")

    base = tmp_path / "index"
    build_index(repo, "repo", _FakeEmbedder(), base)  # 全量:foo 入库

    f.write_text("def bar():\n    pass\n", encoding="utf-8")  # 改名 → id 变
    stats = build_index(repo, "repo", _FakeEmbedder(), base)  # 增量

    assert stats["mode"] == "incremental"
    assert stats["changed_files"] == 1
    ids = set(
        LanceDBStore(base)._open_or_create("repo").to_arrow()["id"].to_pylist()
    )
    assert "m.py:bar" in ids
    assert "m.py:foo" not in ids  # 幽灵行已被 delete_by_file 清掉


def test_incremental_removed_file_leaves_no_rows(tmp_path):
    """增量 + 删文件:文件从仓库消失,它的 chunk 一行不留。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    keep = repo / "keep.py"
    keep.write_text("def keeper():\n    pass\n", encoding="utf-8")
    gone = repo / "gone.py"
    gone.write_text("def dead():\n    pass\n", encoding="utf-8")

    base = tmp_path / "index"
    build_index(repo, "repo", _FakeEmbedder(), base)
    assert LanceDBStore(base).count("repo") >= 2  # 前提:两个文件都进了库

    gone.unlink()  # 文件消失
    stats = build_index(repo, "repo", _FakeEmbedder(), base)

    assert stats["mode"] == "incremental"
    files = set(LanceDBStore(base)._open_or_create("repo").to_arrow()["file"].to_pylist())
    assert files == {"keep.py"}  # dead.py 的行整文件清干净


def test_incremental_unchanged_is_noop(tmp_path):
    """零变化 → 增量 no-op:一行不重嵌、行数不变(回归保护)。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "m.py").write_text("def stable():\n    pass\n", encoding="utf-8")

    base = tmp_path / "index"
    build_index(repo, "repo", _FakeEmbedder(), base)
    before = LanceDBStore(base).count("repo")

    stats = build_index(repo, "repo", _FakeEmbedder(), base)

    assert stats["mode"] == "incremental"
    assert stats["indexed"] == 0 and stats["changed_files"] == 0
    assert LanceDBStore(base).count("repo") == before
