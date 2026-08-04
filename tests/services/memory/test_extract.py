"""R1 记忆核心 · extract_items 逐条容错测试(R3.4 e2e 发现的小脆弱点修复)。

extract_items 从报告抽 KI。旧实现整批 `_ExtractionResult.model_validate`,
LLM schema 不守(踩坑 #5:偶把 kind 的值 "bug_lesson" 塞进 kind_detail,只认
module/symbol/architecture)→ 整批丢、写 0。改成逐条校验:坏条跳过、好条留。

不依赖外部 API:桩 model.invoke 返固定 JSON,确定性验证逐条容错。
"""

from __future__ import annotations

from types import SimpleNamespace

from hyperion.services.memory.backends.native.extract import extract_items
from hyperion.services.memory.schema import Scope, SourceTier


def _stub_model(reply: str) -> SimpleNamespace:
    """桩模型:invoke 返 .content = reply。"""
    return SimpleNamespace(invoke=lambda msgs: SimpleNamespace(content=reply))


def _scope():
    return Scope(codebase="wpa")


# ── 逐条容错:好条留、坏条跳 ────────────────────────────────────────────────────


def test_extract_skips_bad_item_keeps_good():
    # 第 1 条合法;第 2 条把 kind 值 "bug_lesson" 错塞进 kind_detail(只认 module/symbol/architecture)。
    reply = '{"items": [' \
            '{"kind": "bug_lesson", "summary": "合法的好条:根因是未判 NULL", "root_cause": "rc"}, ' \
            '{"kind": "bug_lesson", "summary": "坏条", "kind_detail": "bug_lesson"}' \
            ']}'
    out = extract_items("一段足够长的报告文本喂给抽取。" * 3, repo="wpa", scope=_scope(), model=_stub_model(reply))

    assert len(out) == 1  # 只留好条,坏条跳过(旧实现会返 0)
    assert "未判 NULL" in out[0].summary
    assert out[0].kind == "bug_lesson"


def test_extract_all_bad_returns_empty():
    # 两条都 schema 不守 → 返空 [](不抛异常)。
    reply = '{"items": [' \
            '{"kind": "bug_lesson", "summary": "坏1", "kind_detail": "bug_lesson"}, ' \
            '{"kind": "no_such_kind", "summary": "坏2"}' \
            ']}'
    out = extract_items("一段足够长的报告文本喂给抽取。" * 3, repo="wpa", scope=_scope(), model=_stub_model(reply))
    assert out == []


def test_extract_keeps_good_among_many_bad():
    # 3 坏 + 1 好 → 只留 1 条好。
    reply = '{"items": [' \
            '{"summary": "坏1", "kind_detail": "bug_lesson"}, ' \
            '{"kind": "bug_lesson", "summary": "那一条好货:加锁修复竞态"}, ' \
            '{"kind": "nope", "summary": "坏3"}, ' \
            '{"summary": "坏4", "kind_detail": "whatever"}' \
            ']}'
    out = extract_items("一段足够长的报告文本喂给抽取。" * 3, repo="wpa", scope=_scope(), model=_stub_model(reply))
    assert len(out) == 1
    assert "加锁" in out[0].summary


# ── 既有的兜底行为(回归)──────────────────────────────────────────────────────


def test_extract_short_text_skips_llm():
    # <40 字 → 直接返 [],不调 LLM(省钱)。
    out = extract_items("短", repo="wpa", scope=_scope(), model=_stub_model('{"items":[]}'))
    assert out == []


def test_extract_no_json_returns_empty():
    # 模型回非 JSON → 抠不到 → []。
    out = extract_items("一段足够长的报告文本喂给抽取。" * 3, repo="wpa", scope=_scope(),
                        model=_stub_model("完全不是 JSON 的一段话"))
    assert out == []


def test_extract_empty_items_returns_empty():
    out = extract_items("一段足够长的报告文本喂给抽取。" * 3, repo="wpa", scope=_scope(),
                        model=_stub_model('{"items": []}'))
    assert out == []


def test_extract_source_tier_propagated():
    # 抽出的 KI 带上传入的 source_tier。
    reply = '{"items": [{"kind": "bug_lesson", "summary": "一条带 tier 的好条"}]}'
    out = extract_items("一段足够长的报告文本喂给抽取。" * 3, repo="wpa", scope=_scope(),
                        model=_stub_model(reply), source_tier=SourceTier.imported)
    assert len(out) == 1
    assert out[0].source_tier == SourceTier.imported
