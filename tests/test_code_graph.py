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


@needs_crg
def test_analyze_changes_and_community_ids(tmp_path):
    """analyze_changes(改动文件+行范围 → risk/changed_functions)+ community_ids_for(符号→社区)。P-A 1b 用。

    给 changed_ranges(从 PR diff hunk 算的形态),不靠 git diff —— 跟 1b 实际用法一致。
    """
    (tmp_path / "a.py").write_text(
        "def alpha():\n    return beta()\n"
        "def beta():\n    return 1\n"
        "def gamma():\n    return alpha()\n"
    )
    cg = CodeGraph.build(tmp_path, "fixture_ac", base_dir=str(tmp_path))

    # analyze_changes:改动 a.py 的 1-6 行(覆盖 alpha/beta/gamma)→ CRG 映射到这些函数节点。
    ac = cg.analyze_changes(["a.py"], changed_ranges={"a.py": [(1, 6)]})
    assert isinstance(ac, dict)
    # CRG analyze_changes 的返回键(risk_score/changed_functions/affected_flows/review_priorities)。
    assert "risk_score" in ac and "changed_functions" in ac
    assert isinstance(ac["risk_score"], float)
    assert isinstance(ac["changed_functions"], list)

    # community_ids_for:CRG 的 qualified_name 是「绝对路径::符号」格式(如 .../a.py::alpha)。
    # 1b 实际用法:qn 来自 analyze_changes 的 changed_functions(格式一致),再查社区按 module 分桶。
    qns = [f["qualified_name"] for f in ac.get("changed_functions", []) if f.get("qualified_name")]
    assert qns, "analyze_changes 应映射到 a.py 的函数(alpha/beta/gamma)"
    cmap = cg.community_ids_for(qns)
    assert isinstance(cmap, dict)
    assert all(qn in cmap for qn in qns)  # 查询的 qn 都是 key


@needs_crg
def test_call_chain_small_repo(tmp_path):
    """call_chain:符号中心的 N 跳调用链(仅 CALLS 边)+ PageRank 重要度(P1.5 caller/callee 进适配层)。

    同款小仓 fixture(alpha→beta/gamma,gamma→delta,Foo.method→alpha,b.caller→alpha/beta):
    解析符号 → 建 CALLS 子图 → PageRank → 双向有界 BFS → enrich 节点 → 组装截断。
    callers/callees 可能为空(CRG 对 Python 的 CALLS 边提取视解析器而定),故断言结构 + 字段齐全,
    不强断言非空(空则覆盖「种子无 CALLS 边」分支,也是合法返回)。
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
    cg = CodeGraph.build(tmp_path, "fixture_cc", base_dir=str(tmp_path))

    res = cg.call_chain("alpha", direction="both", depth=2, top_n=10)
    # 顶层结构齐全:键都在,callers/callees 是 list(可能空但不能缺),resolved 非空(符号解析到了)
    assert res["symbol"] == "alpha"
    assert res["resolved"], "alpha 应解析到图节点"
    assert res["direction"] == "both" and res["depth"] == 2
    assert isinstance(res["callers"], list) and isinstance(res["callees"], list)
    assert isinstance(res["truncated"], bool) and "note" in res
    # 每个节点字段齐全(qualified_name/file/line/kind/hop/pagerank);hop ∈ [1, depth]
    for side in ("callers", "callees"):
        for nd in res[side]:
            assert nd["qualified_name"]
            assert all(k in nd for k in ("file", "line", "kind", "hop", "pagerank"))
            assert 1 <= nd["hop"] <= 2
            assert isinstance(nd["pagerank"], float)
    # 不存在的符号 → ValueError(工具层据此转友好串)
    with pytest.raises(ValueError):
        cg.call_chain("no_such_function_zzz")


@needs_crg
def test_call_chain_bad_direction(tmp_path):
    """非法 direction → ValueError(call_chain 的输入校验;工具层兜底的来源)。"""
    (tmp_path / "a.py").write_text("def alpha():\n    return 1\n")
    cg = CodeGraph.build(tmp_path, "fixture_dir", base_dir=str(tmp_path))
    with pytest.raises(ValueError, match="direction"):
        cg.call_chain("alpha", direction="sideways")
