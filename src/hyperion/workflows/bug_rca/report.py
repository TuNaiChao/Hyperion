"""bug-RCA 中文报告渲染(R2 批4)。

按 demo 金标准骨架渲染中文报告(bug-rca-design.md §5)。证据纪律是签名:
每条结论锚 file:line、补丁 unified diff、置信度用数值。
输入:workflow state(anchors/trigger/recalled/verified)+ delegate 的 DelegateResult(data)。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from hyperion.tools.delegate import DelegateResult
from hyperion.workflows.bug_rca.state import BugRcaState


def render_report(state: BugRcaState, result: DelegateResult) -> str:
    """渲染中文 bug-RCA 报告(markdown)。

    state : BugRcaState(repo_root/trigger/anchors/recalled/verified)。
    result: delegate 的 DelegateResult(data 含 root_cause/evidence/trigger_chain/
             patch/confidence/blast_radius_files)。
    """
    data = result.data or {}
    repo_name = Path(state.get("repo_root", ".")).name
    trigger = state.get("trigger", "")
    anchors = state.get("anchors", [])
    verified = state.get("verified", False)
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    parts: list[str] = []

    # —— 元数据表 ——
    parts.append(f"# bug 根因分析报告 — {repo_name}\n")
    parts.append("| 项 | 内容 |")
    parts.append("|---|---|")
    parts.append(f"| 报告时间 | {now} |")
    parts.append(f"| 分析对象 | {repo_name} |")
    parts.append(f"| 委托状态 | {result.status} |")
    parts.append(f"| token 用量 | {result.tokens.get('total', '-')} |")
    parts.append(f"| 验证 | {'通过(patch 非空)' if verified else '未通过/未验'} |")
    parts.append("")

    # —— TL;DR ——
    root_cause = data.get("root_cause", "(未给出)")
    parts.append("## 一、执行摘要(TL;DR)\n")
    parts.append(f"- **根因**:{root_cause}")
    confidence = data.get("confidence")
    if confidence is not None:
        parts.append(f"- **置信度**:{confidence}")
    blast = data.get("blast_radius_files", [])
    if blast:
        parts.append(f"- **影响范围**:{', '.join(blast)}")
    parts.append("")

    # —— Bug 线索 ——
    parts.append("## 二、Bug 线索\n")
    parts.append(f"```\n{trigger}\n```\n")

    # —— 定位与根因 ——
    parts.append("## 三、定位与根因\n")
    chain = data.get("trigger_chain", [])
    if chain:
        parts.append("### 触发链(trigger chain)")
        for i, step in enumerate(chain, 1):
            parts.append(f"{i}. {step}")
        parts.append("")

    ev = data.get("evidence", [])
    if ev:
        parts.append("### 证据(evidence)")
        parts.append("| 文件 | 行 | 片段 |")
        parts.append("|---|---|---|")
        for e in ev:
            if isinstance(e, dict):
                snippet = (e.get("snippet") or "").strip().replace("|", "\\|")[:80]
                parts.append(f"| {e.get('file', '-')} | {e.get('line', '-')} | {snippet} |")
        parts.append("")

    # —— 漏斗锚点(附录性,证明定位过程)——
    if anchors:
        parts.append("### 定位漏斗锚点(Agentless file→function→line)")
        for a in anchors:
            fn = getattr(a, "function", None) or "-"
            parts.append(f"- `{getattr(a, 'file', '?')}:{getattr(a, 'line', '?')}` {fn}  ({getattr(a, 'why', '')})")
        parts.append("")

    # —— 补丁 ——
    patch = data.get("patch", "")
    parts.append("## 四、补丁\n")
    parts.append(f"```diff\n{patch or '(未生成)'}\n```\n")

    # —— 附录 ——
    parts.append("## 五、附录\n")
    parts.append("- 本报告由 Hyperion bug-RCA workflow 生成(定位漏斗 + delegate 委托 + 记忆召回)。")
    parts.append("- 补丁见配套 `.patch` 文件;根因已抽成 `BugLesson` 入记忆(下次同类问题可 recall 命中)。")
    return "\n".join(parts)
