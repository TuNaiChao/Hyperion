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


def test_memorize_corrects_marks_old(store, scope):
    """纠正链(2026-08-13):写新 KI 带 corrects=[旧id] → 旧条 corrected_by 回填 + 仍 active(不失效)。

    核心区分:纠正 ≠ 失效。旧条不设 invalid_at(仍可检索/体检可见),只标 corrected_by(检索降权)。
    对齐只追加原则:旧条物理不变(不 supersede),只加反向标记。
    """
    old = _ki("结论A radio work 阻塞(误诊)", scope=scope, symptom="扫描挂起", root_cause="旧结论abort失败")
    memorize_items([old], store=store)
    # 新条声明纠正 old
    new = _ki("结论B 真因覆盖竞态(纠正A)", scope=scope, symptom="扫描挂起", root_cause="新结论scan覆盖")
    new.corrects = [old.id]
    memorize_items([new], store=store)

    refreshed_old = store.get(old.id)
    assert refreshed_old.corrected_by == new.id    # 回填了反向标记
    assert refreshed_old.active is True             # 仍 active(纠正 ≠ 失效,不设 invalid_at)
    assert refreshed_old.superseded_by is None      # 不走 supersede(对齐只追加)
    assert refreshed_old.invalid_at is None         # 不失效
    # corrects 是 transit 字段,不入库(查/检索/体检读的是旧条的 corrected_by,不是 corrects)
    refreshed_new = store.get(new.id)
    assert refreshed_new.corrected_by is None       # 新条没被纠正


def test_memorize_corrects_accepts_id_prefix(store, scope):
    """纠正链前缀解析(e2e 暴露的真 bug):dump/recall 渲染 id 截断 8 位,agent 传 8 位前缀;
    DB 里却是 16 位完整 id → 旧精确 WHERE id=? 匹配失败,corrected_by 静默不回填。

    _resolve_id 把 8 位前缀解析回 16 位:mark_corrected/set_invalid 都接受前缀。
    前缀歧义(>1 条同名前缀)→ 拒绝不改(宁漏不错)。
    """
    old = _ki("误诊结论(待纠正)", scope=scope, root_cause="旧")
    memorize_items([old], store=store)
    assert len(old.id) == 16  # DB id 是 16 位
    short = old.id[:8]        # dump 里 agent 看到的 8 位

    # agent 传 8 位前缀 → 应解析成功,旧条标 corrected_by
    new = _ki("纠正结论", scope=scope, root_cause="新")
    new.corrects = [short]
    memorize_items([new], store=store)
    assert store.get(old.id).corrected_by == new.id  # 前缀解析成功 → 回填了

    # set_invalid 也接受前缀(CLI memory invalidate 同路径)
    other = _ki("另一条待失效", scope=scope, root_cause="x")
    memorize_items([other], store=store)
    assert store.set_invalid(other.id[:8]) is True
    assert store.get(other.id).invalid_at is not None

    # 前缀歧义(两条同 8 位前缀)→ mark_corrected 拒绝不改(宁漏不错)。
    # 直接 SQL 造两条同前缀 id 的行(绕开 memorize 的 content-addressed 重算)。
    base = "deadbeefdeadbeef"  # 16 位
    _cols = ("id,kind,repo,owner,codebase,summary,confidence,source_tier,"
             "valid_at,created_at,updated_at")
    for kid, sname in [(base, "歧义甲"), (base[:8] + "cafef00d", "歧义乙")]:
        store._conn.execute(
            f"INSERT OR REPLACE INTO knowledge_items({_cols}) "
            f"VALUES(?, 'bug_lesson','wpa','default','wpa',?,0.5,'delegate','2026-01-01','2026-01-01','2026-01-01')",
            (kid, sname),
        )
    store._conn.commit()
    assert store.mark_corrected(base[:8], corrected_by=new.id) is False  # 歧义 → 拒绝(宁漏不错)


