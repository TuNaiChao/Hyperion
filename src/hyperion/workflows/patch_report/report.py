"""patch_report · 报告渲染 + 轻量 Verifier(P-A 1b Checkpoint 4)。

render_patch_report:跨 PR 综合 + 安全告警 + 热模块 + 每 PR deep-dive + sources。
verify_and_append(MVP 轻量):每条 citation 的 file 必须在某条 PR 的 changed_files 里(防 LLM 编造文件);
  line 精确回查(对 diff hunk)留 backlog。统计 + 附录(可疑者列出)。
"""
from __future__ import annotations

from pathlib import Path


def render_patch_report(state) -> str:
    """渲染 patch-report Markdown(结构数字 + LLM cross_summary + 每 PR cited deep-dive + sources)。"""
    findings = state.get("findings") or []
    agg = state.get("aggregate") or {}
    stats = agg.get("stats") or {}
    cross = agg.get("cross_summary") or "(无跨 PR 综合)"
    codebase = state.get("codebase", "")

    # 报告层去重注记:有同主题组时标 unique subjects(底层 finding 全保留,不删)
    n_uniq = stats.get("n_unique_subjects")
    dup_n = len(stats.get("duplicate_subject_groups") or [])
    uniq_note = f" · **{n_uniq} unique subjects**({dup_n} 组同主题)" if (n_uniq is not None and dup_n) else ""

    out = [
        f"# patch-report · {codebase}",
        "",
        f"**{stats.get('total_prs', len(findings))} PRs**{uniq_note} · "
        f"theme={stats.get('by_theme', {})} · security_tier={stats.get('by_tier', {})} · "
        f"high_security={stats.get('high_security_count', 0)} · high_risk={stats.get('high_risk_count', 0)}",
        "",
        "## 跨 PR 综合",
        cross,
        "",
    ]

    hs = agg.get("high_security_prs") or []
    if hs:
        out += ["## ⚠️ 安全相关 PR(建议人工复核)", *[f"- {t}" for t in hs], ""]

    hm = stats.get("hot_modules") or []
    if hm:
        out += ["## 热模块(改动频繁)",
                *[f"- community {m['module']}: {m['pr_count']} PRs" for m in hm[:8]], ""]

    out.append("## 各 PR 鉴定")
    for f in findings:
        out += [
            f"### {f.get('title', '(无标题)')}",
            f"applies={f.get('applies')} · risk={f.get('risk_score', 0):.2f} · "
            f"tier={f.get('security_tier', '?')} · theme={f.get('theme', '?')}",
            f.get("summary", ""),
            "**证据:**",
        ]
        for c in (f.get("citations") or [])[:8]:
            out.append(f"- `{c.get('file', '?')}:{c.get('line', '?')}` {c.get('symbol', '')} — {c.get('claim', '')}")
        out.append("")

    sources = sorted({_cite_loc(c) for f in findings for c in (f.get("citations") or [])})
    out += ["## Sources", *sources]
    return "\n".join(out)


def _cite_loc(c: dict) -> str:
    return f"{c.get('file', '?')}:{c.get('line', '?')}"


def verify_and_append(report_md: str, state) -> str:
    """轻量 Verifier:每条 citation 的 file 必须在某条 PR 的 changed_files 里(防 LLM 编造文件)。

    MVP:file 级回查(basename/contains 容错);line 精确对 diff hunk 的回查留 backlog。
    追加一个 Verifier 段(通过率 + 可疑列表)。透明:总追加,不做静默删改。
    """
    findings = state.get("findings") or []
    agg = state.get("aggregate") or {}

    valid_files = set()
    for f in findings:
        valid_files.update(f.get("changed_files") or [])

    to_check: list[tuple[str, dict]] = []
    for f in findings:
        for c in (f.get("citations") or []):
            to_check.append((f.get("title", "?"), c))
    for c in (agg.get("citations") or []):  # 跨 PR 综合的引用
        to_check.append(("(cross-summary)", c))

    bad: list[tuple[str, str]] = []
    for title, c in to_check:
        f = c.get("file")
        if not f:  # 无 file 的跳过(不算坏,LLM 偶尔只给 symbol)
            continue
        base = Path(f).name
        # 容错匹配:全等 / basename 等 / valid_file 以 f 结尾(diff 路径前缀差异)
        if not any(f == vf or base == Path(vf).name or vf.endswith(f) for vf in valid_files):
            bad.append((title, f"{f}:{c.get('line', '?')}"))

    total = len(to_check)
    nbad = len(bad)
    rate = ((total - nbad) / total) if total else 1.0
    lines = ["", "## Verifier(citation file 回查)",
             f"通过率: {total - nbad}/{total} ({rate:.0%}) — file 必须在某条 PR 的 changed_files 里"]
    if bad:
        lines.append("可疑(文件不在任何 PR 改动里,可能编造):")
        for title, loc in bad[:20]:
            lines.append(f"- [{title}] {loc}")
    else:
        lines.append("✅ 所有 citation 的文件都在某条 PR 的改动里。")
    return report_md.rstrip() + "\n" + "\n".join(lines) + "\n"
