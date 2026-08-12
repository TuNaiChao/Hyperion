"""patch_report workflow 状态 schema(P-A 1b)。

节点间传递的状态。TypedDict:repo_root / codebase / prs 入口必传(Required);其余是各步产物。
镜像 deep_research/state.py 的结构(ModuleFinding → PRFinding)。
"""

from __future__ import annotations

from typing import Any, NotRequired, Required, TypedDict


class PRFinding(TypedDict, total=False):
    """单条 PR 的分析产出(node_analyze 子任务产,带 file:line 引用 + 风险/主题/模块)。"""

    url: str
    title: str
    applies: bool  # validate_patch 结果(能否干净 apply)
    risk_score: float  # CRG analyze_changes 的 overall risk_score(0..1)
    security_tier: str  # "none" | "relevant" | "high"(theme=security 至少 relevant;叠 CRG 安全词 + risk)
    theme: str  # 8 类(LLM 判):security|bugfix|feature|config|deps|refactor|perf|other(见 _analyze.THEMES)
    modules: list  # 改动函数所属 community_id 列表(按 module 分桶用)
    changed_files: list  # PR 改动文件(verify + render + 归因用)
    summary: str  # cited:该 PR 干了啥 / 风险(每条结论锚 file:line)
    citations: list[dict]  # [{file, line, symbol, claim}, ...]


class PatchReportState(TypedDict, total=False):
    # 必需:入口传入(CLI `hyperion patch-report` 给)
    repo_root: Required[str]  # 代码仓根(CRG 图 + validate_patch + verify 都用它)
    codebase: Required[str]  # 仓名(CRG db 目录 / 记忆 scope.codebase)
    prs: Required[list[str]]  # PR URL 列表
    owner: NotRequired[str]  # 记忆 scope.owner(默认 "default")
    deep: NotRequired[bool]  # 高风险/security 子集走 ReAct 深审(默认 light)
    concurrency: NotRequired[int]  # 并发(默认 3,GitHub 限速友好)
    # 各步产物
    scope: Any  # ingest 产:Scope(owner, codebase)
    workdir: str  # ingest 产:data/patch_report/<codebase>__<ts>/
    artifacts: list  # fetch 产:list[PatchArtifact]
    findings: list[PRFinding]  # analyze 产:每 PR 的 cited finding
    aggregate: dict  # aggregate 产(Checkpoint 4):分桶 + 跨 PR 统计
    report_path: str  # report 产
    facts_memorized: int  # memorize 产(Checkpoint 4)