def test_recall_demotes_corrected(store, scope):
    """被纠正条目(corrected_by 非空)在 recall 中被 0.3× 降权 → 排在纠正者后面(但仍可见)。

    场景:两条同主题同 embedding(检索分数一样),但旧条被标记 corrected_by → recall 排序时新条在前。
    """
    old = _ki("根因A(误诊) radio work 阻塞", scope=scope, symptom="扫描挂起",
              root_cause="旧", embedding=[0.1, 0.9, 0.0, 0.5, 0.3, 0.2, 0.1, 0.0])
    memorize_items([old], store=store)
    new = _ki("根因B(纠正A) radio work 竞态", scope=scope, symptom="扫描挂起",
              root_cause="新", embedding=[0.1, 0.9, 0.0, 0.5, 0.3, 0.2, 0.1, 0.0])
    new.corrects = [old.id]
    memorize_items([new], store=store)

    class FakeEmb:
        def embed_query(self, q):
            return np.asarray([0.1, 0.9, 0.0, 0.5, 0.3, 0.2, 0.1, 0.0], dtype=np.float32)

    hits = recall("radio work", scope, store=store, embedder=FakeEmb(), reranker=None, top_k=5)
    assert len(hits) == 2                           # 两条都召回(被纠正条仍可见,不过滤)
    # 纠正者(new)应排第一(分数高),被纠正者(old)排第二(降权后)
    assert hits[0].item_id == new.id
    assert hits[1].item_id == old.id
    assert hits[1].corrected_by == new.id           # RecallHit 透传了 corrected_by


# ── recall 多路 RRF + 衰减 ──────────────────────────────────────

def test_rrf_fuse_prefers_multi_voice_hits():
    h = RecallHit(summary="x", score=0.0, item_id="abc")
    fused = _rrf_fuse([
        [h, RecallHit(summary="y", score=0.0, item_id="def")],
        [RecallHit(summary="z", score=0.0, item_id="abc")],   # abc 在两路都出现
    ])
    assert fused[0].item_id == "abc"           # 跨路一致 → 第一
    assert fused[0].score > fused[1].score


def test_recall_hit_render_includes_item_id_for_memory_path():
    """memory 路 RecallHit 带 item_id → render() 输出 id=xxxxxxxx(纠正链要用)。

    code/structural 路 item_id=None → 不输出(避免 id=None 噪声)。e2e 暴露:memory_memorize
    (corrects=[...]) docstring 承诺「传 recall 输出里看到的 id」,但 render 不输出 id = 闭环走不通。
    """
    # memory 路带 id → 渲染
    h_mem = RecallHit(summary="旧误诊", score=0.5, source="memory", item_id="b448561affff")
    assert "id=b448561a" in h_mem.render(), h_mem.render()
    # code/structural 路 item_id=None → 不渲染 id(避免 id=None)
    h_code = RecallHit(summary="某代码块", score=0.5, source="code", file="a.c", line_start=10)
    assert "id=" not in h_code.render(), h_code.render()


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


# ── Phase 1:consolidate 矛盾检测 + 语义去重 ────────────────────────


def test_consolidate_detects_contradictions(store, scope):
    """矛盾检测(只标不裁):同主题(同 symptom)不同根因的两条 active 高 conf → 都打 needs_review + 进统计。

    不自动选边谁对(语义判断);幂等:再跑一次 contradictions 仍=2,标签不重复加。
    """
    # 同 symptom="扫描挂起"(→ _same_subject=True),不同 root_cause(→ _same_conclusion=False)= 矛盾。
    a = _ki("A 派结论 radio work 阻塞", scope=scope, symptom="扫描挂起", root_cause="未释放 radio work")
    b = _ki("B 派结论 scan 竞态", scope=scope, symptom="扫描挂起", root_cause="scan 竞态覆盖")
    memorize_items([a, b], store=store)
    # delegate 初始置信度 0.5,正好踩 _CONTRADICTION_MIN_CONFIDENCE 线 → 矛盾检测覆盖。
    stats = consolidate(scope, store=store, promote_access_count=99)  # promote 抬高,只测矛盾
    assert stats["contradictions"] == 2                              # 两条都被标
    assert "needs_review" in store.get(a.id).tags
    assert "needs_review" in store.get(b.id).tags
    # 幂等:再跑不重复加标签、统计不变。
    stats2 = consolidate(scope, store=store, promote_access_count=99)
    assert stats2["contradictions"] == 2
    assert store.get(a.id).tags.count("needs_review") == 1           # 没重复加


