"""deep_research · node_index / node_plan 建图降级测试。

背景(#4,2026-08-14 p1-p2 backlog):node_index 裸调 CodeGraph.build,CRG extra
未装 / 建图异常会崩掉整个 workflow —— 与 CLI index 子命令的降级标准
(ImportError 非致命 + 提示)不一致。修法:try 降级(空 overview/stats 继续),
node_plan 的 CodeGraph.open 同理(图未建 → hub 空列表,key_symbols 留空)。

这两条把"CRG 缺席时 deep_research 仍能出报告(检索层照常)"锁死。
"""

from rootrecall.workflows.deep_research.nodes import node_index, node_plan


class _StubModel:
    """最小桩 model:invoke 返回固定文本(这里只喂空 JSON)。"""

    def __init__(self, text: str):
        self._text = text

    def invoke(self, messages):  # noqa: ANN001
        class _R:
            content = self._text

        return _R()


# ── 1. node_index:CodeGraph.build 抛错 → 降级空 overview,不崩 ──────────────
def test_node_index_crg_build_failure_degrades(monkeypatch, tmp_path):
    """CRG 建图炸了(未装/异常)→ node_index 返回空结构而非抛错。"""

    def _boom(*a, **k):  # 模拟 CodeGraph.build 抛错(ImportError / 建图异常都走这)
        raise ImportError("code-review-graph extra 未安装")

    monkeypatch.setattr(
        "rootrecall.services.code_index.code_graph.CodeGraph.build", _boom
    )
    # build_index 也打桩(不依赖真 embedder / 真索引)
    monkeypatch.setattr(
        "rootrecall.services.code_index.index.build_index", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "rootrecall.services.code_index.embed.create_embedder",
        lambda cfg: None,
    )

    class _Cfg:
        class code_index:  # noqa: N801 - 假配置,只嵌 embedding 属性
            class embedding:  # noqa: N801
                pass

    monkeypatch.setattr(
        "rootrecall.workflows.deep_research.nodes.get_app_config", lambda: _Cfg()
    )

    out = node_index(
        {"repo_root": tmp_path, "codebase": "no_crg_repo", "owner": "t"}
    )
    # 降级:不抛错,overview 是空结构(社区/耦合告警全空)
    assert out["index_built"] is True
    assert out["architecture_overview"]["communities"] == []
    assert out["codegraph_stats"] == {}


# ── 2. node_plan:图未建(open 抛 FileNotFoundError)→ hub 空列表继续 ─────────
def test_node_plan_no_graph_hub_empty(monkeypatch):
    """建图降级后 open 必抛 FileNotFoundError → hubs=[] → 候选 key_symbols 空继续。"""

    def _open_boom(codebase):
        raise FileNotFoundError("结构图未建")

    monkeypatch.setattr(
        "rootrecall.services.code_index.code_graph.CodeGraph.open", _open_boom
    )
    monkeypatch.setattr(
        "rootrecall.platform.models.create_chat_model",
        lambda name=None: _StubModel("[]"),
    )

    state = {
        "codebase": "no_crg_repo",
        # overview 空结构(建图降级的产物)→ communities=[] → 候选=[] → plan=[]
        "architecture_overview": {"communities": []},
    }
    assert node_plan(state)["plan"] == []
