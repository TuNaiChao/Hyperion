"""OpencodeDelegate 重试 + fallback 逻辑测试(A,2026-08:治 glm API connection reset)。

不真跑 opencode:monkeypatch _run_once 按序吐结果,验 run() 的重试决策 ——
主模型瞬时错重试 → 仍错换 fallback → 续 session(--continue)。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import rootrecall.tools.delegate as dele
from rootrecall.tools.delegate import DelegateResult, DelegateStatus, OpencodeDelegate


def _err(msg: str) -> DelegateResult:
    """一个带 error 事件的瞬时错回执(模拟 glm connection reset)。"""
    return DelegateResult(
        final_text="", status=DelegateStatus.ERROR, error=msg,
        events=[{"type": "error", "error": {"data": {"message": msg}}}],
    )


def _ok(data: dict | None = None) -> DelegateResult:
    return DelegateResult(final_text="ok", status=DelegateStatus.OK, data=data or {"verdict": "verified"})


def _patch_cfg(monkeypatch, *, retry_max=2, fallback="uniontech-ai/deepseek-v4-flash", model="uniontech-ai/glm-5.2"):
    """mock get_app_config 给个够 run() 用的 opencode 配置。"""
    monkeypatch.setattr(dele, "get_app_config", lambda: SimpleNamespace(delegate=SimpleNamespace(opencode=SimpleNamespace(
        model=model, retry_max=retry_max, fallback_model=fallback,
        config=None, bin="opencode", format="json", timeout=10, agent=None, variant=None, auto_approve=True,
    ))))


def _script(monkeypatch, results):
    """让 OpencodeDelegate._run_once 按序吐 results,记录每次调用的(cont, model)。"""
    seq = list(results)
    calls: list[dict] = []

    async def fake(self, prompt, cwd, output_schema, cfg, timeout, agent, cont, *, model_override=None):
        calls.append({"cont": cont, "model": model_override or cfg.model})
        return seq.pop(0)

    monkeypatch.setattr(OpencodeDelegate, "_run_once", fake)
    return calls


# ── _is_transient_net_error 分类 ──────────────────────────────────────────

@pytest.mark.parametrize("msg,expect", [
    ("read tcp 1.2.3.4:443: read: connection reset by peer", True),
    ("timeout 1200s", True),
    ("fetch failed: 503 service unavailable", True),
    ("opencode 退出码 1;stderr: ...", False),   # 非网络错,不重试
])
def test_is_transient_classifier(msg, expect):
    r = DelegateResult(final_text="", status=DelegateStatus.ERROR, error=msg,
                       events=[{"type": "error", "error": {"data": {"message": msg}}}] if "reset" in msg or "timeout" in msg or "503" in msg else [])
    assert dele._is_transient_net_error(r) is expect


def test_is_transient_only_on_error_status():
    """OK/SCHEMA 不算瞬时错(不该重试)。"""
    assert dele._is_transient_net_error(_ok()) is False
    assert dele._is_transient_net_error(DelegateResult(final_text="", status=DelegateStatus.SCHEMA)) is False


# ── run() 重试循环 ───────────────────────────────────────────────────────

def test_retry_primary_then_fallback(monkeypatch):
    """主模型 2 次瞬时错 → 换 fallback 第 3 次成功;全程 continue_session=True 续 session。"""
    _patch_cfg(monkeypatch)
    calls = _script(monkeypatch, [_err("connection reset by peer"), _err("connection reset"), _ok()])
    out = asyncio.run(OpencodeDelegate().run("p", "/tmp", agent="rootrecall-repair", continue_session=True))
    assert out.status == "ok"
    assert out.data == {"verdict": "verified"}
    assert len(calls) == 3
    assert [c["model"] for c in calls] == ["uniontech-ai/glm-5.2", "uniontech-ai/glm-5.2", "uniontech-ai/deepseek-v4-flash"]
    assert all(c["cont"] is True for c in calls), "重试必须全程 --continue 续同 session"


def test_no_retry_on_non_transient(monkeypatch):
    """非瞬时错(SCHEMA / 真 ERROR 无网络关键字)不重试,直接返第一个。"""
    _patch_cfg(monkeypatch)
    calls = _script(monkeypatch, [DelegateResult(final_text="", status=DelegateStatus.ERROR, error="opencode 退出码 1"), _ok()])
    out = asyncio.run(OpencodeDelegate().run("p", "/tmp"))
    assert out.status == "error"
    assert len(calls) == 1, "非瞬时错不该重试"


def test_first_try_ok_no_retry(monkeypatch):
    """iter0 即成功 → 只调一次,不重试。"""
    _patch_cfg(monkeypatch)
    calls = _script(monkeypatch, [_ok()])
    out = asyncio.run(OpencodeDelegate().run("p", "/tmp"))
    assert out.status == "ok"
    assert len(calls) == 1


def test_localize_first_attempt_new_session_retry_continues(monkeypatch):
    """localize(continue_session=False)首次瞬时错 → 重试时改 continue_session=True(续刚建的 session)。"""
    _patch_cfg(monkeypatch)
    calls = _script(monkeypatch, [_err("connection reset"), _ok()])
    asyncio.run(OpencodeDelegate().run("p", "/tmp", continue_session=False))
    assert calls[0]["cont"] is False, "iter0 attempt0 应是新 session"
    assert calls[1]["cont"] is True, "重试应 --continue 续 session"


def test_all_fail_returns_last(monkeypatch):
    """主 + fallback 全瞬时错 → 返末次(仍是 error),不无限重试。"""
    _patch_cfg(monkeypatch, retry_max=2)
    calls = _script(monkeypatch, [_err("connection reset by peer")] * 4)
    out = asyncio.run(OpencodeDelegate().run("p", "/tmp"))
    assert out.status == "error"
    assert len(calls) == 4, "主 2 + fallback 2 = 4 次封顶"
