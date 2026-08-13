"""R1 记忆核心 native 后端 · 离线逻辑测试(tests/services/memory/test_memory_native.py)。

不依赖任何外部 API:embedding 用手工向量、抽取(memorize_report)不测(需 LLM)。
覆盖:store 往返 / memorize 合并+冲突追加(R3.5+ 不再 supersede)/ recall 多路 RRF+衰减 / consolidate 升级。
"""

from __future__ import annotations

import tempfile
from typing import Literal

import numpy as np
import pytest

from hyperion.platform.config import NativeMemoryConfig
from hyperion.services.memory.backends.native.consolidate import consolidate
from hyperion.services.memory.backends.native.memorize import memorize_items
from hyperion.services.memory.backends.native.recall import _rrf_fuse, recall
from hyperion.services.memory.backends.native.service import NativeMemoryService
from hyperion.services.memory.backends.native.store import MemoryStore
from hyperion.services.memory.backends.native.structural import NoopStructuralBackend
from hyperion.services.memory.schema import Evidence, KnowledgeItem, RecallHit, Scope, SourceTier

VEC_DIM = 8  # 测试用向量维度(小,够区分相似度)


def _vec_available() -> bool:
    """探测 sqlite-vec 是否可加载(测试守卫:无扩展环境跳过 vec0 路径测试)。"""
    try:
        import sqlite3

        import sqlite_vec

        c = sqlite3.connect(":memory:")
        c.enable_load_extension(True)
        c.load_extension(sqlite_vec.loadable_path())
        c.close()
        return True
    except Exception:  # noqa: BLE001
        return False


vec_required = pytest.mark.skipif(not _vec_available(), reason="sqlite-vec 未装/不可加载")


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


# ── 建议 D:recall → bump → consolidate 全链 e2e + 自转 ────────────

def test_recall_bump_consolidate_e2e(store, scope):
    """e2e 锁住「真实 recall 链」:recall(默认 bump=True)→ access_count 涨 → consolidate promote。

    区别于 test_consolidate_promotes_mental_model(那个用 store.bump_access 手动模拟 bump,
    没走真实 recall→bump 链)。这里用真实 recall() 函数,锁整条链不被回归破坏。
    """
    a = _ki("sdp_extract_seqtype 整数溢出未校验 size", scope=scope)
    memorize_items([a], store=store)

    # recall 3 次(默认 bump=True → 每次命中 store.bump_access → access_count+1)
    for _ in range(3):
        hits = recall("sdp 整数溢出", scope, store=store, embedder=None, reranker=None, top_k=3)
        assert hits and hits[0].item_id == a.id

    # recall 真的把 access_count 从 0 bump 到 3 了(consolidate 升级的前置)
    assert store.get(a.id).access_count >= 3

    # consolidate → 该条升级 mental_model
    stats = consolidate(scope, store=store, promote_access_count=3)
    assert stats["promoted"] == 1
    assert store.get(a.id).kind == "mental_model"


def _native_service(store, *, auto_consolidate: bool = True) -> NativeMemoryService:
    """造一个 store-only 的 NativeMemoryService(embedder/reranker/code/structural/model 全空,只测 consolidate 自转)。"""
    cfg = NativeMemoryConfig(auto_consolidate=auto_consolidate)
    return NativeMemoryService(
        store=store, embedder=None, reranker=None, code_bundle=None,
        structural=NoopStructuralBackend(), model=None, native_cfg=cfg,
    )


def test_service_recall_auto_consolidates(store, scope):
    """建议 D 自转:recall 命中达标条目 → 后台 task 自动 consolidate → 条目升级 mental_model(无需手动敲 CLI)。

    关键:全程在同一个 asyncio.run 里跑 —— create_task 的后台 task 绑在事件循环上,
    循环结束就被丢弃。所以用单个 async 函数串起 3 次 recall + drain,让后台 task 有机会跑完。
    summary 带英文标识符(BM25 对英文 token 友好;纯中文短查询踩 §3.2 短板 2 的 FTS5 分词弱)。
    """
    import asyncio

    a = _ki("bt_connect 连接流程建立 ATT 链路", scope=scope)
    memorize_items([a], store=store)
    svc = _native_service(store, auto_consolidate=True)

    async def _run():
        for _ in range(3):
            await svc.recall("bt_connect ATT", scope, top_k=3)
        await asyncio.sleep(0.05)   # 让最后一次 recall create_task 的后台 consolidate 跑完

    asyncio.run(_run())

    assert store.get(a.id).kind == "mental_model"          # 自转升级了,没手动调 consolidate
    assert store.get(a.id).access_count >= 3


