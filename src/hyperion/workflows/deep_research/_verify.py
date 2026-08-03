"""deep_research · Verifier 核心(R3.2,窗口展示 · 用户手敲)。被 node_report 调用。

DocAgent 式写后 fact-check:报告写完,抽每条 file:line 引用,回查文件真实存在(防 LLM 编造路径)。
虚假引用(文件不存在)→ 末尾「Verifier」章节透明列出(不删原文,让人看到哪里可疑)。
返 (报告 + Verifier 章节, 统计)。

为什么只查"文件存在":一条编造的引用最硬的破绽就是路径不存在 —— 真符号总在真文件里。
逐符号精确核验(parse_file 验 symbol@line)更严但要 per-citation 解析,留 pull-by-need。
"""

from __future__ import annotations

import re
from pathlib import Path

from hyperion.workflows.deep_research.state import DeepResearchState

# 报告里的引用形如 `相对/路径.c:123`(render_report 用反引号包 file:line)。
_CITE_RE = re.compile(r"`([^`]+\.[a-z]+):(\d+)`", re.IGNORECASE)


def _verify_report_citations(report_md: str, state: DeepResearchState) -> tuple[str, dict]:
    """回查报告里每条 file:line:文件存在 = 通过,否则末尾列「疑似幻觉」。

    返 (报告_md [末尾可能追加 Verifier 章节], stats {citations, verified, unverified, module_coverage})。
    """
    repo_root = Path(state["repo_root"])
    cites = set(_CITE_RE.findall(report_md))  # {(file, line)} 去重
    bad: list[tuple[str, str]] = []  # (file:line, 原因)
    good = 0
    for file, _line in cites:
        fp = repo_root / file
        if not fp.exists():
            bad.append((f"{file}:{_line}", "文件不存在"))
        elif not fp.is_file():
            bad.append((f"{file}:{_line}", "不是文件"))
        else:
            good += 1

    # 模块覆盖率:plan 里多少模块产出了带 citation 的 finding(粗粒度的"调研到位了吗")
    plan = state.get("plan") or []
    findings = state.get("findings") or []
    modules_with_cites = sum(1 for f in findings if f.get("citations"))
    module_coverage = round(modules_with_cites / len(plan), 2) if plan else 0.0

    stats = {
        "citations": len(cites),
        "verified": good,
        "unverified": len(bad),
        "module_coverage": module_coverage,
    }

    # Verifier 章节始终附上(透明:让读者看到"核验跑过、结果如何"),而非只在有幻觉时才出现。
    lines = [
        "",
        "## Verifier(写后回查)",
        "",
        f"- 引用总数 **{len(cites)}**(去重);通过文件存在性核验 **{good}**;疑似幻觉 **{len(bad)}**。",
        f"- 模块覆盖率(产出带 citation finding 的模块占比):**{module_coverage:.0%}**。",
    ]
    if bad:
        lines += ["", "⚠️ 以下引用的文件不存在(疑似幻觉,需人工复核):"]
        for ref, why in bad:
            lines.append(f"- `{ref}` — {why}")
    else:
        lines.append("- ✅ 所有引用均通过核验(文件真实存在)。")
    report_md = report_md.rstrip() + "\n" + "\n".join(lines) + "\n"

    return report_md, stats
