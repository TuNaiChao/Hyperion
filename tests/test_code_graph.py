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


# ── repo_map(PageRank 排名全仓符号地图,#38)────────────────────────────────


def test_render_repomap_tree_format():
    """_render_repomap_tree:纯函数,按文件分组 + PageRank 降序 + 树连接符(不需 CRG,快,恒跑)。

    造两个文件各俩符号 + 假分:验「文件按楼内最高分降序」「楼内按分降序」「树连接符 + pr= 分数格式」。
    """
    from types import SimpleNamespace

    from hyperion.services.code_index.code_graph import _render_repomap_tree

    meta = {
        "a.py::alpha": SimpleNamespace(kind="function", line_start=1),
        "a.py::alpha2": SimpleNamespace(kind="function", line_start=10),
        "b.py::beta": SimpleNamespace(kind="function", line_start=5),
        "b.py::beta2": SimpleNamespace(kind="method", line_start=20),
    }
    # b.py 的 beta(0.3)是全仓最高 → b.py 段应排在 a.py 前
    scores = {"a.py::alpha": 0.10, "a.py::alpha2": 0.05, "b.py::beta": 0.30, "b.py::beta2": 0.20}
    files = {"a.py": ["a.py::alpha", "a.py::alpha2"], "b.py": ["b.py::beta", "b.py::beta2"]}
    out = _render_repomap_tree(files, meta, scores)
    lines = out.splitlines()

    def _idx_containing(sub: str) -> int:
        # 符号行带 ├── 前缀 + kind/L/pr 后缀,不是裸名 → 用包含匹配找行号
        for i, ln in enumerate(lines):
            if sub in ln:
                return i
        raise AssertionError(f"{sub!r} 不在输出: {lines}")

    # 文件段顺序:文件头是裸路径精确行;最高分符号所在的文件在前(b.py 0.30 > a.py 0.10)
    assert lines.index("b.py") < lines.index("a.py")
    # 路径前缀已剥:符号行只留 Class::symbol(beta/beta2),不再带 "b.py::"
    assert "b.py::" not in out and "a.py::" not in out
    # 楼内符号按分降序:beta(0.30) 在 beta2(0.20) 前(尾随空格防 beta 命中 beta2)
    assert _idx_containing("beta (") < _idx_containing("beta2 (")
    # 末符号 └──、其余 ├──;分数格式 pr=0.300 / pr=0.200
    assert "├── beta (function) L5 pr=0.300" in out
    assert "└── beta2 (method) L20 pr=0.200" in out