def test_consolidate_detects_contradictions_no_symptom(store, scope):
    """e2e 暴露的回退场景:bug_lesson symptom 空(CLI/MCP 写入常见)+ 同 evidence 文件 + 不同 root_cause → 仍判矛盾。

    _same_subject 对 bug_lesson:symptom 都非空→比 symptom;任一空→回退比 evidence 文件。
    不回退会让两条同 bug 不同根因的 bug_lesson(symptom 都空)漏判为"不同主题"。
    """
    a = _ki("A派 radio work 阻塞", scope=scope, symptom="", root_cause="未释放 radio work", file="scan.c", line=10)
    b = _ki("B派 scan 竞态", scope=scope, symptom="", root_cause="scan 竞态覆盖", file="scan.c", line=10)
    memorize_items([a, b], store=store)
    stats = consolidate(scope, store=store, promote_access_count=99)
    assert stats["contradictions"] == 2                              # symptom 空回退 evidence 仍判矛盾


def test_consolidate_no_contradiction_same_conclusion(store, scope):
    """同主题同结论(同 symptom 同 root_cause)→ 不算矛盾,不标 needs_review(边界:别误报)。"""
    a = _ki("同一根因 radio work", scope=scope, symptom="扫描挂起", root_cause="死锁")
    b = _ki("同根因复述 radio work", scope=scope, symptom="扫描挂起", root_cause="死锁")
    memorize_items([a, b], store=store)
    stats = consolidate(scope, store=store, promote_access_count=99)
    assert stats["contradictions"] == 0


def test_consolidate_semantic_duplicates(store, scope):
    """语义去重:同 kind + embedding 高 cosine 的条目 → 报 duplicate_clusters≥1(只报不合)。"""
    # 两条 codebase_fact,向量几乎一样(cosine≈1.0 > 0.92 阈值)→ 疑似重复簇。
    near = [0.12, 0.88, 0.01]
    a = _ki("scan 模块职责 阻塞", scope=scope, kind="codebase_fact", root_cause="", embedding=list(near))
    b = _ki("scan 模块职责 串行化", scope=scope, kind="codebase_fact", root_cause="", embedding=list(near))
    memorize_items([a, b], store=store)
    stats = consolidate(scope, store=store, promote_access_count=99, duplicate_threshold=0.92)
    assert stats["duplicate_clusters"] >= 1
    # 不自动合并:两条仍在(active count 不减)。
    assert store.count(scope) == 2


def test_consolidate_skips_domain_knowledge(store, scope):
    """domain_knowledge 不参与任何 pass:不升级 / 不进矛盾检测 / 不进语义去重(领域常理天然语义近邻是正常的)。"""
    dk1 = KnowledgeItem(
        kind="domain_knowledge", repo="wpa", scope=scope, summary="4-way handshake EAPOL-Key 流程",
        kind_detail="domain", source_tier=SourceTier.stated, embedding=[0.5, 0.5, 0.0],
    )
    dk2 = KnowledgeItem(
        kind="domain_knowledge", repo="wpa", scope=scope, summary="4-way handshake PTK 派生",
        kind_detail="domain", source_tier=SourceTier.stated, embedding=[0.5, 0.5, 0.0],  # 同向量(高 cosine)
    )
    memorize_items([dk1, dk2], store=store)
    # 频繁 bump 也不会被升级(domain_knowledge 排除)。
    for _ in range(5):
        store.bump_access(dk1.id)
    stats = consolidate(scope, store=store, promote_access_count=3)
    assert stats["promoted"] == 0                                    # domain_knowledge 不升级
    assert stats["contradictions"] == 0                              # 不进矛盾检测
    assert stats["duplicate_clusters"] == 0                          # 不进语义去重


