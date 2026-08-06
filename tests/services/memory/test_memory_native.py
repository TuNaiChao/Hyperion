"""R1 记忆核心 native 后端 · 离线逻辑测试(tests/services/memory/test_memory_native.py)。

不依赖任何外部 API:embedding 用手工向量、抽取(memorize_report)不测(需 LLM)。
覆盖:store 往返 / memorize 合并+冲突追加(R3.5+ 不再 supersede)/ recall 多路 RRF+衰减 / consolidate 升级。
"""

from __future__ import annotations

import tempfile
from typing import Literal

import numpy as np
import pytest

from hyperion.services.memory.backends.native.consolidate import consolidate
from hyperion.services.memory.backends.native.memorize import memorize_items
from hyperion.services.memory.backends.native.recall import _rrf_fuse, recall
from hyperion.services.memory.backends.native.store import MemoryStore
from hyperion.services.memory.schema import Evidence, KnowledgeItem, RecallHit, Scope, SourceTier


@pytest.fixture
def store():
    s = MemoryStore(tempfile.mkdtemp())
    yield s
    s.close()


@pytest.fixture
def scope():
    return Scope(codebase="wpa")


def _ki(summary, *, scope, kind: Literal["codebase_fact", "bug_lesson"] = "bug_lesson", symptom="扫描挂起", root_cause="rc",
        file="scan.c", line=10, embedding=None, tier=SourceTier.delegate):
    """造一条 KI(默认 bug_lesson + 一条 evidence + delegate 档)。"""
    return KnowledgeItem(
        kind=kind, repo="wpa", scope=scope, summary=summary, symptom=symptom,
        root_cause=root_cause, evidence=[Evidence(file=file, line=line)],
        source_tier=tier, embedding=embedding,
    )


# ── store 往返 ──────────────────────────────────────────────────

def test_store_upsert_get_roundtrip(store, scope):
    a = _ki("radio work 阻塞扫描", scope=scope, embedding=[0.1, 0.9, 0.0])
    assert store.upsert([a]) == 1
    got = store.get(a.id)
    assert got.summary == a.summary
    assert got.evidence[0].file == "scan.c"
    assert got.active
    assert np.allclose(got.embedding, [0.1, 0.9, 0.0], atol=1e-6)
    assert store.count(scope) == 1


def test_store_search_bm25_and_vector(store, scope):
    store.upsert([
        _ki("p2p-scan 孤儿 radio work 阻塞", scope=scope, embedding=[0.1, 0.9, 0.0]),
        _ki("radio work 串行化无线操作", scope=scope, embedding=[0.9, 0.1, 0.5], kind="codebase_fact"),
    ])
    assert len(store.search_bm25("radio work", scope)) >= 1          # BM25 命中
    vc = store.search_vector([0.11, 0.88, 0.02], scope)
    assert vc and vc[0][0].summary.startswith("p2p-scan")            # 向量 top-1 是它


def test_store_invalidate_soft_delete(store, scope):
    a = _ki("要被失效的", scope=scope)
    store.upsert([a])
    assert store.set_invalid(a.id) is True
    assert store.get(a.id).active is False
    assert store.count(scope) == 0                                   # active 计数排掉
    assert len(store.list_items(scope, include_invalid=True)) == 1   # 含失效仍可见


# ── memorize 合并 / 冲突取代 ────────────────────────────────────

def test_memorize_remention_merges_confidence(store, scope):
    a = _ki("同一根因 radio work 阻塞", scope=scope)
    memorize_items([a], store=store)
    memorize_items([a], store=store)            # 重提(同 content_key → 同 id)
    assert store.count(scope) == 1              # 不新增
    assert store.get(a.id).confidence > 0.5     # Bayes 累加(delegate 初始 0.5 → 0.65)


