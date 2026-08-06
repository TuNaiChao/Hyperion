"""bug-RCA workflow StateGraph 组装(多阶段委托 + 迭代 verify-refine,R3.1)。

六节点线性流水线(踩坑 #2,2026-07-31:砍 Hyperion 侧定位漏斗 —— 旧 recall/localize/
assemble_localize 三节点与 opencode 重复定位 double localization,改 opencode 自主定位 + MCP 工具。
R3 收尾 ②[b] 又加回 recall_lessons —— 但它**只翻记忆预注入先验、不定位**,和被砍的旧 recall
本质不同,不算重造漏斗):
  ingest → recall_lessons → delegate_localize_loop → assemble_repair → delegate_repair_loop → report_memorize

R3.1 #54-rework(B):双循环同会话 verify-refine ——
  ① delegate_localize_loop:阶段① 定位,max K1 轮(verdict=needs_revisit 则 --continue 重定位);
  ② delegate_repair_loop:阶段② 修复,max K2 轮(git diff 观察 patch + validate_patch 门控 +
     verdict=needs_fix 则 --continue 再修)。
两阶段共用一个 opencode session(--continue 链;per-bug workspace 隔离),verdict 由 opencode
证伪式自审产出,执行硬门控由 Hyperion validate_patch(非 LLM)。收敛靠每 delegate call 的 steps +
单 schema + max-loop 兜底(不重蹈 glm-5.2 单 loop 97K 不收敛)。
opencode 定位/修复时经 MCP 调 Hyperion 工具(search_codebase/recall/filter_logs,见 bug-rca-design.md §6)。
LangGraph StateGraph;run() 是入口(CLI / 测试调)。
"""
from __future__ import annotations

from pathlib import Path

from langgraph.graph import END, START, StateGraph

from hyperion.workflows.bug_rca.nodes import (
    node_assemble_repair,
    node_delegate_localize_loop,
    node_delegate_repair_loop,
    node_ingest,
    node_recall_lessons,
    node_report_memorize,
)
from hyperion.workflows.bug_rca.state import BugRcaState


def build_graph():
    """构建并编译 bug-RCA verify-refine StateGraph(返回 CompiledStateGraph)。"""
    g = StateGraph(BugRcaState)
    g.add_node("ingest", node_ingest)
    # 1.5 确定性 recall 预注入(②[b]):历史同类教训预进 localize prompt(0 决策 turn 先验)
    g.add_node("recall_lessons", node_recall_lessons)
    # 阶段① 定位:opencode 自主定位(调 MCP 工具)+ verify-refine 循环
    g.add_node("delegate_localize_loop", node_delegate_localize_loop)
    # 阶段② 修复:opencode edit code/ + git diff 观察 + validate_patch 门控 + verify-refine 循环
    g.add_node("assemble_repair", node_assemble_repair)
    g.add_node("delegate_repair_loop", node_delegate_repair_loop)
    g.add_node("report_memorize", node_report_memorize)
    # 线性(even if 未过 gate 也继续出报告 + 记 lesson,故无分支)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "recall_lessons")
    g.add_edge("recall_lessons", "delegate_localize_loop")
    g.add_edge("delegate_localize_loop", "assemble_repair")
    g.add_edge("assemble_repair", "delegate_repair_loop")
    g.add_edge("delegate_repair_loop", "report_memorize")
    g.add_edge("report_memorize", END)
    return g.compile()


async def run(repo_root: str, trigger: str | None, log_path: str | None = None) -> dict:
    """跑完整 bug-RCA verify-refine workflow,返回最终 state(含 report_path / patch_path / lesson)。

    repo_root:仓库根。
    trigger  :bug 线索(日志摘要 / 问题描述 / 漏洞报告关键句);纯日志驱动时可空。
    log_path :原始日志文件路径(可选;喂 opencode 的 filter_logs MCP 工具)。
    """
    graph = build_graph()
    # resolve 绝对路径:repo_root 给 workspace 拷码、log_path 给 MCP server 的 filter_logs 读。
    # 后者尤其要绝对 —— MCP server 的 cwd 是 workspace(≠ Hyperion 根),相对 log_path 会找不到。
    initial: BugRcaState = {
        "repo_root": str(Path(repo_root).resolve()) if repo_root else repo_root,
        "trigger": trigger or "",
        "log_path": str(Path(log_path).resolve()) if log_path else "",
    }
    return await graph.ainvoke(initial)