# ── Phase 2:B4 stale 检测 + B3 补丁已合入检测 ─────────────────────


def test_consolidate_stale_detection(store, scope):
    """stale 检测:超过 N 天没人翻 → 打 stale 标签(只标不降权);近期翻过的不标;幂等。"""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    fresh = _ki("新教训 scan 挂起", scope=scope)
    old = _ki("老教训 radio 泄漏", scope=scope)
    memorize_items([fresh, old], store=store)
    # 把 old 的 last_recalled 拨到 400 天前(伪造"很久没人翻")。
    store._conn.execute(
        "UPDATE knowledge_items SET last_recalled=? WHERE id=?",
        ((now - timedelta(days=400)).isoformat(), old.id),
    )
    stats = consolidate(scope, store=store, promote_access_count=99, stale_after_days=365, now=now)
    assert stats["stale"] == 1                                       # 只有 old 被标
    assert "stale" in store.get(old.id).tags
    assert "stale" not in store.get(fresh.id).tags                   # fresh(刚建)不标
    # 只标不降权:confidence 不变。
    assert store.get(old.id).confidence == store.get(fresh.id).confidence
    # 幂等:再跑 stale 仍=1(已标跳过),不重复。
    stats2 = consolidate(scope, store=store, promote_access_count=99, stale_after_days=365, now=now)
    assert stats2["stale"] == 1
    assert store.get(old.id).tags.count("stale") == 1


def test_consolidate_stale_uses_last_recalled(store, scope):
    """stale 基准取 last_recalled 与 created_at 的较晚者:老卡但最近被召回过 → 不算 stale。"""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    it = _ki("老卡但常被翻", scope=scope)
    it.created_at = now - timedelta(days=500)                        # 建卡 500 天前
    memorize_items([it], store=store)
    # 最近被召回过(10 天前)→ 不 stale。
    store._conn.execute(
        "UPDATE knowledge_items SET last_recalled=? WHERE id=?",
        ((now - timedelta(days=10)).isoformat(), it.id),
    )
    stats = consolidate(scope, store=store, promote_access_count=99, stale_after_days=365, now=now)
    assert stats["stale"] == 0
    assert "stale" not in store.get(it.id).tags


def _git_repo_with_patch(tmp_path):
    """造一个真 git 仓 + 一个补丁:先 commit 原文件,再把"加一行守卫"的改动写进工作树。

    返回 (repo_path, patch_text):树里已含改动 → git apply --check --reverse 能过(判"已合入")。
    """
    import subprocess

    repo = tmp_path / "grepo"
    repo.mkdir()
    f = repo / "scan.c"
    f.write_text("int scan(void) {\n    return 0;\n}\n")
    _run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)  # noqa: E731
    _run("init", "-q")
    _run("config", "user.email", "t@t")
    _run("config", "user.name", "t")
    _run("add", "-A")
    _run("commit", "-qm", "init")

    # 补丁:给 scan.c 加一行守卫(unified diff,含正确行号)。
    new_body = "int scan(void) {\n    if (0) return -1;\n    return 0;\n}\n"
    patch = (
        "--- a/scan.c\n+++ b/scan.c\n"
        "@@ -1,3 +1,4 @@\n int scan(void) {\n"
        "+    if (0) return -1;\n"
        "     return 0;\n }\n"
    )
    # 把改动写进树(= 补丁"已合入"状态,reverse-apply 能过)。
    f.write_text(new_body)
    return str(repo), patch


