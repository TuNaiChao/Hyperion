"""native 后端 · 巩固 / 持续学习(consolidate.py)。

干什么(面向小白)
  记忆的"夜间整理"——趁没人翻笔记本时,把里面重复的、打架的、该升级的梳理一遍。
  对标 2026 业界共识:consolidation = keeps / merges / evicts(留 / 合 / 逐)三件事。

三个 pass(对应 keeps / merges,evicts 见 backlog):
  ① 升级 mental_model(keeps):被召回≥N 次(access_count)的教训 → 升级为"稳定规则"(借 Letta 3+ 规则)。
  ② 矛盾检测(只标不裁):同主题不同结论的 active 对 → 打 needs_review 标签 + 进统计上报。
     不自动选边(谁是正确根因是语义判断,踩坑#11:apply 过≠根因对);留给 memory-health-check skill /
     agent / 人裁决。检测本身是确定性的(_same_subject + not _same_conclusion)。
  ③ 语义近邻去重候选(merges):同 scope+同 kind,embedding cosine 超阈值 → 报候选合并对。
     默认只报不自动合(自动合并语义上危险,可能误合近义不同 bug;宁漏不错)。

为什么不物理删 / 不 Weibull 物理降级
  bi-temporal 铁律:永不物理删(能回答"这 bug 在 X 时点还在不在",审计可追溯)。recall 已做 exp 衰减
  打分(检索时降权),consolidate 不重复做物理降级。evict 的"主动降 confidence"见 backlog Phase 2。

记 backlog(刻意不做):
  - 自动失效(补丁合入上游 → invalidate):需接 git/merge_eval,Phase 2。
  - 长期未命中主动降 confidence(evict):Phase 2。
"""

from __future__ import annotations

import logging
from typing import Any

from hyperion.services.memory.backends.native.memorize import _same_conclusion, _same_subject
from hyperion.services.memory.backends.native.store import MemoryStore
from hyperion.services.memory.schema import Scope

logger = logging.getLogger(__name__)

# 矛盾检测考虑的最低置信度(低于此值的条目不参与"打架"判定,噪声不值得报)。
_CONTRADICTION_MIN_CONFIDENCE = 0.5
# 语义近邻去重:cosine 超此阈值才算"疑似重复"。保守(0.92),宁漏不错——误合两个不同 bug 比留两条重复更糟。
_DUPLICATE_COSINE_THRESHOLD = 0.92


def _cosine(a: list[float], b: list[float]) -> float:
    """两向量的 cosine 相似度(纯 numpy)。维度不符 → -1(判为不相似)。"""
    import numpy as np

    va, vb = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
    if va.shape[0] != vb.shape[0] or va.shape[0] == 0:
        return -1.0
    na, nb = float(np.linalg.norm(va)) + 1e-12, float(np.linalg.norm(vb)) + 1e-12
    return float(np.dot(va, vb) / (na * nb))


def consolidate(
    scope: Scope,
    *,
    store: MemoryStore,
    promote_access_count: int = 3,
    detect_contradictions: bool = True,
    detect_duplicates: bool = True,
    duplicate_threshold: float = _DUPLICATE_COSINE_THRESHOLD,
) -> dict[str, Any]:
    """巩固 pass:升级 mental_model + 矛盾检测 + 语义去重候选。返回统计 dict。

    返回:``{scanned, promoted, contradictions, duplicate_clusters}``。
      - contradictions:发现的"同主题不同结论"矛盾对数(已打 needs_review 标签)。
      - duplicate_clusters:疑似语义重复的条目组数(只统计上报,不自动合)。

    domain_knowledge 不参与任何 pass:① 不升级(evergreen 不"毕业");② 不进矛盾检测
    (领域常理无"同主题不同结论"的 bug 式冲突语义);③ 不进语义去重(领域知识条目天然
    语义近邻是正常的,如多条都讲 4-way handshake,不该判重复)。mental_model 已是升级终态,也跳过。

    各 pass 幂等:重复跑不会叠加标签/重复上报(set 去重 + 已标的不再加)。
    """
    stats: dict[str, Any] = {"scanned": 0, "promoted": 0, "contradictions": 0, "duplicate_clusters": 0}
    items = store.list_items(scope)
    stats["scanned"] = len(items)

    # ① 升级 mental_model(keeps)。
    for it in items:
        if it.kind not in ("mental_model", "domain_knowledge") and it.access_count >= promote_access_count:
            store.set_kind(it.id, "mental_model")
            stats["promoted"] += 1

    # ② 矛盾检测(只标不裁):同主题不同结论的 active 高 conf 对 → 打 needs_review 标签。
    if detect_contradictions:
        stats["contradictions"] = _detect_contradictions(store, items)

    # ③ 语义近邻去重候选(只报不合):同 kind 高 cosine 对 → 进统计。
    if detect_duplicates:
        stats["duplicate_clusters"] = _detect_semantic_duplicates(items, duplicate_threshold)

    logger.info(
        "memory.consolidate(%s): 扫 %d,升级 %d,矛盾对 %d,重复簇 %d",
        scope.codebase, stats["scanned"], stats["promoted"], stats["contradictions"], stats["duplicate_clusters"],
    )
    return stats


