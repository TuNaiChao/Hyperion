"""F5 · index --seed 播种端到端:小版本索引从同线基线拷贝起步,只重嵌差异文件。

剧本(对应真实场景:v20 线基线索引已建,5.50.61 小版本出 bug):
  ① 基线 v1(3 个 C 文件)建索引 bluez-v20 → full,embed 计数 = 全量 chunk 数;
  ② 小版本 v2 = v1 拷贝 + 改 1 个文件 + 新增 1 个文件;
     `index v2 bluez-v20-5.50.61 --seed bluez-v20 --no-graph`:
     - 向量索引拷贝自基线 → mode=incremental;
     - embed 计数 = 恰好等于「改动+新增」文件的 chunk 数(用 chunk_repo 独立对账);
     - 结构图目录也被播种(增量刷新的底);
  ③ 幂等:目标索引已存在时 --seed 跳过拷贝,不报错。
隔离:chdir tmp(CLI 的 data/code_index 相对落点)+ 假 embedder(计数器)。
"""

from __future__ import annotations

import hashlib
import shutil

import numpy as np
import pytest

import rootrecall.services.code_index.embed as embed_mod
from rootrecall.cli import main as cli_main
from rootrecall.services.code_index.chunker import chunk_repo

_calls = {"n": 0}


class _CountingEmbedder:
    """假 embedder:确定性向量 + 全局计数(e2e 断言增量只重嵌差异)。"""

    @property
    def fingerprint(self) -> str:
        return "fake-embedder-v1"

    def embed_chunks(self, chunks):
        _calls["n"] += len(chunks)
        return np.stack([self._vec(c.id) for c in chunks])

    def embed_query(self, query: str) -> np.ndarray:
        return self._vec(query)

    @staticmethod
    def _vec(key: str) -> np.ndarray:
        h = hashlib.sha256(key.encode()).digest()
        v = np.frombuffer(h[:8], dtype=np.uint8).astype(np.float32)
        return v / (np.linalg.norm(v) + 1e-9)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(embed_mod, "create_embedder", lambda cfg: _CountingEmbedder())
    _calls["n"] = 0

    v1 = tmp_path / "bluez-v20"
    v1.mkdir()
    (v1 / "a.c").write_text("int util_a(void) { return 1; }\n", encoding="utf-8")
    (v1 / "b.c").write_text("int util_b(void) { return 2; }\n", encoding="utf-8")
    (v1 / "d.c").write_text("int util_d(void) { return 4; }\n", encoding="utf-8")
    return {"v1": v1, "tmp": tmp_path}


def test_f5_index_seed_e2e(env):
    v1, tmp = env["v1"], env["tmp"]

    # ── ① 基线索引:full ──────────────────────────────────────────────────────
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()) as b1:
        assert cli_main(["index", str(v1), "bluez-v20", "--no-graph"]) == 0
    assert "全量" in b1.getvalue() or "full" in b1.getvalue()
    full_embeds = _calls["n"]
    assert full_embeds > 0
    total_chunks_v1 = len(chunk_repo(v1))
    assert full_embeds == total_chunks_v1

    # ── ② 小版本:v2 = v1 + 改 a.c + 增 c.c → 播种增量 ───────────────────────
    v2 = tmp / "bluez-v20-5.50.61"
    shutil.copytree(v1, v2)
    (v2 / "a.c").write_text("int util_a(void) { return 100; }\n", encoding="utf-8")  # 改
    (v2 / "c.c").write_text("int util_c(void) { return 3; }\n", encoding="utf-8")   # 增

    with contextlib.redirect_stdout(io.StringIO()) as b2:
        assert cli_main(["index", str(v2), "bluez-v20-5.50.61",
                         "--seed", "bluez-v20", "--no-graph"]) == 0
    out2 = b2.getvalue()
    assert "已播种向量索引" in out2 and "增量" in out2
    assert f"{full_embeds} chunk" not in out2  # 不是全量路径

    # embed 对账:恰好 = 「改动+新增」文件在 v2 里的 chunk 数(a.c + c.c),不多不少。
    expected = len([c for c in chunk_repo(v2) if c.file in ("a.c", "c.c")])
    assert _calls["n"] - full_embeds == expected, (
        f"播种增量应只重嵌差异文件:期望 {expected},实际 {_calls['n'] - full_embeds}")
    assert expected < total_chunks_v1, "测试前提:v2 差异应显著小于全量"

    # 播种产出的索引真实可用:检索到新文件的符号(c.c 的 util_c 进了库)。
    from rootrecall.services.code_index.store import LanceDBStore
    store = LanceDBStore("data/code_index")
    files = set(store._open_or_create("bluez-v20-5.50.61").to_arrow()["file"].to_pylist())
    assert {"a.c", "b.c", "c.c", "d.c"} == files  # 新文件在、旧文件也在、没丢

    # ── ③ 幂等:目标已存在 → 播种跳过,索引照常增量对账 ──────────────────────
    with contextlib.redirect_stdout(io.StringIO()) as b3:
        assert cli_main(["index", str(v2), "bluez-v20-5.50.61",
                         "--seed", "bluez-v20", "--no-graph"]) == 0
    assert "播种跳过" in b3.getvalue()
    assert _calls["n"] - full_embeds == expected  # 无变化 → 不再 embed