def test_consolidate_merged_upstream(store, scope, tmp_path):
    """B3:补丁改动已在仓里(reverse-apply 过)→ merged_upstream 标签 + confidence 打折,不失效。"""
    repo_path, patch_applied = _git_repo_with_patch(tmp_path)
    a = _ki("sdp 溢出教训", scope=scope)
    a.fix_patch = patch_applied
    memorize_items([a], store=store)
    conf_before = store.get(a.id).confidence

    stats = consolidate(scope, store=store, promote_access_count=99, repo_path=repo_path, merged_discount=0.5)
    assert stats["merged_upstream"] == 1
    got = store.get(a.id)
    assert "merged_upstream" in got.tags
    assert abs(got.confidence - conf_before * 0.5) < 1e-9            # 打了对折
    assert got.active is True                                        # 不失效(只标不删)
    assert got.invalid_at is None
    # 幂等(计数=当前态,写入=只一次):再跑统计稳定=1,confidence 不被重复打折。
    stats2 = consolidate(scope, store=store, promote_access_count=99, repo_path=repo_path, merged_discount=0.5)
    assert stats2["merged_upstream"] == 1                            # 计数=当前态(含已标)
    assert abs(store.get(a.id).confidence - conf_before * 0.5) < 1e-9  # 没被折第二次


def test_consolidate_merged_upstream_not_merged(store, scope, tmp_path):
    """B3 边界:补丁没合入(树是原始状态)→ 不标不打折。"""
    # _git_repo_with_patch 最后把改动写进了树;这里再造一个"原始树 + 补丁"的组合。
    import subprocess

    repo = tmp_path / "grepo2"
    repo.mkdir()
    f = repo / "scan.c"
    f.write_text("int scan(void) {\n    return 0;\n}\n")
    def _run(*args):
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    _run("init", "-q")
    _run("config", "user.email", "t@t")
    _run("config", "user.name", "t")
    _run("add", "-A")
    _run("commit", "-qm", "init")

    patch = (
        "--- a/scan.c\n+++ b/scan.c\n"
        "@@ -1,3 +1,4 @@\n int scan(void) {\n"
        "+    if (0) return -1;\n"
        "     return 0;\n }\n"
    )
    a = _ki("未合入的溢出教训", scope=scope)
    a.fix_patch = patch
    memorize_items([a], store=store)
    conf_before = store.get(a.id).confidence

    stats = consolidate(scope, store=store, promote_access_count=99, repo_path=str(repo), merged_discount=0.5)
    assert stats["merged_upstream"] == 0                             # 树里没有这改动 → 不标
    got = store.get(a.id)
    assert "merged_upstream" not in got.tags
    assert abs(got.confidence - conf_before) < 1e-9                  # 不打折


def test_consolidate_merged_upstream_no_repo_path(store, scope):
    """B3 边界:repo_path=None(自动 consolidate 路径)→ 跳过检测,不算错。"""
    a = _ki("没仓路径的教训", scope=scope)
    a.fix_patch = "--- a/x.c\n+++ b/x.c\n@@ -1 +1 @@\n-a\n+b\n"
    memorize_items([a], store=store)
    stats = consolidate(scope, store=store, promote_access_count=99, repo_path=None)
    assert stats["merged_upstream"] == 0
    assert "merged_upstream" not in store.get(a.id).tags


def test_consolidate_tags_not_clobbered_across_passes(store, scope, tmp_path):
    """e2e 暴露的标签竞写 bug:同一条既"已合入上游"(pass ④)又"过期"(pass ⑤)→ 两个标签都得在。

    病根:五个 pass 共享 list_items 快照,后跑的 pass 用旧快照 tags 整体覆盖写回,
    把先跑 pass 打的标签洗掉了(merged_upstream 被 stale 覆盖丢)。修复 = 写前重读 DB 最新 tags。
    """
    repo_path, patch_applied = _git_repo_with_patch(tmp_path)
    a = _ki("既合入又过期的教训", scope=scope)
    a.fix_patch = patch_applied
    memorize_items([a], store=store)

    # created_at 拨回 400 天前 → 同时触发 pass ④(补丁已合入)和 pass ⑤(stale)。
    from datetime import UTC, datetime, timedelta
    old = datetime.now(UTC) - timedelta(days=400)
    conn = store._conn
    conn.execute("UPDATE knowledge_items SET created_at = ? WHERE id = ?", (old.isoformat(), a.id))

    stats = consolidate(scope, store=store, promote_access_count=99, repo_path=repo_path,
                        merged_discount=0.5, stale_after_days=365.0)
    assert stats["merged_upstream"] == 1
    assert stats["stale"] == 1
    got = store.get(a.id)
    assert "merged_upstream" in got.tags        # pass ④ 的标签没被 pass ⑤ 洗掉(e2e 踩过的 bug)
    assert "stale" in got.tags                   # pass ⑤ 的标签也在
    assert got.active is True                    # 全程只标不删


