"""eval scorer 指标的离线单测(纯函数,零依赖,BEIR 标准定义)。

重点测 2026-08-11 补的 ``precision_at_min_k``(R-precision 风格,backlog #16):
小 gold 集不被 |gold|/k 封顶(单 gold 命中 = 1.0),修复退出标准 ② 数学不可达。
顺带覆盖 recall / MRR / nDCG / hit / acc / mean_metrics 防回归。
"""
from __future__ import annotations

import pytest

from hyperion.services.code_index.eval.scorer import (
    acc_at_k,
    hit_rate_at_k,
    mean_metrics,
    ndcg_at_k,
    precision_at_k,
    precision_at_min_k,
    recall_at_k,
    reciprocal_rank,
)

# ── precision_at_min_k(R-precision,backlog #16 核心)──────────────────────

def test_rprecision_single_gold_hit_is_one():
    """单 gold + 命中 → 1.0(普通 precision@5 此处封顶 0.2,正是 #16 要修的)。"""
    assert precision_at_min_k(["a", "b", "c", "d", "e"], {"a"}, 5) == 1.0


def test_rprecision_single_gold_miss_is_zero():
    """单 gold + 没命中 → 0.0。"""
    assert precision_at_min_k(["b", "c", "d", "e", "f"], {"a"}, 5) == 0.0


def test_rprecision_multi_gold_partial():
    """双 gold 命中一个 → 0.5(分母 min(5,2) = 2)。"""
    assert precision_at_min_k(["a", "c", "d", "e", "f"], {"a", "b"}, 5) == 0.5


def test_rprecision_multi_gold_all_hit():
    """双 gold 全命中 → 1.0。"""
    assert precision_at_min_k(["a", "b", "c"], {"a", "b"}, 5) == 1.0


def test_rprecision_k_smaller_than_gold_uses_k():
    """k < |gold|:分母 = k,退化为普通 precision@k(不虚高)。"""
    # k=2,双 gold {a,b},retrieved=[a,c] → |{a}|/min(2,2) = 1/2 = 0.5
    assert precision_at_min_k(["a", "c"], {"a", "b"}, 2) == 0.5


def test_rprecision_empty_gold_is_zero():
    """gold 空 → 0.0(与 recall_at_k 一致;调用方应过滤空 gold query)。"""
    assert precision_at_min_k(["a", "b"], set(), 5) == 0.0


def test_rprecision_not_capped_like_plain_precision():
    """对比:同样单 gold 命中,普通 precision@5=0.2(封顶),R-precision=1.0。"""
    retrieved = ["a", "b", "c", "d", "e"]
    assert precision_at_k(retrieved, {"a"}, 5) == 0.2
    assert precision_at_min_k(retrieved, {"a"}, 5) == 1.0


# ── 既有指标回归(防回归)──────────────────────────────────────────────

def test_recall_at_k_multilabel():
    assert recall_at_k(["a", "b", "c"], {"a", "d"}, 5) == 0.5   # 命中 a,漏 d
    assert recall_at_k([], {"a"}, 5) == 0.0
    assert recall_at_k(["a"], set(), 5) == 0.0                 # 空 gold → 0


def test_reciprocal_rank_multilabel():
    assert reciprocal_rank(["x", "a", "y"], {"a", "b"}) == 0.5  # 首命中在 rank 2
    assert reciprocal_rank(["x", "y"], {"a"}) == 0.0


def test_ndcg_hit_acc():
    g = {"a", "b"}
    assert hit_rate_at_k(["x", "a"], g, 5) == 1.0
    assert hit_rate_at_k(["x", "y"], g, 5) == 0.0
    assert ndcg_at_k(["a", "x"], g, 5) > 0.0                    # a 命中 → 非零
    assert acc_at_k(["a", "b", "x"], g, 5) == 1.0               # 覆盖全部 gold
    assert acc_at_k(["a", "x"], g, 5) == 0.0                    # 漏 b


def test_mean_metrics_averages_and_empty():
    rows = [{"recall@5": 0.5, "precision@5": 0.2}, {"recall@5": 1.0, "precision@5": 0.4}]
    m = mean_metrics(rows, ["recall@5", "precision@5"])
    assert m["recall@5"] == pytest.approx(0.75)
    assert m["precision@5"] == pytest.approx(0.3)
    assert mean_metrics([], ["x"]) == {"x": 0.0}
