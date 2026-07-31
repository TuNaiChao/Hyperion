"""bug-RCA workflow StateGraph 组装(多阶段委托 + 迭代 verify-refine,R3.1 #54-rework)。

八节点线性流水线:
  ingest → recall → localize → assemble_localize → delegate_localize_loop
    → assemble_repair → delegate_repair_loop → report_memorize

R3.1 #54-rework(2026-07-30,B):弃多候选采样投票,改双循环同会话 verify-refine ——
  ① delegate_localize_loop:阶段① 定位,max K1 轮(verdict=needs_revisit 则 --continue 重定位);
  ② delegate_repair_loop:阶段② 修复,max K2 轮(git diff 观察 patch + validate_patch 门控 +
     verdict=needs_fix 则 --continue 再修)。
两阶段共用一个 opencode session(--continue 链;per-bug workspace 隔离),verdict 由 opencode
证伪式自审产出,执行硬门控由 Hyperion validate_patch(非 LLM)。收敛靠每 delegate call 的 steps +
单 schema + max-loop 兜底(不重蹈 glm-5.2 单 loop 97K 不收敛)。rerank 降为兜底(默认关)。
LangGraph StateGraph;run() 是入口(CLI / 测试调)。
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from hyperion.workflows.bug_rca.nodes import (
    node_assemble_localize,
    node_assemble_repair,
    node_delegate_localize_loop,
    node_delegate_repair_loop,
    node_ingest,
    node_localize,
    node_recall,
    node_report_memorize,
)
from hyperion.workflows.bug_rca.state import BugRcaState


def build_graph():
    """构建并编译 bug-RCA verify-refine StateGraph(返回 CompiledStateGraph)。"""
    g = StateGraph(BugRcaState)
    g.add_node("ingest", node_ingest)
    g.add_node("recall", node_recall)
    g.add_node("localize", node_localize)
    # 阶段① 定位(verify-refine 循环)
    g.add_node("assemble_localize", node_assemble_localize)
    g.add_node("delegate_localize_loop", node_delegate_localize_loop)
    # 阶段② 修复(verify-refine 循环)
    g.add_node("assemble_repair", node_assemble_repair)
    g.add_node("delegate_repair_loop", node_delegate_repair_loop)
    g.add_node("report_memorize", node_report_memorize)
    # 线性(even if 未过 gate 也继续出报告 + 记 lesson,故无分支)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "recall")
    g.add_edge("recall", "localize")
    g.add_edge("localize", "assemble_localize")
    g.add_edge("assemble_localize", "delegate_localize_loop")
    g.add_edge("delegate_localize_loop", "assemble_repair")
    g.add_edge("assemble_repair", "delegate_repair_loop")
    g.add_edge("delegate_repair_loop", "report_memorize")
    g.add_edge("report_memorize", END)
    return g.compile()


async def run(repo_root: str, trigger: str) -> dict:
    """跑完整 bug-RCA verify-refine workflow,返回最终 state(含 report_path / patch_path / lesson)。

    repo_root:仓库根。
    trigger  :bug 线索(日志摘要 / 问题描述 / 漏洞报告关键句)。
    """
    graph = build_graph()
    initial: BugRcaState = {"repo_root": repo_root, "trigger": trigger}
    return await graph.ainvoke(initial)
