"""bug-RCA workflow 状态 schema(R2 批4;R3.1 砍漏斗精简)。

六步节点间传递的状态(踩坑 #2,2026-07-31:砍旧 recall/localize/assemble_localize 三节点 →
opencode 自主定位 + MCP 工具;R3 收尾 ②[b] 加回 recall_lessons 预注入节点,只翻记忆不定位)。
TypedDict:repo_root/trigger 入口必传(Required);
其余是各步产物(NotRequired)——前序节点填,后序节点读。
"""

from __future__ import annotations

from typing import Any, NotRequired, Required, TypedDict


class BugRcaState(TypedDict, total=False):
    # 必需:入口传入(CLI `hyperion bug-rca` 时给)
    repo_root: Required[str]
    trigger: Required[str]  # bug 线索(--trigger;纯日志驱动时可空串)
    log_path: NotRequired[str]  # 原始日志文件路径(可选;喂 opencode 的 filter_logs MCP 工具)
    # 可选:各步产物(前序节点产,后序节点读)
    scope: NotRequired[Any]  # 1.ingest 产:Scope(owner, codebase)
    workspace: str  # workspace 目录路径(node_ingest 建;delegate 在 workspace/code 改码)
    recalled_lessons_ctx: str  # 1.5 recall_lessons 产:历史同类教训渲染段(预进 localize prompt;②[b])
    recalled_lessons: list  # 1.5 recall_lessons 产:RecallHit 列表(observability;空则不预注入)
    prompt: str  # 3.assemble_repair 产:修复委托提示词
    output_schema: dict  # 3.assemble_repair 产:委托产出契约
    patch: str  # git diff 观察出的补丁(node_delegate_repair_loop 选定后写,非 delegate 吐)
    delegate_result: NotRequired[Any]  # 4.delegate_repair_loop 产:DelegateResult
    verified: NotRequired[bool]  # 4.delegate_repair_loop 产(validate_patch + verdict 双门控)
    report_path: str  # 5.report 产
    patch_path: str  # 5.report 产
    # R3.1 #54-rework B:迭代 verify-refine 双循环(替旧「多候选采样投票」)
    localize_loops: int  # localize loop 实际跑了几轮(node_delegate_localize_loop 写)
    repair_loops: int  # repair loop 实际跑了几轮(node_delegate_repair_loop 写)
    validate_log: str  # 末次 validate_patch 诊断(供 revisit prompt + report)
    verdict_chain: list  # 每轮 verdict 记录(供 report 显示 verify-refine 过程)
    localize_revisit_prompt: str  # localize 重定位 prompt(带 falsification 反馈;loop 内组装)
    repair_revisit_prompt: str  # repair 重修 prompt(带 validate_log 反馈;loop 内组装)
    # 多阶段委托阶段间传递
    delegate_localize_result: object  # 阶段① delegate 回执
    localization_json: dict  # ★ 阶段① 产的 root_cause/evidence(喂阶段②)