def test_service_recall_auto_consolidate_disabled(store, scope):
    """auto_consolidate=False → recall 不触发后台 consolidate(扩展口:想纯手动时关掉)。"""
    import asyncio

    a = _ki("gatt_discover 服务发现流程", scope=scope)
    memorize_items([a], store=store)
    svc = _native_service(store, auto_consolidate=False)

    async def _run():
        for _ in range(3):
            await svc.recall("gatt_discover", scope, top_k=3)
        await asyncio.sleep(0.05)

    asyncio.run(_run())

    # access_count 涨了(recall 仍 bump),但没自动 consolidate → kind 不变
    assert store.get(a.id).access_count >= 3
    assert store.get(a.id).kind == "bug_lesson"            # 没被 promote


# ── 建议 A:sqlite-vec 渐进式 ANN(双路径阈值切换)──────────────────────

def _vec_store(tmp_path=None, *, ann_threshold=500, auto_index=True) -> MemoryStore:
    """造带小阈值的 MemoryStore(测 vec0 路径时强制 count>threshold 走 KNN)。"""
    import tempfile as _tf

    return MemoryStore(_tf.mkdtemp(), auto_index=auto_index, ann_threshold=ann_threshold)


def _ki_with_vec(summary: str, vec: list[float], *, scope: Scope, repo="r") -> KnowledgeItem:
    """造一条带手工向量的 KI(测 vec0 双写/KNN;不依赖 embedder API)。"""
    return KnowledgeItem(
        kind="bug_lesson", repo=repo, scope=scope, summary=summary,
        evidence=[Evidence(file="f.c", line=1)], source_tier=SourceTier.delegate, embedding=vec,
    )


@vec_required
def test_vec0_lazy_create_and_double_write(scope):
    """首次 upsert 带 embedding 的 KI → vec0 表按探测维度延迟建表 + 主表/vec0 双写。

    验证:① 建表前 _vec_dim=None,upsert 后 _vec_dim=8;② ki_meta 记了 vec_dim;③ 主表 embedding 列仍在;④ vec0 行数=写入数。
    """
    import tempfile as _tf

    s = MemoryStore(_tf.mkdtemp())
    assert s._vec_ok and s._vec_dim is None            # 加载成功但还没建表
    items = [_ki_with_vec(f"test {i} radio", [float(i + 1)] * VEC_DIM, scope=scope) for i in range(3)]
    s.upsert(items)
    assert s._vec_dim == VEC_DIM                        # 探测维度建表
    assert s._conn.execute("SELECT value FROM ki_meta WHERE key='vec_dim'").fetchone()[0] == str(VEC_DIM)
    assert s._conn.execute("SELECT COUNT(*) FROM ki_vec").fetchone()[0] == 3       # vec0 双写
    assert s._conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE embedding IS NOT NULL").fetchone()[0] == 3  # 主表不破
    s.close()


@vec_required
def test_search_vector_threshold_routing(scope):
    """count ≤ threshold → 走 loop;count > threshold → 走 vec0(阈值切换核心)。

    用 ann_threshold=5:插 3 条(≤5)走 loop;插到 6 条(>5)走 vec0。spy 两个内部方法计数。
    """
    s = _vec_store(ann_threshold=5)
    items = [_ki_with_vec(f"ki{i}", np.random.default_rng(i).standard_normal(VEC_DIM).astype(np.float32).tolist(), scope=scope) for i in range(3)]
    s.upsert(items)
    assert not s._should_use_ann(scope)                # 3 ≤ 5 → loop
    # 再插 3 条(不同 summary = 不同 content_key = 新条目,不触发 ON CONFLICT)→ 6 > 5 → vec0
    s.upsert([_ki_with_vec(f"kj{i}", np.random.default_rng(i + 10).standard_normal(VEC_DIM).astype(np.float32).tolist(), scope=scope) for i in range(3)])
    assert s.count(scope) == 6
    assert s._should_use_ann(scope)                    # 6 > 5 → KNN
    s.close()


