"""代码理解服务 · 评测 runner(P1.3 eval/runner.py)。

这一层干什么
------------
加载评测集(JSONL:每条 = {query, gold=[chunk_id...], tier}),对每条跑 retrieve,用 scorer
算指标,按 tier 分档聚合 + 出报告。

工程纪律(借 CRG eval/runner.py,见 §11)
---------------------------------------
- **失败语义**:某条 retrieve 抛异常 → status="error",不默认 recall=0/1,**从聚合里排除**
  (不让单条故障污染均值,也不造假胜利)。
- **空 gold 跳过 recall**:负例(gold=[])recall 是 0/0 无意义,从聚合排除,单独计 n_empty。
- **per-query 可观测**:每条留 out_mode(hybrid+rerank / hybrid / empty),便于定位问题。

评测集格式(JSONL,每行一条)
---------------------------
  {"query":"断连处理","gold":["src/a.c:disconnect_cb"],"tier":"L2","note":"..."}
  gold = chunk id 列表({file}:{qualified_name},与 chunker 一致);tier = L1/L2/L3。

对外提供
--------
- load_eval_set(path):读 JSONL。
- run_eval(...):跑评测,返回 report(rows + overall + by_tier + 计数)。
- format_report(report):格式化成 markdown 表。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from rootrecall.services.code_index.embed import Embedder
from rootrecall.services.code_index.eval.scorer import (
    group_metrics,
    hit_rate_at_k,
    mean_metrics,
    ndcg_at_k,
    precision_at_k,
    precision_at_min_k,
    recall_at_k,
    reciprocal_rank,
)
from rootrecall.services.code_index.retrieval import Reranker, retrieve
from rootrecall.services.code_index.store import VectorStore

logger = logging.getLogger(__name__)

_METRIC_KEYS = ["recall@5", "precision@5", "rprecision@5", "mrr", "ndcg@5", "hit@5"]


def load_eval_set(path: Path | str) -> list[dict]:
    """读 JSONL 评测集(每行一条 {query, gold, tier, ...})。"""
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def run_eval(
    eval_set: list[dict],
    repo: str,
    embedder: Embedder,
    store: VectorStore,
    reranker: Reranker | None = None,
    *,
    top_k: int = 5,
    candidate_top_n: int = 50,
) -> dict[str, Any]:
    """对评测集每条跑 retrieve + 算指标,返回 report(rows + overall + by_tier + 计数)。

    - 失败(retrieve 抛异常)→ status="error",从聚合排除。
    - 空 gold → status="empty_gold",从聚合排除(单独计 n_empty)。
    - 聚合只对 status="ok" 的行。
    """
    rows: list[dict] = []
    n_errors = n_empty = 0
    for i, q in enumerate(eval_set):
        query = q.get("query", "")
        gold = set(q.get("gold", []))
        tier = q.get("tier", "?")
        if not gold:
            rows.append({"i": i, "tier": tier, "status": "empty_gold", "query": query})
            n_empty += 1
            continue
        try:
            res = retrieve(query, repo, embedder, store, reranker, top_k=top_k, candidate_top_n=candidate_top_n)
            retrieved = [h.id for h in res.hits]
            rows.append(
                {
                    "i": i, "tier": tier, "status": "ok", "query": query, "out_mode": res.out_mode,
                    "n_hits": len(res.hits),
                    "recall@5": recall_at_k(retrieved, gold, top_k),
                    "precision@5": precision_at_k(retrieved, gold, top_k),
                    "rprecision@5": precision_at_min_k(retrieved, gold, top_k),
                    "mrr": reciprocal_rank(retrieved, gold),
                    "ndcg@5": ndcg_at_k(retrieved, gold, top_k),
                    "hit@5": hit_rate_at_k(retrieved, gold, top_k),
                    "gold": sorted(gold), "top5": retrieved[:top_k],
                }
            )
        except Exception as e:  # 失败语义:不默认 recall,标记 error 并排除
            logger.warning("query %d 检索失败: %s", i, e)
            rows.append({"i": i, "tier": tier, "status": "error", "query": query, "error": str(e)})
            n_errors += 1

    ok = [r for r in rows if r.get("status") == "ok"]
    return {
        "n_total": len(eval_set),
        "n_ok": len(ok),
        "n_errors": n_errors,
        "n_empty_gold": n_empty,
        "overall": mean_metrics(ok, _METRIC_KEYS),
        "by_tier": group_metrics(ok, "tier", _METRIC_KEYS),
        "rows": rows,
    }


def format_report(report: dict[str, Any]) -> str:
    """把 report 格式化成 markdown(总体 + 按 tier 表 + 失败明细)。"""
    lines = [
        f"### 评测报告(n={report['n_total']}, ok={report['n_ok']}, error={report['n_errors']}, empty_gold={report['n_empty_gold']})",
        "",
        "**总体:** " + " | ".join(f"{k}={v:.3f}" for k, v in report["overall"].items()),
        "",
        "**按 tier:**",
        "| tier | n | " + " | ".join(_METRIC_KEYS) + " |",
        "|---|---|" + "---|" * len(_METRIC_KEYS),
    ]
    tier_counts: dict[str, int] = {}
    for r in report["rows"]:
        if r.get("status") == "ok":
            tier_counts[r["tier"]] = tier_counts.get(r["tier"], 0) + 1
    for tier in sorted(report["by_tier"]):
        m = report["by_tier"][tier]
        lines.append(f"| {tier} | {tier_counts.get(tier, 0)} | " + " | ".join(f"{m[k]:.3f}" for k in _METRIC_KEYS) + " |")
    errs = [r for r in report["rows"] if r.get("status") == "error"]
    if errs:
        lines.append("\n**失败明细(前 5):**")
        for r in errs[:5]:
            lines.append(f"- q{r['i']} [{r['tier']}]: {r['error'][:120]}")
    return "\n".join(lines)
