"""code_graph.py 测试(R3.2)。

两类:
- 单元:CRG 缺 extra 时 _require_crg 抛清晰错;open() 对未建 db 抛 FileNotFoundError。
- 集成:小仓真跑 CRG full_build → 各查询返回结构正确(慢 ~1-2s,但验端到端)。

CRG 是可选 extra:没装时集成测自动 skip。
"""

from __future__ import annotations

import importlib.util

import pytest

from hyperion.services.code_index.code_graph import CodeGraph, _require_crg


def _crg_installed() -> bool:
    return importlib.util.find_spec("code_review_graph") is not None


needs_crg = pytest.mark.skipif(not _crg_installed(), reason="需要 code-review-graph extra")


# ── 单元 ──────────────────────────────────────────────────────────────────


def test_require_crg_missing_raises(monkeypatch):
    """CRG 没装时 _require_crg 抛 ImportError 且带安装指引。"""
    import hyperion.services.code_index.code_graph as cg

    real = cg.importlib.util.find_spec

    def fake(name, *args, **kwargs):
        return None if name == "code_review_graph" else real(name, *args, **kwargs)

    monkeypatch.setattr(cg.importlib.util, "find_spec", fake)
    with pytest.raises(ImportError, match="code-review-graph"):
        _require_crg()


@needs_crg
def test_open_missing_db_raises(tmp_path):
    """open() 对未建的 db 抛 FileNotFoundError(不默默建空图)。"""
    with pytest.raises(FileNotFoundError):
        CodeGraph.open("definitely_not_built_repo_xyz", base_dir=str(tmp_path))


# ── 集成(真跑 CRG full_build)────────────────────────────────────────────


@needs_crg
def test_build_and_query_small_repo(tmp_path):
    """小仓建图 + 各查询返回结构正确。

    造一个有调用/包含关系的小 .py 仓(跨文件 import + 函数互调 + 一个类),足够产出节点/边/社区。
    """
    (tmp_path / "a.py").write_text(
        "def alpha():\n    return beta() + gamma()\n"
        "def beta():\n    return 1\n"
        "def gamma():\n    return delta()\n"
        "def delta():\n    return 0\n"
        "class Foo:\n    def method(self):\n        return alpha()\n"
    )
    (tmp_path / "b.py").write_text(
        "from a import alpha, beta\n"
        "def caller():\n    return alpha() + beta()\n"
    )

    cg = CodeGraph.build(tmp_path, "fixture", base_dir=str(tmp_path))

    # stats 有节点/边
    s = cg.stats()
    assert s["total_nodes"] > 0
    assert s["total_edges"] > 0

    # communities 是 list(小仓可能就几个,不强断言数量)
    assert isinstance(cg.communities(), list)

    # architecture_overview 三个键齐全
    ov = cg.architecture_overview()
    assert {"communities", "cross_community_edges", "warnings"} <= set(ov.keys())

    # hub/bridge 返回 list 且不超过 top_n
    hubs = cg.hub_nodes(top_n=5)
    assert isinstance(hubs, list) and len(hubs) <= 5
    if hubs:
        assert "total_degree" in hubs[0] and "qualified_name" in hubs[0]
    bridges = cg.bridge_nodes(top_n=5)
    assert isinstance(bridges, list) and len(bridges) <= 5

    # open() 能复用刚建的图(不重建)
    cg2 = CodeGraph.open("fixture", base_dir=str(tmp_path))
    assert cg2.stats()["total_nodes"] == s["total_nodes"]
