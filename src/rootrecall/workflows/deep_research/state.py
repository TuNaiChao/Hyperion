"""deep_research workflow 状态 schema(R3.2)。

六步节点间传递的状态。TypedDict:repo_root / codebase 入口必传(Required);
其余是各步产物(NotRequired)—— 前序节点填,后序节点读。
"""

from __future__ import annotations

from typing import Any, NotRequired, Required, TypedDict


class ModulePlan(TypedDict, total=False):
    """单个模块的调研计划(node_plan 产,node_research 消费)。"""

    name: str  # 模块名(CRG 社区名 / LLM 命名)
    focus: str  # 该模块要回答的问题 / 调研重点(storm 式多视角发问)
    member_files: list[str]  # 该社区覆盖的文件(CRG 给)
    key_symbols: list[str]  # 该社区的 hub 节点(CRG find_hub_nodes 给)


class ModuleFinding(TypedDict, total=False):
    """单个模块的调研产出(node_research 子 agent 产,带 file:line 引用)。"""

    module: str
    summary: str  # 该模块职责 / 关键设计(每条结论锚 file:line)
    citations: list[dict]  # [{file, line, symbol, claim}, ...] 每条断言的证据(cited-reporter)


class DeepResearchState(TypedDict, total=False):
    # 必需:入口传入(CLI `rootrecall research` 给)
    repo_root: Required[str]
    codebase: Required[str]
    owner: NotRequired[str]  # 记忆 scope.owner(默认 "default")
    # 各步产物
    workdir: str  # 1.ingest 建:data/research/<codebase>__<ts>/(放报告 + structgraph db)
    scope: Any  # 1.ingest 产:Scope(owner, codebase)
    index_built: bool  # 2.index 完成标志
    codegraph_stats: dict  # 2.index 产:CRG 图统计(节点/边/社区数,给报告元数据)
    architecture_overview: dict  # 2.index 产:CRG architecture_overview(报告「系统架构」数据源)
    plan: list[ModulePlan]  # 3.plan 产:模块清单(社区边界)
    findings: list[ModuleFinding]  # 4.research 产:每模块 cited findings
    report_path: str  # 5.report 产
    facts_memorized: int  # 6.memorize 产:入了多少 CodebaseFact