def test_same_subject_nearby_lines_only(store, scope):
    """e2e 暴露的误报:同文件但行号差大(不同 bug)→ 不判同主题,不误报矛盾。

    _same_subject 的 evidence 回退收紧为"同文件且行号差 ≤5";scan.c:2 溢出和 scan.c:3 越界
    是同处相邻行(仍判矛盾),scan.c:2 和 scan.c:999 是两个不相干 bug(不判)。
    """
    # 相邻行(差 1 ≤5):同主题 → 矛盾对计数。
    a = _ki("A派 溢出", scope=scope, symptom="", root_cause="结论甲", file="scan.c", line=10)
    b = _ki("B派 越界", scope=scope, symptom="", root_cause="结论乙", file="scan.c", line=12)
    memorize_items([a, b], store=store)
    stats = consolidate(scope, store=store, promote_access_count=99)
    assert stats["contradictions"] == 2

    # 远行号(差 >5):同文件不同 bug → 不判同主题 → 不误报矛盾。
    c = _ki("C派 野指针", scope=scope, symptom="", root_cause="结论丙", file="other.c", line=10)
    d = _ki("D派 死锁", scope=scope, symptom="", root_cause="结论丁", file="other.c", line=999)
    memorize_items([c, d], store=store)
    stats2 = consolidate(scope, store=store, promote_access_count=99)
    # c/d 是新加的两条,旧的 a/b 已标过(计数=当前态=2 恒定),c/d 不给它们加数 → 总数不变。
    assert stats2["contradictions"] == 2         # 若行号收紧失效,c/d 也会被算进矛盾对(=4)


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


# ── Phase 3:CJK 分词(FTS standalone + jieba)+ 治理标签展示 ────────────

def test_segment_only_cjk(scope):
    """tokenize.segment:只切 CJK 段(英文标识符/路径原样),停用词滤掉,jieba 缺席时原样返回。"""
    from hyperion.services.memory.backends.native import tokenize

    if tokenize.jieba_available():
        out = tokenize.segment("蓝牙L2CAP面向连接的可靠传输,扫描阻塞所有站点")
        toks = out.split()
        assert "蓝牙" in toks and "扫描" in toks and "阻塞" in toks   # 2 字技术词被切出
        assert "L2CAP" in toks                                        # 英文段不进 jieba,原样保留
        assert "的" not in toks                                       # 停用词滤掉
    # jieba 缺席的降级路径:monkeypatch 成 None → 原样返回(不抛)。
    saved = tokenize._jieba
    try:
        tokenize._jieba = None
        tokenize._jieba_tried = True
        assert tokenize.segment("扫描阻塞") == "扫描阻塞"
    finally:
        tokenize._jieba = saved


def test_bm25_chinese_match(store, scope):
    """CJK 分词端到端:中文 summary 入库(索引侧分词)→ 纯中文查询(查询侧分词)→ BM25 命中。

    这是 Phase 3 的核心验收:unicode61 时代"扫描"匹配不上"扫描会阻塞所有站点"(整段一个
    token);分词后两侧同切,2 字查询词能命中。embedder 不参与(纯 BM25 路)。
    """
    a = _ki("蓝牙扫描会阻塞所有站点扫描", scope=scope)
    b = _ki("整数溢出未校验导致越界写", scope=scope)
    memorize_items([a, b], store=store)

    hits = store.search_bm25("扫描 阻塞", scope)
    assert hits and hits[0][0].id == a.id                     # 查询词命中第一条
    misses = store.search_bm25("毫不相关的词", scope)
    assert all(h[0].id != a.id for h in misses) or not misses  # 不相干查询不误命中 a


