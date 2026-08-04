"""bug-RCA 中文报告渲染(R3.5 #46 精修:8 段去重结构)。

按 demo 金标准骨架渲染中文报告(bug-rca-design.md §5),但比手写旗舰版紧凑(自动生成,不堆砌)。
证据纪律是签名:每条结论锚 file:line、补丁 unified diff、数字从结构化聚合来(不让 LLM 凭空报)。

**去重规则(规避 AI 味)**:代码 snippet 只在「四、关键证据」出现一次;「三、定位定界」只列
文件/范围(file 级,无 snippet/无 line);「二、根因分析」触发链内嵌 file:line 是因果叙事(非
表格重复)→ 同一 file:line 不重复罗列。「附录·代码锚点速查表」是 file:line→含义 的一行速查。

输入:workflow state —— **单一数据源**,直读 state(不再像旧版把字段 mutate 进 delegate_result):
  - localize 侧(_loc):root_cause/problem_summary/impact/trigger_chain/evidence/blast_radius_files/
    scope_notes/log_evidence/falsification
  - repair 侧(_repair_data):patch_rationale/next_steps/confidence
  - 直取:patch(git diff 观察)/ verified / validate_log / verdict_chain / localize_loops/repair_loops /
    trigger / log_path / repo_root
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from hyperion.workflows.bug_rca.nodes import _coerce_evidence_line
from hyperion.workflows.bug_rca.state import BugRcaState

# 补丁 diff 超过 _TRUNCATE_THRESHOLD 行 → 报告里只展示前 _PREVIEW_LINES 行 + 文件级清单,
# 全量见配套 .patch 文件(防几百行补丁把报告撑到上千行;金标也只在正文展示关键 hunk)。
_PATCH_PREVIEW_LINES = 50
_PATCH_TRUNCATE_THRESHOLD = 200


def _loc(state: BugRcaState) -> dict:
    """阶段① 定位产出(root_cause/evidence/trigger_chain/...)。None/非 dict → {}。"""
    loc = state.get("localization_json")
    return loc if isinstance(loc, dict) else {}


def _repair_data(state: BugRcaState) -> dict:
    """阶段② 修复 delegate 的结构化产出(patch_rationale/next_steps/confidence/...)。"""
    res = state.get("delegate_result")
    data = getattr(res, "data", None)
    return data if isinstance(data, dict) else {}


def _dedup(seq: list[str]) -> list[str]:
    """去重保序(给文件清单用)。"""
    seen: set[str] = set()
    out: list[str] = []
    for s in seq:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _patch_changed_files(patch: str) -> list[str]:
    """从 unified diff 抽被改文件(`+++ b/path`)→ 去重清单(定位定界/锚点表用)。"""
    files: list[str] = []
    for line in patch.splitlines():
        if line.startswith("+++ b/"):
            files.append(line[6:].split("\t")[0])
        elif line.startswith("+++ ") and line.strip() != "+++ /dev/null":
            files.append(line[4:].split("\t")[0])  # 兜底:个别 diff 无 b/ 前缀
    return _dedup(files)


def _cell(s: str, limit: int = 80) -> str:
    """markdown 表格单元格安全化:转义 |、压空白、截断(防表格被换行/竖线撑坏)。"""
    return (str(s or "")).replace("|", "\\|").replace("\n", " ").strip()[:limit]


def render_report(state: BugRcaState) -> str:
    """渲染中文 bug-RCA 报告(markdown,8 段去重 + 附录)。

    单一数据源 state。每段缺字段优雅降级(短占位,不堆"(未提供)"海洋)。METR 警示前置到 TL;DR。
    """
    loc = _loc(state)
    repair = _repair_data(state)
    repo_name = Path(state.get("repo_root", ".")).name
    trigger = state.get("trigger", "")
    log_path = state.get("log_path")
    patch = state.get("patch", "")
    verified = bool(state.get("verified", False))
    validate_log = state.get("validate_log", "")
    vchain = state.get("verdict_chain") or []
    loc_loops = state.get("localize_loops")
    rep_loops = state.get("repair_loops")
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    res = state.get("delegate_result")
    tokens = getattr(res, "tokens", {}) or {}
    delegate_status = getattr(res, "status", "-")

    # —— localize 侧字段 ——
    root_cause = loc.get("root_cause", "") or "(未给出)"
    problem_summary = loc.get("problem_summary", "")
    impact = loc.get("impact", "")
    trigger_chain = loc.get("trigger_chain", [])
    evidence = loc.get("evidence", [])
    blast = loc.get("blast_radius_files", [])
    scope_notes = loc.get("scope_notes", "")
    log_evidence = loc.get("log_evidence", [])
    loc_falsification = loc.get("falsification", "")
    # —— repair 侧字段 ——
    patch_rationale = repair.get("patch_rationale", "")
    next_steps = repair.get("next_steps", []) or []
    confidence = repair.get("confidence")

    changed_files = _patch_changed_files(patch)
    locus_files = _dedup([
        e.get("file") for e in evidence if isinstance(e, dict) and e.get("file")
    ])

    parts: list[str] = []

    # ════════ 标题 + 元数据表 ════════
    parts.append(f"# bug 根因分析报告 — {repo_name}\n")
    parts.append("| 项 | 内容 |")
    parts.append("|---|---|")
    parts.append(f"| 报告时间 | {now} |")
    parts.append(f"| 分析对象 | {repo_name} |")
    parts.append(f"| 问题来源 | {('日志驱动: ' + str(log_path)) if log_path else '问题描述(issue/线索)'} |")
    parts.append(f"| 委托状态 | {delegate_status} |")
    if tokens:
        parts.append(f"| token 用量 | {tokens.get('total', '-')} |")
    if loc_loops is not None or rep_loops is not None:
        parts.append(f"| verify-refine | 定位 {loc_loops or 0} 轮 / 修复 {rep_loops or 0} 轮 |")
    parts.append(f"| 自动验证 | {'通过(validate_patch + 自审)' if verified else '未通过/未验'} |")
    parts.append("")

    # ════════ TL;DR(METR 警示前置)════════
    parts.append("## 执行摘要(TL;DR)\n")
    parts.append(f"- **根因**:{root_cause}")
    if problem_summary:
        parts.append(f"- **现象**:{problem_summary}")
    if impact:
        parts.append(f"- **影响**:{impact}")
    if confidence is not None:
        parts.append(f"- **修复置信度(自评)**:{confidence}")
    if next_steps:
        head = next_steps[0]
        parts.append(f"- **首要建议**:{head}" + (f"(共 {len(next_steps)} 条,详见七)" if len(next_steps) > 1 else ""))
    parts.append(
        "- ⚠️ **准确率警示**:本报告验证 = `validate_patch`(apply-check)+ delegate 证伪式自审,"
        "**非** 复现级验证(约半数 test-passing PR 不会被合,METR);补丁须人工 / 日志 repro 终审。"
    )
    parts.append("")

    # ════════ 一、问题描述 ════════
    parts.append("## 一、问题描述\n")
    if problem_summary:
        parts.append(f"**现象**:{problem_summary}\n")
    else:  # 降级:用 trigger 前 300 字
        preview = (trigger or "").strip()[:300] or "(未提供问题描述)"
        parts.append(f"**现象**:\n```\n{preview}\n```\n")
    if impact:
        parts.append(f"**影响**:{impact}\n")
    parts.append("")

    # ════════ 二、根因分析(触发链内嵌 file:line 是叙事,非表格重复)════════
    parts.append("## 二、根因分析\n")
    parts.append(f"**根因**:{root_cause}\n")
    if trigger_chain:
        parts.append("**触发链**:\n")
        for i, step in enumerate(trigger_chain, 1):
            parts.append(f"{i}. {step}")
        parts.append("")
    if loc_falsification:
        parts.append(f"**自审(证伪式)**:{loc_falsification}\n")
    parts.append("")

    # ════════ 三、定位定界(file 级:落点/影响半径/补丁改动 in-scope;无 snippet/无 line)════════
    parts.append("## 三、定位定界\n")
    if scope_notes:
        parts.append(f"{scope_notes}\n")
    parts.append("| 类别 | 文件 |")
    parts.append("|---|---|")
    parts.append(f"| 根因落点 | {', '.join(locus_files) or '(阶段①未给)'} |")
    parts.append(f"| 影响半径 | {', '.join(blast) or '(未给)'} |")
    parts.append(f"| 补丁改动(in-scope) | {', '.join(changed_files) or '(未生成补丁)'} |")
    parts.append("")

    # ════════ 四、关键证据(snippet 只在此段出现)════════
    parts.append("## 四、关键证据\n")
    if evidence:
        parts.append("### 4.1 代码证据\n")
        parts.append("| 文件 | 行 | 片段 | 说明 |")
        parts.append("|---|---|---|---|")
        for e in evidence:
            if not isinstance(e, dict):
                continue
            ln = _coerce_evidence_line(e.get("line"))
            parts.append(
                f"| {_cell(e.get('file', '-'), 50)} | {ln if ln else '-'} | "
                f"{_cell(e.get('snippet'), 80)} | {_cell(e.get('why'), 60)} |"
            )
        parts.append("")
    else:
        parts.append("### 4.1 代码证据\n(阶段①未提供代码证据)\n")
    if log_evidence:
        parts.append("### 4.2 日志证据\n")
        parts.append("| 行 | 事件 | 说明 |")
        parts.append("|---|---|---|")
        for le in log_evidence:
            if not isinstance(le, dict):
                continue
            ln = _coerce_evidence_line(le.get("line"))
            parts.append(
                f"| {ln if ln else '-'} | {_cell(le.get('event'), 50)} | {_cell(le.get('note'), 60)} |"
            )
        parts.append("")
    parts.append("")

    # ════════ 五、补丁说明(>200 行截断)════════
    parts.append("## 五、补丁说明\n")
    if patch_rationale:
        parts.append(f"**补丁论证**:{patch_rationale}\n")
    if patch:
        patch_lines = patch.splitlines()
        if len(patch_lines) > _PATCH_TRUNCATE_THRESHOLD:
            preview = "\n".join(patch_lines[:_PATCH_PREVIEW_LINES])
            parts.append(
                f"**补丁**(共 {len(patch_lines)} 行,此处展示前 {_PATCH_PREVIEW_LINES} 行;"
                f"全量见配套 `.patch` 文件):\n"
            )
            parts.append(f"```diff\n{preview}\n```\n")
        else:
            parts.append("**补丁**:\n")
            parts.append(f"```diff\n{patch}\n```\n")
    else:
        parts.append("**补丁**:(未生成)\n")
    if validate_log:
        parts.append(f"**apply/revert 门控**:{validate_log.strip()[:200]}\n")
    parts.append("")

    # ════════ 六、验证与过程(verdict 链本身 + METR)════════
    parts.append("## 六、验证与过程\n")
    parts.append(
        f"- **自动验证(validate_patch Tier0 + 自审)**:{'通过' if verified else '未通过/未验'}"
    )
    if confidence is not None:
        parts.append(f"- **修复置信度(自评)**:{confidence}")
    if vchain:
        parts.append(f"- **verify-refine verdict 链**:{' → '.join(str(v) for v in vchain)}")
    if loc_loops is not None or rep_loops is not None:
        parts.append(f"- **轮数**:定位 {loc_loops or 0} / 修复 {rep_loops or 0}")
    if validate_log:
        parts.append(f"- **门控诊断**:{validate_log.strip()[:200]}")
    parts.append(
        "- ⚠️ **准确率警示**:verify-refine 自审 + apply-check 通过 ≠ 补丁正确"
        "(约半数 test-passing PR 不会被合,METR);须人工 / 日志 repro 终审。"
    )
    parts.append("")

    # ════════ 七、下一步建议 ════════
    parts.append("## 七、下一步建议\n")
    if next_steps:
        for i, s in enumerate(next_steps[:5], 1):
            parts.append(f"{i}. {s}")
    else:  # 兜底模板(verified 分档强化)
        if verified:
            parts.append("1. 补丁通过自动验证,建议补充回归用例并人工确认根因消除。")
        else:
            parts.append("1. ⚠️ 补丁未通过自动验证,**强烈建议人工复核**;条件允许时用日志 repro 终审。")
        parts.append("2. 复核上方 falsification 所列「改完仍可能出问题」处。")
    parts.append("")

    # ════════ 附录(代码锚点速查表 + 溯源)════════
    parts.append("## 附录\n")
    parts.append("### 代码锚点速查表\n")
    if evidence:
        parts.append("| 位置 | 含义 |")
        parts.append("|---|---|")
        for e in evidence:
            if not isinstance(e, dict) or not e.get("file"):
                continue
            ln = _coerce_evidence_line(e.get("line"))
            pos = f"{e.get('file')}:{ln}" if ln else str(e.get("file"))
            meaning = _cell(e.get("why") or e.get("snippet"), 60)
            parts.append(f"| {pos} | {meaning} |")
        parts.append("")
    else:
        parts.append("(无代码锚点)\n")
    parts.append("### 生成溯源\n")
    parts.append(
        "- 本报告由 Hyperion bug-RCA workflow 生成:opencode 自主定位+修复"
        "(调 Hyperion MCP 工具 search_codebase / recall / filter_logs)+ `git diff` 观察补丁 + "
        "`validate_patch` 执行门控 + verify-refine 同会话迭代。"
    )
    parts.append("- 根因已抽成 `BugLesson` 入记忆(下次同类问题可 `recall` 命中)。")
    return "\n".join(parts)