@needs_crg
def test_repo_map_small_repo(tmp_path):
    """repo_map:小仓整图 PageRank → 按文件分组树 + token 预算贪心裁剪(#38)。

    同 call_chain 测的小仓(alpha→beta/gamma,gamma→delta,Foo.method→alpha,b.caller→alpha/beta)。
    验结构齐全 + token 预算生效(小预算装的符号 ≤ 大预算)+ PageRank 分是 float。CALLS 边提取
    视 CRG 解析器而定,可能为空(空则覆盖「无 CALLS 边」分支,合法),故按 n_symbols 分支断言。
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
    cg = CodeGraph.build(tmp_path, "fixture_rm", base_dir=str(tmp_path))

    big = cg.repo_map(map_tokens=2048)
    # 顶层结构齐全
    assert big["repo"] == "fixture_rm"
    for k in ("map_text", "n_symbols", "n_files", "map_tokens_budget",
              "map_tokens_used", "truncated", "top_symbols", "note"):
        assert k in big, f"缺键 {k}"
    assert big["map_tokens_budget"] == 2048
    assert isinstance(big["truncated"], bool)

    if big["n_symbols"] > 0:  # 有 CALLS 边 → 验排名输出
        assert big["map_text"], "有符号就应有地图文本"
        assert big["n_files"] >= 1
        assert "pr=" in big["map_text"]  # 渲染了分数
        assert big["map_tokens_used"] <= big["map_tokens_budget"]  # 贪心不超预算
        assert len(big["top_symbols"]) <= 10
        for s in big["top_symbols"]:
            assert all(k in s for k in ("qualified_name", "file", "pagerank"))
            assert isinstance(s["pagerank"], float)
        # 小预算装的符号不多于大预算(预算生效);装不下全部 → truncated=True
        small = cg.repo_map(map_tokens=15)
        assert small["n_symbols"] <= big["n_symbols"]
        if small["n_symbols"] < big["n_symbols"]:
            assert small["truncated"] is True
    else:  # 无 CALLS 边(空地图分支)
        assert big["map_text"] == ""
        assert "CALLS" in big["note"]


@needs_crg
def test_repo_map_no_calls_empty(tmp_path):
    """无调用边的仓(单函数返常量)→ 期 CALLS 子图空 → 空地图 + note;不抛是硬要求。

    单函数 lonely 无调用 → CALLS 子图无边 → PageRank 返空 → 走空地图分支。若 CRG 意外造了边
    (n_symbols≠0),也接受 —— 只验「不抛 + 结构齐全」契约,不强绑死空分支。
    """
    (tmp_path / "solo.py").write_text("def lonely():\n    return 42\n")
    cg = CodeGraph.build(tmp_path, "fixture_empty", base_dir=str(tmp_path))
    res = cg.repo_map()  # 不抛即硬通过
    assert isinstance(res, dict) and "map_text" in res
    if res["n_symbols"] == 0:  # 走了空地图分支才验其契约
        assert res["map_text"] == ""
        assert "CALLS" in res["note"]


# ── cross_version_diff(模块级函数,feature 2b)──────────────────────────────


def test_cross_version_diff_small_git_repo(tmp_path):
    """cross_version_diff:同一 git 仓两 ref 间 —— base..head 提交 + concern diff(纯 git,不需 CRG);
    有 CRG/图时再验 touched_functions 富化。git 不在 PATH 则 skip。

    建 tmp git 仓:commit1 加 a.py(v1),commit2 改 a.py(v2)。cross_version_diff("HEAD~1","HEAD")。
    """
    import os
    import shutil
    import subprocess

    if not shutil.which("git"):
        pytest.skip("git 不在 PATH")
    env = {**os.environ,
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

    def g(args):
        subprocess.run(["git", *args], cwd=str(tmp_path), env=env, check=True,
                       capture_output=True, text=True)

    g(["init", "-q"])
    (tmp_path / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    g(["add", "-A"])
    g(["commit", "-q", "-m", "v1: add alpha"])
    (tmp_path / "a.py").write_text("def alpha():\n    return 2\n", encoding="utf-8")
    g(["add", "-A"])
    g(["commit", "-q", "-m", "v2: change alpha"])

    from hyperion.services.code_index.code_graph import cross_version_diff

    # 纯 git 核(无图):refs / commits / concern_diff / patch_equivalence
    res = cross_version_diff("HEAD~1", "HEAD", repo_path=str(tmp_path),
                             concern_files=["a.py"])
    assert res["refs"]["base_sha"] and res["refs"]["head_sha"]
    assert res["refs"]["base_sha"] != res["refs"]["head_sha"]
    assert len(res["commits"]) == 1, res["commits"]
    assert "v2" in res["commits"][0]["subject"]
    assert res["concern_diff"], "应有 a.py 的 diff"
    assert "return 2" in res["concern_diff"]  # 改动行(+    return 2)
    assert {"new_in_head", "equivalent_in_base"} <= set(res["patch_equivalence"])

    # 没给 concern → 跳全量 diff(防回巨大 diff)+ note 提示
    res_full = cross_version_diff("HEAD~1", "HEAD", repo_path=str(tmp_path))
    assert res_full["concern_diff"] == ""
    assert "跳过全量 diff" in res_full["note"]

    # 有 CRG + 图:touched_functions 富化(alpha 在 a.py 且被 base..head diff 触及)
    if not _crg_installed():
        return  # 没装 CRG,git 核部分已验完,富化跳过(不 fail)
    cg = CodeGraph.build(tmp_path, "fixture_cvd", base_dir=str(tmp_path))
    res2 = cross_version_diff("HEAD~1", "HEAD", repo_path=str(tmp_path),
                              concern_files=["a.py"], graph=cg)
    assert isinstance(res2["touched_functions"], list)
    # 路径格式/CRG 解析容错:非空才断言结构 + note 含映射说明
    if res2["touched_functions"]:
        assert "qualified_name" in res2["touched_functions"][0]
        assert "touched_functions 映射" in res2["note"]
