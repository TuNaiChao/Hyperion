"""patch_report workflow StateGraph 组装(P-A 1b,一组 PR → cited 聚合报告)。

pipeline(镜像 deep_research/graph.py 的 StateGraph 模式):
  ingest → fetch_prs → analyze → report

  ingest    建工作区 + 注册 scope
  fetch_prs 并发抓取每条 PR 的 diff + meta(GitHubFetcher;Gerrit 同接口)
  analyze   每 PR 一个分析任务并发跑(_analyze_one_pr 核心算法,窗口展示):
            validate_patch(apply 门)+ CodeGraph.analyze_changes(risk/affected)+ cited-reporter → PRFinding
  report    渲染 cited 报告 + 跨 PR 聚合 + Verifier(Checkpoint 4 实装 _aggregate + _verify + render)

Checkpoint 3 跑通 fetch→analyze→findings;Checkpoint 4 在 analyze 后插 aggregate、report 后插 memorize,
并替换 report 节点为真 cited 渲染 + 零幻觉回查。

LangGraph StateGraph;run() 是入口(CLI `rootrecall patch-report` / 测试调)。
"""

from __future__ import annotations

from pathlib import Path

from langgraph.graph import END, START, StateGraph

from rootrecall.workflows.patch_report.nodes import (
    node_aggregate,
    node_analyze,
    node_fetch_prs,
    node_ingest,
    node_memorize,
    node_report,
)
from rootrecall.workflows.patch_report.state import PatchReportState


def build_graph():
    """构建并编译 patch_report StateGraph(返回 CompiledStateGraph)。"""
    g = StateGraph(PatchReportState)
    g.add_node("ingest", node_ingest)
    g.add_node("fetch_prs", node_fetch_prs)
    g.add_node("analyze", node_analyze)
    g.add_node("aggregate", node_aggregate)
    g.add_node("report", node_report)
    g.add_node("memorize", node_memorize)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "fetch_prs")
    g.add_edge("fetch_prs", "analyze")
    g.add_edge("analyze", "aggregate")
    g.add_edge("aggregate", "report")
    g.add_edge("report", "memorize")
    g.add_edge("memorize", END)
    return g.compile()


async def run(prs: list[str], *, repo: str, codebase: str,
              owner: str = "default",
              concurrency: int = 3) -> dict:
    """跑 patch_report workflow,返回最终 state(含 report_path / findings)。

    prs        :PR URL 列表(GitHub `github.com/.../pull/N`;Gerrit 同接口)。
    repo       :代码仓根(CRG 图 + validate_patch + verify 都用它;需先 `rootrecall index` 建图)。
    codebase   :仓库名(CRG db 目录名 / 记忆 scope.codebase)。
    concurrency:并发抓取/分析(GitHub 限速友好,默认 3)。
    (曾有 deep 参数留 stretch,2026-08-14 删——空壳违背诚实信号原则;真需要逐 PR 深审时
    按 deep_research 的子 agent 模式实现,见 docs/p1-p2-improvement-backlog.md #8。)
    """
    graph = build_graph()
    initial: PatchReportState = {
        "repo_root": str(Path(repo).resolve()),
        "codebase": codebase,
        "prs": list(prs),
        "owner": owner,
        "concurrency": concurrency,
    }
    return await graph.ainvoke(initial)
