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
    workspace: str       # 方式 B:workspace 目录路径(node_ingest 建)
    patch: str           # 方式 B:git diff 观察出的补丁(node_verify 写,替代 delegate 吐的 patch)
    # 多阶段委托(R2 收尾)阶段间传递
    localize_prompt: str           # 阶段① 定位 prompt(node_assemble_localize 产)
    localize_schema: dict          # 阶段① 定位 schema
    delegate_localize_result: object  # 阶段① delegate 回执
    localization_json: dict        # ★ 阶段① 产的 root_cause/evidence(喂阶段②)

