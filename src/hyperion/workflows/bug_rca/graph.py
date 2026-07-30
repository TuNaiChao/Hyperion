"""bug-RCA workflow StateGraph 组装(多阶段委托,R2 收尾)。

九节点线性流水线:
  ingest → recall → localize → assemble_localize → delegate_localize
    → assemble_repair → delegate_repair → verify → report_memorize

多阶段委托(2026-07-30,对标 bug-rca-design.md §7.5):delegate 拆两阶段 ——
① localize_delegate 只定位 root_cause/evidence(禁补丁);
② repair_delegate 根因已锁、只改局部、产 patch。
解 glm-5.2 单 loop 不收敛(97K token 全工具,最后 prose 无 JSON)。依据 Agentless 32%/$0.70
vs SWE-agent 18.3%/$2.53(分阶段又便宜又稳)+ 消融 skeleton>整文件(lost-in-the-middle)。
LangGraph StateGraph;run() 是入口(CLI / 测试调)。R2 线性(多轮/条件分支留 R5)。
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from hyperion.workflows.bug_rca.nodes import (
    node_assemble_localize,
    node_assemble_repair,
    node_delegate_localize,
    node_delegate_repair,
    node_ingest,
    node_localize,
    node_recall,
    node_report_memorize,
    node_verify,
)
from hyperion.workflows.bug_rca.state import BugRcaState


def build_graph():
    """构建并编译 bug-RCA 多阶段 StateGraph(返回 CompiledStateGraph)。"""
    g = StateGraph(BugRcaState)
    g.add_node("ingest", node_ingest)
    g.add_node("recall", node_recall)
    g.add_node("localize", node_localize)
    # 阶段① 定位
    g.add_node("assemble_localize", node_assemble_localize)
    g.add_node("delegate_localize", node_delegate_localize)
    # 阶段② 修复
    g.add_node("assemble_repair", node_assemble_repair)
    g.add_node("delegate_repair", node_delegate_repair)
    g.add_node("verify", node_verify)
    g.add_node("report_memorize", node_report_memorize)
    # 线性九步(verify 即使失败也继续出报告+记 lesson,故无分支)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "recall")
    g.add_edge("recall", "localize")
    g.add_edge("localize", "assemble_localize")
    g.add_edge("assemble_localize", "delegate_localize")
    g.add_edge("delegate_localize", "assemble_repair")
    g.add_edge("assemble_repair", "delegate_repair")
    g.add_edge("delegate_repair", "verify")
    g.add_edge("verify", "report_memorize")
    g.add_edge("report_memorize", END)
    return g.compile()


async def run(repo_root: str, trigger: str) -> dict:
    """跑完整 bug-RCA 多阶段 workflow,返回最终 state(含 report_path / patch_path / lesson)。

    repo_root:仓库根。
    trigger  :bug 线索(日志摘要 / 问题描述 / 漏洞报告关键句)。
    """
    graph = build_graph()
    initial: BugRcaState = {"repo_root": repo_root, "trigger": trigger}
    return await graph.ainvoke(initial)