@vec_required
def test_vec0_knn_matches_loop_recall(scope):
    """vec0 KNN 与 loop 的 top-K 召回集 + sim 值一致(ANN 正确性核心)。

    sqlite-vec cosine metric + 1-distance 转换;小规模近精确,召回应一致、sim 误差<1e-4。
    """
    s = _vec_store(ann_threshold=3)
    rng = np.random.default_rng(42)
    items = [_ki_with_vec(f"ki{i}", rng.standard_normal(VEC_DIM).astype(np.float32).tolist(), scope=scope) for i in range(10)]
    s.upsert(items)
    # 用第一条的向量当 query(它自己应 top1,sim≈1.0)
    q = items[0].embedding
    hits_vec = s._search_vec0(q, scope, repo=None, limit=5)
    hits_loop = s._search_vec_loop(q, scope, repo=None, limit=5)
    assert hits_vec is not None
    vec_ids = [h[0].id for h in hits_vec]
    loop_ids = [h[0].id for h in hits_loop]
    assert set(vec_ids) == set(loop_ids)               # 召回集一致
    assert vec_ids[0] == loop_ids[0] == items[0].id    # top1 是自己
    vmap = {h[0].id: h[1] for h in hits_vec}
    lmap = {h[0].id: h[1] for h in hits_loop}
    assert max(abs(vmap[i] - lmap[i]) for i in vec_ids) < 1e-4   # sim 误差浮点级
    s.close()


@vec_required
def test_vec0_partition_isolation_and_active_filter(scope):
    """① partition_key 隔离:查 scope A 绝不返回 scope B;② active 过滤:invalidate 的不返回。"""
    s = _vec_store(ann_threshold=3)
    scope_a = Scope(owner="ownA", codebase="cbA")
    scope_b = Scope(owner="ownB", codebase="cbB")
    rng = np.random.default_rng(7)
    a_items = [_ki_with_vec(f"A{i}", rng.standard_normal(VEC_DIM).astype(np.float32).tolist(), scope=scope_a) for i in range(5)]
    b_items = [_ki_with_vec(f"B{i}", rng.standard_normal(VEC_DIM).astype(np.float32).tolist(), scope=scope_b) for i in range(5)]
    s.upsert(a_items + b_items)
    # ① 查 scope_a → 只返回 A 的(绝不串 B)
    hits_a = s._search_vec0(a_items[0].embedding, scope_a, repo=None, limit=5)
    assert hits_a is not None
    a_ids = {h[0].id for h in hits_a}
    b_ids_all = {it.id for it in b_items}
    assert a_ids.isdisjoint(b_ids_all), "partition_key 应隔离:scope A 的查询不该返回 B 的项"
    assert a_items[0].id in a_ids                      # 自己在结果里
    # ② invalidate A 的一条 → vec0 路不返回它(回主表 invalid_at 过滤)
    s.set_invalid(a_items[0].id)
    hits_a2 = s._search_vec0(a_items[1].embedding, scope_a, repo=None, limit=10)
    assert hits_a2 is not None
    assert all(h[0].id != a_items[0].id for h in hits_a2), "invalidate 的项应被 active 过滤掉"
    s.close()


def test_search_vector_degrades_when_auto_index_off(scope):
    """auto_index=False → 不加载 sqlite-vec,纯 loop 降级,不崩(无需 vec 扩展,任何环境可跑)。

    验证扩展不可用/关闭时的安全降级:记忆是核心,向量加速是优化,绝不崩。
    """
    import tempfile as _tf

    s = MemoryStore(_tf.mkdtemp(), auto_index=False)
    assert s._vec_ok is False                           # 没加载
    items = [_ki_with_vec(f"ki{i}", [float(i + 1)] * VEC_DIM, scope=scope) for i in range(3)]
    s.upsert(items)
    # vec0 表不存在(auto_index=False 不建表)
    assert s._conn.execute("SELECT name FROM sqlite_master WHERE name='ki_vec'").fetchone() is None
    # search_vector 正常走 loop,不崩
    hits = s.search_vector([1.0] * VEC_DIM, scope, limit=2)
    assert len(hits) == 2
    s.close()
