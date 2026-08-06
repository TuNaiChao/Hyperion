"""R3 收尾 ②[b] 确定性 recall 预注入 · 离线逻辑测试。

不真跑记忆 recall:mock get_memory_service(假 svc.search 返脚本 RecallHit)。验:
  - node_recall_lessons:happy(渲染段)/ 空 trigger 跳过 / 无 scope 跳过 / search 异常优雅降级;
  - _build_localize_prompt:先验段正确 prepend(prior 非空)+ 无先验不插(prior 空)。
"""
from __future__ import annotations

import asyncio

from hyperion.services.memory.schema import RecallHit, Scope
from hyperion.workflows.bug_rca import nodes


# ── 假 MemoryService:search 返脚本 hits ──
class _FakeSvc:
    def __init__(self, hits=None, raise_on_search=False):
        self._hits = hits or []
        self._raise = raise_on_search

    async def search(self, query, scope, *, top_k=5):
        if self._raise:
            raise RuntimeError("recall 炸了(模拟)")
        return self._hits


def _patch_svc(monkeypatch, svc):
    monkeypatch.setattr(nodes, "get_memory_service", lambda: svc)


def _hit(summary, *, score=0.8):
    return RecallHit(summary=summary, score=score, item_id=summary)


# ═════════════════════════ node_recall_lessons ═════════════════════════

def test_recall_lessons_happy(monkeypatch):
    """有 scope + trigger + 命中 → 渲染段含各 hit summary,recalled_lessons 带回。"""
    _patch_svc(monkeypatch, _FakeSvc([_hit("lesson-A 扫描挂起"), _hit("lesson-B radio work 泄漏")]))
    out = asyncio.run(nodes.node_recall_lessons(
        {"scope": Scope(codebase="wpa"), "trigger": "p2p 扫描挂起"},
    ))
    assert "lesson-A" in out["recalled_lessons_ctx"]
    assert "lesson-B" in out["recalled_lessons_ctx"]
    assert len(out["recalled_lessons"]) == 2


def test_recall_lessons_empty_trigger(monkeypatch):
    """trigger 空 → 跳过(返空段),不调 svc。"""
    svc = _FakeSvc([_hit("x")])
    _patch_svc(monkeypatch, svc)
    out = asyncio.run(nodes.node_recall_lessons({"scope": Scope(codebase="wpa"), "trigger": "   "}))
    assert out["recalled_lessons_ctx"] == ""
    assert out["recalled_lessons"] == []


def test_recall_lessons_no_scope(monkeypatch):
    """scope 缺(ingest 没跑)→ 跳过。"""
    _patch_svc(monkeypatch, _FakeSvc([_hit("x")]))
    out = asyncio.run(nodes.node_recall_lessons({"trigger": "现象"}))
    assert out["recalled_lessons_ctx"] == ""


def test_recall_lessons_search_exception_graceful(monkeypatch):
    """svc.search 抛异常 → 返空段(绝不阻断 bug-RCA 主流程;delegate 仍可调 MCP 工具)。"""
    _patch_svc(monkeypatch, _FakeSvc(raise_on_search=True))
    out = asyncio.run(nodes.node_recall_lessons(
        {"scope": Scope(codebase="wpa"), "trigger": "现象"},
    ))
    assert out["recalled_lessons_ctx"] == ""
    assert out["recalled_lessons"] == []


def test_recall_lessons_empty_hits(monkeypatch):
    """search 返空(记忆库无同类)→ 空段。"""
    _patch_svc(monkeypatch, _FakeSvc([]))
    out = asyncio.run(nodes.node_recall_lessons(
        {"scope": Scope(codebase="wpa"), "trigger": "现象"},
    ))
    assert out["recalled_lessons_ctx"] == ""


# ═════════════════════════ _build_localize_prompt 先验段 ═════════════════════════

def test_build_localize_prompt_prepends_prior():
    """prior_lessons 非空 → 顶部插"历史同类 bug 教训"先验段(在"你是 C/系统软件"之前)。"""
    p = nodes._build_localize_prompt("trigger", {"type": "object"}, prior_lessons="- lessonX 扫描挂起")
    assert "历史同类 bug 教训" in p
    assert "不是答案" in p                       # 安全关键 nudge:先验非答案
    assert "- lessonX 扫描挂起" in p
    assert p.index("历史同类 bug 教训") < p.index("你是 C/系统软件")  # 先验段在最前


def test_build_localize_prompt_no_prior():
    """prior_lessons 空 → 不插先验段,prompt 以"你是 C/系统软件"开头(行为不变)。"""
    p = nodes._build_localize_prompt("trigger", {"type": "object"}, prior_lessons="")
    assert "历史同类 bug 教训" not in p
    assert p.startswith("你是 C/系统软件")