def _detect_contradictions(store: MemoryStore, items: list) -> int:
    """扫 active 高 conf 条目,找"同主题不同结论"的矛盾对 → 打 needs_review 标签。返回矛盾对数。

    只标不裁:不自动选边谁对(语义判断,踩坑#11),打标签让 memory-health-check skill 聚焦提示裁决。
    _same_subject / _same_conclusion 是确定性 helper(memorize.py 复用),判定结果可复现。
    幂等:已带 needs_review 标签的条目不再重复加(集合去重)。
    """
    # 只在 codebase_fact / bug_lesson 里找矛盾(这两类有"同主题不同结论"语义);domain_knowledge / mental_model 跳过。
    candidates = [
        it for it in items
        if it.kind in ("codebase_fact", "bug_lesson")
        and it.confidence >= _CONTRADICTION_MIN_CONFIDENCE
    ]
    flagged: set[str] = set()
    for i, a in enumerate(candidates):
        for b in candidates[i + 1:]:
            if _same_subject(a, b) and not _same_conclusion(a, b):
                flagged.add(a.id)
                flagged.add(b.id)
    for item_id in flagged:
        it = store.get(item_id)
        if it and "needs_review" not in it.tags:
            store.set_tags(item_id, [*it.tags, "needs_review"])
    return len(flagged)


def _union_find(parent: dict[str, str], x: str) -> str:
    """并查集 find(带路径压缩)。parent 是 {id: 父id};根的父是自己。

    放模块级(不嵌在循环里)——ruff B023:循环内定义的闭包不绑定循环变量,模块级函数无此问题。
    """
    root = x
    while parent[root] != root:
        root = parent[root]
    # 路径压缩:把链上的点都指到根。
    while parent[x] != root:
        parent[x], x = root, parent[x]
    return root


def _count_duplicate_clusters(group: list, threshold: float) -> int:
    """一组同 kind 条目里,互相 embedding cosine≥threshold 的并成簇。返回 size≥2 的簇数。

    并查集:遍历所有对,cosine 超阈值就 union;最后数 size≥2 的连通分量。
    """
    parent = {it.id: it.id for it in group}
    for i, a in enumerate(group):
        for b in group[i + 1:]:
            if _cosine(a.embedding, b.embedding) >= threshold:
                ra, rb = _union_find(parent, a.id), _union_find(parent, b.id)
                if ra != rb:
                    parent[ra] = rb
    # 按 root 聚合,数 size≥2 的簇。
    sizes: dict[str, int] = {}
    for it in group:
        sizes[_union_find(parent, it.id)] = sizes.get(_union_find(parent, it.id), 0) + 1
    return sum(1 for c in sizes.values() if c >= 2)


def _detect_semantic_duplicates(items: list, threshold: float) -> int:
    """扫同 kind 条目,找 embedding cosine 超阈值的疑似重复簇。返回簇数。

    只报不合(自动合并语义上危险:近义不同 bug 可能被误合;宁漏不错)。
    跳过 domain_knowledge(领域知识天然语义近邻,如多条都讲 4-way handshake 是正常的)。
    按 kind 分组(跨 kind 不比),每组用并查集并成簇,返回 size≥2 的簇数。
    """
    # 按 kind 分组(domain_knowledge 跳过);只看有 embedding 的。
    groups: dict[str, list] = {}
    for it in items:
        if it.kind == "domain_knowledge" or not it.embedding:
            continue
        groups.setdefault(it.kind, []).append(it)

    return sum(_count_duplicate_clusters(g, threshold) for g in groups.values() if len(g) >= 2)
