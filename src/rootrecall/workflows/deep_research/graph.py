"""deep_research workflow StateGraph 组装(R3.2,P1 代码仓深度调研)。

六节点线性流水线(镜像 bug_rca graph.py 的 StateGraph 模式):
  ingest → index → plan → research → report → memorize

  1.ingest   本地路径/git → 注册 scope + 建工作区(不改代码,轻量 data/research/<repo>__<ts>/)
  2.index    code_index 建语义索引(复用 build_index)+ CRG 建结构图(复用 CodeGraph.build)
  3.plan     CRG communities + hub_nodes → 模块清单(社区 = 自然模块边界)+ 调研焦点
  4.research 每模块一个 ReAct 子 agent(create_agent + nav 工具 + 中间件)并行深挖,
             cited-reporter:每条断言锚 file:line(source registry + emit-concept 防幻觉)
  5.report   渲染 §5 Markdown;系统架构章节 = CRG overview 图驱动;Verifier 回查 file:line 防虚假引用
  6.memorize 抽 CodebaseFact(schema 已就位)入记忆,带 commit SHA(P1→P2 闭环)

LangGraph StateGraph;run() 是入口(CLI / 测试调)。
"""

from __future__ import annotations

from pathlib import Path

from langgraph.graph import END, START, StateGraph

from rootrecall.workflows.deep_research.nodes import (
    node_index,
    node_ingest,
    node_memorize,
    node_plan,
    node_report,
    node_research,
)
from rootrecall.workflows.deep_research.state import DeepResearchState


def build_graph():
    """构建并编译 deep_research StateGraph(返回 CompiledStateGraph)。"""
    g = StateGraph(DeepResearchState)
    g.add_node("ingest", node_ingest)
    g.add_node("index", node_index)
    g.add_node("plan", node_plan)
    g.add_node("research", node_research)
    g.add_node("report", node_report)
    g.add_node("memorize", node_memorize)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "index")
    g.add_edge("index", "plan")
    g.add_edge("plan", "research")
    g.add_edge("research", "report")
    g.add_edge("report", "memorize")
    g.add_edge("memorize", END)
    return g.compile()


async def run(repo_root: str, *, codebase: str, owner: str = "default") -> dict:
    """跑完整 deep_research workflow,返回最终 state(含 report_path / facts_memorized)。

    repo_root:仓库根(会 resolve 成绝对)。
    codebase :仓库名(LanceDB 表名 / CRG db 目录名 / 记忆 scope.codebase)。
    owner    :记忆 scope.owner(默认 "default";多用户 R4 再用)。
    """
    graph = build_graph()
    initial: DeepResearchState = {
        "repo_root": str(Path(repo_root).resolve()),
        "codebase": codebase,
        "owner": owner,
    }
    return await graph.ainvoke(initial)