def test_fts_migration_rebuild_and_idempotent(tmp_path):
    """migration:老库(external-content FTS + 触发器)打开即重建 standalone + 全量重灌,幂等。

    手工造一个老结构库(external-content FTS + 三触发器 + 一条中文数据),用 MemoryStore
    打开 → 触发 _migrate_fts_standalone:触发器消失、FTS 无 content= 、中文可查、
    再开一次不重复重灌(ki_meta 标记幂等)。
    """
    import sqlite3

    db = tmp_path / "memory.db"
    old_ddl_fts = (
        "CREATE VIRTUAL TABLE ki_fts USING fts5(summary, detail, root_cause, "
        "content='knowledge_items', content_rowid='rowid', tokenize='unicode61 remove_diacritics 2')"
    )
    cols = ("id TEXT PRIMARY KEY, kind TEXT NOT NULL, repo TEXT NOT NULL, owner TEXT NOT NULL, "
            "codebase TEXT NOT NULL, summary TEXT NOT NULL, detail TEXT, symptom TEXT, root_cause TEXT, "
            "fix_patch TEXT, blast_radius_files TEXT, kind_detail TEXT, commit_sha TEXT, evidence TEXT, "
            "source TEXT, source_tier TEXT, confidence REAL, access_count INTEGER, last_recalled TEXT, "
            "valid_at TEXT NOT NULL, invalid_at TEXT, created_at TEXT NOT NULL, related TEXT, tags TEXT, "
            "superseded_by TEXT, corrected_by TEXT, source_url TEXT, embedding BLOB, updated_at TEXT NOT NULL")
    conn = sqlite3.connect(db)
    conn.executescript(
        f"CREATE TABLE knowledge_items({cols});"
        + old_ddl_fts
        + ";CREATE TRIGGER ki_fts_ai AFTER INSERT ON knowledge_items BEGIN "
        "INSERT INTO ki_fts(rowid, summary, detail, root_cause) "
        "VALUES (new.rowid, new.summary, new.detail, new.root_cause); END;"
        "CREATE TABLE ki_meta(key TEXT PRIMARY KEY, value TEXT);"
    )
    conn.execute(
        "INSERT INTO knowledge_items(id, kind, repo, owner, codebase, summary, valid_at, created_at, "
        "related, tags, updated_at) VALUES ('old1','bug_lesson','r','default','oldcb','扫描会阻塞所有站点',"
        "'2026-01-01','2026-01-01','[]','[]','2026-01-01')"
    )
    conn.execute(
        "INSERT INTO ki_fts(rowid, summary, detail, root_cause) "
        "SELECT rowid, summary, detail, root_cause FROM knowledge_items"
    )
    conn.commit()
    conn.close()

    s = MemoryStore(tmp_path)                     # 打开即迁移
    ddl = s._conn.execute("SELECT sql FROM sqlite_master WHERE name='ki_fts'").fetchone()
    assert "content=" not in ddl["sql"]                          # 重建为 standalone
    assert s._conn.execute("SELECT name FROM sqlite_master WHERE name='ki_fts_ai'").fetchone() is None
    hits = s.search_bm25("扫描 阻塞", Scope(codebase="oldcb"))    # 重灌后中文可查
    assert hits and hits[0][0].id == "old1"
    s.close()

    s2 = MemoryStore(tmp_path)                    # 幂等:标记位在 → 不再重灌
    n = s2._conn.execute("SELECT count(*) AS c FROM ki_fts").fetchone()["c"]
    assert n == 1
    s2.close()


def test_recall_chinese_bm25_voice(store, scope):
    """recall 全链(无 embedder):纯中文 query 走 BM25 路也能召回中文记忆(不依赖向量 API)。

    对齐真实场景:embedder=None(离线)时中文查询此前完全失明(unicode61 整段 token),
    Phase 3 后 BM25 路独立可召回 —— 减少 domain_knowledge 中文知识对向量 API 的依赖。
    """
    a = _ki("P2P 扫描进行时会触发闸门,阻塞所有站点扫描", scope=scope)
    memorize_items([a], store=store)
    hits = recall("扫描 阻塞", scope, store=store, embedder=None, reranker=None, top_k=3)
    assert hits and hits[0].item_id == a.id
