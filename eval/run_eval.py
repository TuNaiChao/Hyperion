"""评测入口(P1.3):跑一个评测集,出 recall/precision/MRR/nDCG 报告。

用法:
  uv run python eval/run_eval.py eval/sets/rootrecall.jsonl rootrecall [--base-dir data/code_index] [--top-k 5]

前提:对应 repo 的索引已用 `index.py: build_index` 建好(在 base-dir/<repo>/lancedb)。
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from rootrecall.platform.config import get_app_config
from rootrecall.services.code_index.embed import create_embedder
from rootrecall.services.code_index.eval.runner import format_report, load_eval_set, run_eval
from rootrecall.services.code_index.retrieval import create_reranker
from rootrecall.services.code_index.store import LanceDBStore


def main() -> None:
    ap = argparse.ArgumentParser(description="RootRecall 代码检索评测")
    ap.add_argument("eval_set", help="JSONL 评测集路径(每行 {query, gold, tier})")
    ap.add_argument("repo", help="已建索引的 repo 名(对应 base-dir/<repo>)")
    ap.add_argument("--base-dir", default="data/code_index", help="向量库根目录")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--no-reranker", action="store_true", help="关闭 reranker(只跑 hybrid+RRF)")
    args = ap.parse_args()

    load_dotenv()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    cfg = get_app_config()
    emb = create_embedder(cfg.code_index.embedding)
    rr = None if args.no_reranker else create_reranker(getattr(cfg.code_index, "reranker", {}))
    store = LanceDBStore(args.base_dir)
    es = load_eval_set(args.eval_set)

    # 校验:gold id 必须都在索引里,否则该查询被不公平记 0
    tbl = store._open_or_create(args.repo)  # 白盒:校验用
    if tbl is None:
        sys.exit(f"❌ 索引不存在: {Path(args.base_dir) / args.repo / 'lancedb'}(先跑 build_index)")
    indexed = {r["id"] for r in tbl.to_arrow().to_pylist()}
    bad = [(q["query"][:40], [g for g in q.get("gold", []) if g not in indexed]) for q in es]
    bad = [(q, gs) for q, gs in bad if gs]
    if bad:
        print("⚠️ 以下 gold id 不在索引(会不公平记 recall=0),请修正评测集:", file=sys.stderr)
        for q, gs in bad:
            print(f"   {q}: {gs}", file=sys.stderr)

    rep = run_eval(es, args.repo, emb, store, rr, top_k=args.top_k)
    print(format_report(rep))
    print(f"\nreranker: {'off' if rr is None else type(rr).__name__}")


if __name__ == "__main__":
    main()