def test_memorize_conflict_appends_both(store, scope):
    """R3.5+(2026-08-06,对标 mem0 v3):同主题不同结论 → 不再 supersede,新旧都 active 并存(检索时最新为主)。"""
    old = _ki("结论A radio work 阻塞", scope=scope, symptom="扫描挂起", root_cause="旧结论未释放")
    memorize_items([old], store=store)
    new = _ki("结论B 真因死锁", scope=scope, symptom="扫描挂起", root_cause="新结论死锁")  # 同症状不同根因
    memorize_items([new], store=store)
    assert store.get(old.id).active is True                        # 旧不再被取代,保持 active
    assert store.get(old.id).superseded_by is None                 # 没盖戳作废
    assert store.get(new.id).active is True                        # 新也 active
    assert store.count(scope) == 2                                 # 两条并存(追加,非取代)


# ── recall 多路 RRF + 衰减 ──────────────────────────────────────

def test_rrf_fuse_prefers_multi_voice_hits():
    h = RecallHit(summary="x", score=0.0, item_id="abc")
    fused = _rrf_fuse([
        [h, RecallHit(summary="y", score=0.0, item_id="def")],
        [RecallHit(summary="z", score=0.0, item_id="abc")],   # abc 在两路都出现
    ])
    assert fused[0].item_id == "abc"           # 跨路一致 → 第一
    assert fused[0].score > fused[1].score


def test_recall_fuses_bm25_and_vector(store, scope):
    memorize_items([_ki("p2p-scan 孤儿 radio work 阻塞扫描", scope=scope, embedding=[0.1, 0.9, 0.0])], store=store)
    memorize_items([_ki("radio work 串行化无线操作", scope=scope, embedding=[0.9, 0.1, 0.5], kind="codebase_fact")], store=store)

    class FakeEmb:                             # 离线假 embedder:返回固定向量
        def embed_query(self, q):
            return np.asarray([0.11, 0.88, 0.02], dtype=np.float32)

    hits = recall("radio work", scope, store=store, embedder=FakeEmb(), reranker=None, top_k=2)
    assert hits
    assert all(h.source == "memory" for h in hits)


def test_recall_surfaces_conflict_both_and_hides_invalidated(store, scope):
    """R3.5+(2026-08-06):冲突两条都召回(旧版本作参考,created_at 可见);手动 invalidate(invalid_at)仍隐藏。"""
    a = _ki("根因A 扫描挂起", scope=scope, symptom="扫描挂起", root_cause="结论A", embedding=[0.1, 0.9, 0.0])
    b = _ki("根因B 扫描挂起", scope=scope, symptom="扫描挂起", root_cause="结论B", embedding=[0.1, 0.9, 0.0])
    memorize_items([a, b], store=store)                           # 同主题不同结论 → 都 active 并存(不再 supersede)
    assert store.count(scope) == 2

    class FakeEmb:
        def embed_query(self, q):
            return np.asarray([0.1, 0.88, 0.0], dtype=np.float32)

    hits = recall("扫描挂起", scope, store=store, embedder=FakeEmb(), reranker=None, top_k=5)
    assert len(hits) == 2                                         # 冲突两条都召回(superseded_by 不再过滤)
    assert all(h.created_at is not None for h in hits)            # created_at 可见(消费方判新旧)

    # 手动 invalidate 一条(invalid_at)→ recall 隐藏(错 fact 该藏);另一条仍在
    assert store.set_invalid(a.id) is True
    hits2 = recall("扫描挂起", scope, store=store, embedder=FakeEmb(), reranker=None, top_k=5)
    assert all(h.item_id != a.id for h in hits2)
    assert any(h.item_id == b.id for h in hits2)


# ── consolidate 升级 mental_model ───────────────────────────────

def test_consolidate_promotes_mental_model(store, scope):
    a = _ki("高频教训 radio work", scope=scope)
    memorize_items([a], store=store)
    for _ in range(3):
        store.bump_access(a.id)                # 被召回 3 次
    stats = consolidate(scope, store=store, promote_access_count=3)
    assert stats["promoted"] == 1
    assert store.get(a.id).kind == "mental_model"
