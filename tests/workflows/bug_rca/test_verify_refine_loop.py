"""R3.1 #54-rework B 迭代 verify-refine 双循环 · 离线逻辑测试。

不真跑 opencode / git:scripted delegate(按序吐 DelegateResult)+ mock _observe_patch /
validate_patch / get_app_config。验 loop 决策逻辑 ——
localize(confirmed / visit / max-loop / infra-error)+ repair(verified / visit / max-loop)。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from hyperion.tools.delegate import CodingAgentDelegate, DelegateResult, DelegateStatus
from hyperion.workflows.bug_rca import nodes
from hyperion.workflows.bug_rca.nodes import (
    LOCALIZE_SCHEMA,
    REPAIR_SCHEMA,
    node_delegate_localize_loop,
    node_delegate_repair_loop,
)


# ── scripted delegate:按序吐结果,记录每次调用(验 continue_session / agent)──
class _ScriptedDelegate:
    def __init__(self, results):
        self._results = list(results)
        self.calls: list[dict] = []

    async def run(self, prompt, cwd, output_schema=None, *, timeout=None, agent=None, continue_session=False):
        self.calls.append({"agent": agent, "continue": continue_session})
        if not self._results:
            raise AssertionError("scripted delegate 结果已耗尽(测试给的脚本不够)")
        return self._results.pop(0)


def _patch_delegate(monkeypatch, delegate):
    """让 nodes 里 CodingAgentDelegate.from_config() 返回我们的 scripted 实例。"""
    monkeypatch.setattr(CodingAgentDelegate, "from_config", classmethod(lambda cls: delegate), raising=False)


def _fake_cfg(max_localize=2, max_repair=2):
    """够 loop 用的假 config(只 delegate.max_*_loops)。

    用 SimpleNamespace 而非嵌套 class:Python 类作用域不参与闭包(class body 里引用外层
    函数参数会 NameError),SimpleNamespace 没这坑。
    """
    return SimpleNamespace(
        delegate=SimpleNamespace(
            max_localize_loops=max_localize,
            max_repair_loops=max_repair,
        )
    )


def _cfg(monkeypatch, **kw):
    monkeypatch.setattr("hyperion.platform.config.get_app_config", lambda: _fake_cfg(**kw))


def _state(**kw):
    base = {"repo_root": "/tmp/repo", "trigger": "P2P scan 空列表", "workspace": "/tmp/ws"}
    base.update(kw)
    return base


def _loc(verdict, root_cause="根因X"):
    return DelegateResult(
        final_text="{}", status=DelegateStatus.OK,
        data={"root_cause": root_cause, "trigger_chain": [], "evidence": [],
              "blast_radius_files": [], "verdict": verdict, "falsification": "反例"},
    )


def _rep(verdict, confidence=0.9):
    return DelegateResult(
        final_text="{}", status=DelegateStatus.OK,
        data={"confidence": confidence, "verdict": verdict, "falsification": "反例"},
    )


def _patch_observe_validate(monkeypatch, patch_seq, validate_seq):
    """_observe_patch / validate_patch 按序返回(mock 掉真 git,纯逻辑测试)。"""
    obs = list(patch_seq)
    val = list(validate_seq)
    monkeypatch.setattr(nodes, "_observe_patch", lambda code_dir: obs.pop(0) if obs else "")
    monkeypatch.setattr(
        "hyperion.services.workspace.validate.validate_patch",
        lambda patch, forward_dir=None, reverse_dir=None, timeout=60.0: val.pop(0),
    )


# ═════════════════════════ localize loop ═════════════════════════

def test_localize_confirmed_first_try(monkeypatch):
    """iter0 即 confirmed → 1 轮收敛,不重试。"""
    d = _ScriptedDelegate([_loc("confirmed")])
    _patch_delegate(monkeypatch, d)
    _cfg(monkeypatch)
    out = asyncio.run(node_delegate_localize_loop(_state(localize_prompt="定位它", localize_schema=LOCALIZE_SCHEMA)))
    assert out["localize_loops"] == 1
    assert out["verdict_chain"] == ["iter0:confirmed"]
    assert out["localization_json"]["root_cause"] == "根因X"
    assert len(d.calls) == 1
    assert d.calls[0]["continue"] is False  # iter0 新 session


def test_localize_revisit_then_confirmed(monkeypatch):
    """iter0 needs_revisit → iter1 confirmed(2 轮,第 2 次 --continue 续同 session)。"""
    d = _ScriptedDelegate([_loc("needs_revisit"), _loc("confirmed", "根因Y")])
    _patch_delegate(monkeypatch, d)
    _cfg(monkeypatch)
    out = asyncio.run(node_delegate_localize_loop(_state(localize_prompt="定位它", localize_schema=LOCALIZE_SCHEMA)))
    assert out["localize_loops"] == 2
    assert out["verdict_chain"] == ["iter0:needs_revisit", "iter1:confirmed"]
    assert out["localization_json"]["root_cause"] == "根因Y"
    assert d.calls[1]["continue"] is True  # 重定位续同 session
    assert "needs_revisit" in out["localize_revisit_prompt"]


def test_localize_max_loop_exhausted(monkeypatch):
    """K1=2 都 needs_revisit → 耗尽,取末次 localization,不越界重试。"""
    d = _ScriptedDelegate([_loc("needs_revisit"), _loc("needs_revisit", "根因末")])
    _patch_delegate(monkeypatch, d)
    _cfg(monkeypatch, max_localize=2)
    out = asyncio.run(node_delegate_localize_loop(_state(localize_prompt="定位它", localize_schema=LOCALIZE_SCHEMA)))
    assert out["localize_loops"] == 2
    assert out["localization_json"]["root_cause"] == "根因末"
    assert len(d.calls) == 2


def test_localize_infra_error_breaks(monkeypatch):
    """iter0 timeout(infra)→ 跳出,不 --continue 破损 session,localization=None。"""
    d = _ScriptedDelegate([DelegateResult(final_text="", status=DelegateStatus.TIMEOUT, error="超时")])
    _patch_delegate(monkeypatch, d)
    _cfg(monkeypatch, max_localize=3)
    out = asyncio.run(node_delegate_localize_loop(_state(localize_prompt="定位它", localize_schema=LOCALIZE_SCHEMA)))
    assert out["localize_loops"] == 1
    assert out["verdict_chain"] == ["iter0:infra-timeout"]
    assert out["localization_json"] is None
    assert len(d.calls) == 1  # infra 错误不重试


# ═════════════════════════ repair loop ═════════════════════════

def test_repair_verified_first_try(monkeypatch):
    """iter0 verdict=verified + gate 过 → 1 轮收敛。"""
    d = _ScriptedDelegate([_rep("verified")])
    _patch_delegate(monkeypatch, d)
    _cfg(monkeypatch)
    _patch_observe_validate(monkeypatch, ["PATCH"], [{"verified": True, "forward_method": "strict", "log": "ok"}])
    out = asyncio.run(node_delegate_repair_loop(_state(prompt="修它", output_schema=REPAIR_SCHEMA)))
    assert out["verified"] is True
    assert out["repair_loops"] == 1
    assert out["patch"] == "PATCH"


def test_repair_revisit_then_verified(monkeypatch):
    """iter0 needs_fix + gate 失败 → iter1 verified + gate 过(2 轮,第 2 次 --continue)。"""
    d = _ScriptedDelegate([_rep("needs_fix"), _rep("verified")])
    _patch_delegate(monkeypatch, d)
    _cfg(monkeypatch)
    _patch_observe_validate(
        monkeypatch,
        ["P1", "P2"],
        [{"verified": False, "forward_method": "failed", "log": "错位"},
         {"verified": True, "forward_method": "strict", "log": "ok"}],
    )
    out = asyncio.run(node_delegate_repair_loop(_state(prompt="修它", output_schema=REPAIR_SCHEMA)))
    assert out["verified"] is True
    assert out["repair_loops"] == 2
    assert d.calls[1]["continue"] is True


def test_repair_max_loop_exhausted(monkeypatch):
    """K2=2 都没过 → verified=False、patch=末次(rerank 已移除,无 fan-out)。"""
    d = _ScriptedDelegate([_rep("needs_fix"), _rep("needs_fix")])
    _patch_delegate(monkeypatch, d)
    _cfg(monkeypatch, max_repair=2)
    _patch_observe_validate(
        monkeypatch,
        ["P1", "P2"],
        [{"verified": False, "forward_method": "failed", "log": "x"},
         {"verified": False, "forward_method": "failed", "log": "y"}],
    )
    out = asyncio.run(node_delegate_repair_loop(_state(prompt="修它", output_schema=REPAIR_SCHEMA)))
    assert out["verified"] is False
    assert out["repair_loops"] == 2
    assert out["patch"] == "P2"
    assert len(d.calls) == 2  # 没 fan-out


def test_observe_empty_short_circuits(monkeypatch):
    """observe 全程返空 → 不调 validate_patch(空短路),patch="" verified=False。

    锁住 nodes.py 的 `if patch else {empty}` 分支(此前零覆盖)。validate 给个会 fail
    的 sentinel,确认空 patch 没被喂进 validate_patch(防重构误把空串送进去)。
    """
    d = _ScriptedDelegate([_rep("needs_fix"), _rep("needs_fix")])
    _patch_delegate(monkeypatch, d)
    _cfg(monkeypatch, max_repair=2)
    monkeypatch.setattr(nodes, "_observe_patch", lambda code_dir: "")  # 全程空
    monkeypatch.setattr(
        "hyperion.services.workspace.validate.validate_patch",
        lambda **k: pytest.fail("空 patch 不该喂进 validate_patch"),
    )
    out = asyncio.run(node_delegate_repair_loop(_state(prompt="修它", output_schema=REPAIR_SCHEMA)))
    assert out["verified"] is False
    assert out["patch"] == ""  # 全程没产出有效补丁,非假阴性
    assert out["validate_log"] == "patch 为空"


def test_observe_empty_falls_back_to_best(monkeypatch):
    """末轮 observe 空但前轮有非空补丁 → 回退到 best_patch 重验,救假阴性。

    场景(校正后真因):delegate 末轮把改动 net-zero 改回 base → 末轮 diff 空 →
    若直接覆盖会丢掉 iter0 的有效补丁(P1)。回退到 P1 重验,过则 verified=True。
    """
    d = _ScriptedDelegate([_rep("needs_fix"), _rep("needs_fix")])
    _patch_delegate(monkeypatch, d)
    _cfg(monkeypatch, max_repair=2)
    # iter0 observe="P1"(非空→记 best);iter1 observe=""(末轮 net-zero)。
    _patch_observe_validate(
        monkeypatch,
        ["P1", ""],
        # [0] = iter0 的 P1 validate(没过→继续);[1] = 循环后回退 P1 的重验(过→救回)。
        [{"verified": False, "forward_method": "failed", "log": "首轮没过"},
         {"verified": True, "forward_method": "strict", "log": "ok"}],
    )
    out = asyncio.run(node_delegate_repair_loop(_state(prompt="修它", output_schema=REPAIR_SCHEMA)))
    assert out["patch"] == "P1"  # 回退救回,没被末轮空覆盖
    assert out["verified"] is True  # 回退重验过
    assert "回退自第 0 轮" in out["validate_log"]  # 诚实标注来源
    assert "末轮观察到空补丁" in out["validate_log"]
