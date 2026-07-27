"""代码理解服务 · 评测指标(P1.3 eval/scorer.py)。

这一层干什么
------------
retrieval.py 给出检索结果(top-k chunk id 列表),这层算「结果有多准」:
Recall@k / Precision@k / HitRate@k / MRR / nDCG@k / Acc@k。纯函数,零依赖,BEIR 标准。

为什么 recall@k 是主指标(设计 §11)
-----------------------------------
下游是 RAG(agent 拿 top-k chunk 进上下文推理):正确 chunk 进了 top-k 就能被 LLM 用上,
recall 直接度量「正确信号有没有进上下文」。precision 由下游 LLM 兜底(它能忽略噪音 chunk),
但 recall 漏的 chunk 下游无从挽回。故 P1.3 退出标准瞄准 L2 recall@5 ≥ 0.55。

多标签(一个 query 可能有多个 gold chunk)
------------------------------------------
fix commit 常改多个符号 → gold 是个集合。这里的 MRR/nDCG 都按多标签定义(第一个命中 /
分级相关),不是 CRG scorer 那种单标签(CRG scorer 不够用,见 §11)。

对外提供
--------
- recall_at_k / precision_at_k / hit_rate_at_k / reciprocal_rank / ndcg_at_k / acc_at_k
- mean_metrics / group_metrics(聚合 + 按 tier 分档)
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence


def recall_at_k(retrieved: Sequence[str], gold: set[str], k: int) -> float:
    """Recall@k = |top-k ∩ gold| / |gold|。gold 空 → 0.0(调用方应过滤空 gold query)。"""
    if not gold:
        return 0.0
    return len(set(retrieved[:k]) & gold) / len(gold)


def precision_at_k(retrieved: Sequence[str], gold: set[str], k: int) -> float:
    """Precision@k = |top-k ∩ gold| / k。"""
    if k <= 0:
        return 0.0
    return len(set(retrieved[:k]) & gold) / k


def hit_rate_at_k(retrieved: Sequence[str], gold: set[str], k: int) -> float:
    """HitRate@k:top-k 里至少命中一个 gold(单查询 0/1;对查询集求均值得 hit rate)。"""
    return 1.0 if (set(retrieved[:k]) & gold) else 0.0


def reciprocal_rank(retrieved: Sequence[str], gold: set[str]) -> float:
    """多标签 MRR:第一个 gold 命中位置的倒数(1/rank);无命中 0.0。"""
    for i, rid in enumerate(retrieved, start=1):
        if rid in gold:
            return 1.0 / i
    return 0.0


def ndcg_at_k(retrieved: Sequence[str], gold: set[str], k: int) -> float:
    """nDCG@k(二元相关性):DCG@k / IDCG@k。

    DCG@k  = Σ_{i=1..k} rel_i / log2(i+1),  rel_i = 1 if retrieved[i-1] ∈ gold else 0
    IDCG@k = Σ_{i=1..min(k,|gold|)} 1 / log2(i+1)   (理想排序:所有 gold 排最前)
    """
    if not gold:
        return 0.0
    dcg = sum(1.0 / math.log2(i + 1) for i, rid in enumerate(retrieved[:k], start=1) if rid in gold)
    ideal_n = min(k, len(gold))
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_n + 1))
    return dcg / idcg if idcg > 0 else 0.0


def acc_at_k(retrieved: Sequence[str], gold: set[str], k: int) -> float:
    """Acc@k(SWE-bench 风格,全有或全无):top-k 覆盖【所有】 gold 才算 1.0。"""
    if not gold:
        return 0.0
    return 1.0 if set(retrieved[:k]) >= gold else 0.0


def mean_metrics(rows: Iterable[dict], keys: Iterable[str]) -> dict[str, float]:
    """对一组 per-query 指标行求均值。keys = 要聚合的指标名(如 recall@5 / precision@5 / mrr)。"""
    rows = list(rows)
    keys = list(keys)
    if not rows:
        return {k: 0.0 for k in keys}
    return {k: (sum(r.get(k, 0.0) for r in rows) / len(rows)) for k in keys}


def group_metrics(rows: Iterable[dict], by: str, keys: Iterable[str]) -> dict[str, dict[str, float]]:
    """按某字段(如 tier=L1/L2/L3)分组聚合 → {组值: {指标: 均值}}。"""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(str(r.get(by, "?")), []).append(r)
    return {g: mean_metrics(grp, keys) for g, grp in groups.items()}
