"""bug-RCA workflow 状态 schema(R2 批4)。

七步节点间传递的状态。TypedDict:repo_root/trigger 入口必传(Required);
其余是各步产物(NotRequired)——前序节点填,后序节点读。
"""

from __future__ import annotations

from typing import Any, NotRequired, Required, TypedDict


class BugRcaState(TypedDict, total=False):
    # 必需:入口传入(CLI `hyperion bug-rca` 时给)
    repo_root: Required[str]
    trigger: Required[str]
    # 可选:各步产物(前序节点产,后序节点读)
    scope: NotRequired[Any]  # 1.ingest 产:Scope(owner, codebase)
    recalled: NotRequired[list]  # 2.recall 产:RecallHit[]
    anchors: NotRequired[list]  # 3.localize 产:LocAnchor[]
    prompt: NotRequired[str]  # 4.assemble 产:委托提示词
    output_schema: NotRequired[dict]  # 4.assemble 产:委托产出契约
    delegate_result: NotRequired[Any]  # 5.delegate 产:DelegateResult
    verified: NotRequired[bool]  # 6.verify 产
    report_path: NotRequired[str]  # 7.report 产
    patch_path: NotRequired[str]  # 7.report 产
    workspace: str                # workspace 目录路径(node_ingest 建;delegate 在 workspace/code 改码)
    patch: str                    # git diff 观察出的补丁(node_delegate_repair_loop 选定后写,非 delegate 吐)
    # R3.1 #54-rework B:迭代 verify-refine 双循环(替旧「多候选采样投票」)
    localize_loops: int           # localize loop 实际跑了几轮(node_delegate_localize_loop 写)
    repair_loops: int             # repair loop 实际跑了几轮(node_delegate_repair_loop 写)
    validate_log: str             # 末次 validate_patch 诊断(供 revisit prompt + report)
    verdict_chain: list           # 每轮 verdict 记录(供 report 显示 verify-refine 过程)
    localize_revisit_prompt: str  # localize 重定位 prompt(带 falsification 反馈;loop 内组装)
    repair_revisit_prompt: str    # repair 重修 prompt(带 validate_log 反馈;loop 内组装)
    # rerank 兜底(默认关;delegate.rerank.enabled=true 且 loop 耗尽才 fan-out)
    candidates: list              # rerank fan-out 的 Candidate[](仅 enabled 时填)
    rerank_summary: dict          # majority_vote 摘要(仅 enabled 时;含 METR 警示)
    # 多阶段委托(R2 收尾)阶段间传递
    localize_prompt: str           # 阶段① 定位 prompt(node_assemble_localize 产)
    localize_schema: dict          # 阶段① 定位 schema
    delegate_localize_result: object  # 阶段① delegate 回执
    localization_json: dict        # ★ 阶段① 产的 root_cause/evidence(喂阶段②)

