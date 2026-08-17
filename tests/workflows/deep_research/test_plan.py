"""deep_research · _plan 核心 + node_plan 接线测试(R3.3.1)。

测什么(面向小白)
  plan_modules 是「一次 LLM batch → 每模块人话名 + STORM focus」的核心。它有三条出口:
    ① LLM 吐合法 JSON   → 用 LLM 的名字 + focus
    ② LLM 吐坏东西/抛错  → 降级:原社区名 + 通用 focus(等同 R3.2 行为,不阻断调研)
    ③ LLM 漏掉某模块     → 那条降级,其余照用 LLM(不全盘丢)
  这三条 + 空候选 + node_plan 接线,各一条测,把行为锁死。

为什么不接真 LLM / 真 CRG
  plan_modules 对 model 的全部依赖 = 「.invoke(消息) → 返回值有 .content」;对 CRG 无依赖
  (CRG 在 node_plan 里建候选,_plan 只吃 dict)。所以用桩 model + 打桩 CodeGraph,
  几毫秒确定性跑完,不联网、不解析全仓。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from rootrecall.workflows.deep_research._plan import plan_modules


# ── 桩 model:plan_modules 只用 .invoke() + .content,不需要真 langchain 模型 ──
class _StubModel:
    """返回固定文本的假 chat model(测「LLM 吐合法/坏 JSON」两条出口)。"""

    def __init__(self, content: str):
        self._content = content

    def invoke(self, messages):  # noqa: ANN001 - 桩,签名无所谓
        return SimpleNamespace(content=self._content)


class _BoomModel:
    """invoke 永远抛异常,测「LLM 调用失败 → 整体降级」。"""

    def invoke(self, messages):  # noqa: ANN001
        raise RuntimeError("LLM 挂了(测试故意)")


# ── 1. LLM 吐合法 JSON:每模块拿到人话名 + STORM focus,id 对齐,member/symbol 透传 ──
def test_plan_modules_llm_success():
    candidates = [
        {
            "id": 3,
            "raw_name": "community-3",
            "member_files": ["p2p.c", "p2p_build.c"],
            "key_symbols": ["p2p_start", "p2p_connect"],
        },
        {
            "id": 7,
            "raw_name": "community-7",
            "member_files": ["driver_nl80211.c"],
            "key_symbols": ["nl80211_init"],
        },
    ]
    # 注意:id 写成字符串 "3"(候选里是 int 3)—— 顺带验 id 类型容忍(int/str 能对齐)
    json_resp = json.dumps(
        [
            {"id": "3", "name": "P2P 设备发现与协商", "focus": "p0: 职责/公开接口\n安全: 输入校验"},
            {"id": "7", "name": "驱动适配层(nl80211)", "focus": "p0: 职责\n维护: 与内核边界"},
        ]
    )

    plans = plan_modules(candidates, "wpa", _StubModel(json_resp))

    assert len(plans) == 2
    # 名字是 LLM 给的人话名(覆盖了原机械名 community-3)
    assert plans[0]["name"] == "P2P 设备发现与协商"
    assert plans[1]["name"] == "驱动适配层(nl80211)"
    # focus 带上了 STORM 的 p0 基础视角
    assert "p0" in plans[0]["focus"]
    # member_files / key_symbols 从候选透传过来(不丢)
    assert plans[0]["member_files"] == ["p2p.c", "p2p_build.c"]
    assert plans[0]["key_symbols"] == ["p2p_start", "p2p_connect"]
    assert plans[1]["key_symbols"] == ["nl80211_init"]


# ── 2. LLM 没吐 JSON(纯闲话)→ 全降级:原社区名 + 通用 focus ──────────────────
def test_plan_modules_no_json_fallback():
    candidates = [{"id": 1, "raw_name": "community-1", "member_files": ["a.c"], "key_symbols": []}]
    # 模型讲了句废话,没有 [ ... ] 结构
    plans = plan_modules(candidates, "wpa", _StubModel("抱歉,我无法处理这个请求。"))

    assert len(plans) == 1
    assert plans[0]["name"] == "community-1"  # 降级用原社区名
    assert "梳理该模块的职责" in plans[0]["focus"]  # R3.2 通用 focus
    assert plans[0]["member_files"] == ["a.c"]  # 透传不丢


# ── 3. LLM 调用抛异常 → 整体降级(不阻断调研)──────────────────────────────
def test_plan_modules_invoke_raises_fallback():
    candidates = [{"id": 1, "raw_name": "community-1", "member_files": [], "key_symbols": []}]
    plans = plan_modules(candidates, "wpa", _BoomModel())

    assert len(plans) == 1
    assert plans[0]["name"] == "community-1"  # 降级
    assert "梳理该模块的职责" in plans[0]["focus"]


# ── 4. LLM 漏掉一个模块 → 那条降级,其余照用 LLM(不全盘丢)──────────────────
def test_plan_modules_partial_coverage():
    candidates = [
        {"id": 1, "raw_name": "community-1", "member_files": ["a.c"], "key_symbols": ["a"]},
        {"id": 2, "raw_name": "community-2", "member_files": ["b.c"], "key_symbols": ["b"]},
    ]
    # LLM 只回了 id=1,id=2 漏了
    json_resp = json.dumps([{"id": 1, "name": "模块A", "focus": "p0: 职责"}])

    plans = plan_modules(candidates, "wpa", _StubModel(json_resp))

    assert len(plans) == 2
    assert plans[0]["name"] == "模块A"  # LLM 命名
    assert plans[1]["name"] == "community-2"  # 漏掉的降级原社区名
    assert "梳理该模块的职责" in plans[1]["focus"]  # 通用 focus
    assert plans[1]["member_files"] == ["b.c"]  # 透传不丢


# ── 5. 空候选 → 空计划(不调 LLM)───────────────────────────────────────────
def test_plan_modules_empty_candidates():
    assert plan_modules([], "wpa", _StubModel("[]")) == []


# ── 6. node_plan 接线:社区 → 候选 → plan_modules(CodeGraph/create_chat_model 打桩)──
def test_node_plan_wiring(monkeypatch):
    """node_plan 把 CRG 社区 + hub 分桶成候选,再交给 plan_modules。打桩 CodeGraph.open +
    create_chat_model,不依赖真 CRG / 真 LLM,验:大社区优先 + hub 按 community 分桶 + LLM 命名落地。"""
    from rootrecall.workflows.deep_research.nodes import node_plan

    # 假 CodeGraph.open → 返一个只有 hub_nodes() 的假对象(按 community_id 分桶的输入)
    class _FakeCG:
        def hub_nodes(self, top_n=15):  # noqa: ANN001
            return [
                {"community_id": 3, "qualified_name": "p2p_start"},
                {"community_id": 3, "qualified_name": "p2p_connect"},
                {"community_id": 7, "qualified_name": "nl80211_init"},
            ]

    monkeypatch.setattr(
        "rootrecall.services.code_index.code_graph.CodeGraph.open",
        lambda codebase: _FakeCG(),
    )
    # 假 create_chat_model → 返吐合法 JSON 的桩(node_plan 只用它产 model 传给 plan_modules)
    monkeypatch.setattr(
        "rootrecall.platform.models.create_chat_model",
        lambda name=None: _StubModel(
            json.dumps(
                [
                    {"id": 3, "name": "P2P 模块", "focus": "p0: 职责"},
                    {"id": 7, "name": "驱动模块", "focus": "p0: 职责"},
                ]
            )
        ),
    )

    state = {
        "codebase": "wpa",
        "architecture_overview": {
            "communities": [
                {"id": 3, "name": "community-3", "members": ["p2p.c"] * 5},  # 大社区(5 成员)
                {"id": 7, "name": "community-7", "members": ["driver_nl80211.c"] * 3},
            ]
        },
    }

    plan = node_plan(state)["plan"]
    assert len(plan) == 2
    # 大社区优先:community-3(5 成员)排在前
    assert plan[0]["name"] == "P2P 模块"
    assert plan[1]["name"] == "驱动模块"
    # hub 符号按 community 分桶,透传到对应模块
    assert "p2p_start" in plan[0]["key_symbols"]
    assert "p2p_connect" in plan[0]["key_symbols"]
    assert "nl80211_init" in plan[1]["key_symbols"]
    # member_files 也透传
    assert plan[0]["member_files"] == ["p2p.c"] * 5
