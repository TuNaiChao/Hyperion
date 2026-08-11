"""patch_report · 报告渲染 + 轻量 Verifier(P-A 1b Checkpoint 4)。

render_patch_report:跨 PR 综合 + 安全告警 + 热模块 + 每 PR deep-dive + sources。
verify_and_append:每条 citation 的 file 必须在某条 PR 的 changed_files 里(硬,防 LLM 编造文件);
  另做行锚定软查 —— line 是否落在 diff hunk 改动区间(软,防引错行;合法引上下文也可能未锚定,故只提示不删)。
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
    """citation 回查:file 必须在某条 PR 的 changed_files 里(硬);line 落在 diff hunk 改动区间(软)。

    - 硬指标(file):citation.file 不在任何 PR 改动文件里 → 「可疑」(可能 LLM 编造文件)。
    - 软指标(行锚定):citation.line 落在该文件 diff hunk 改动区间 → 锚到真实改动;否则「未锚定」
      (可能引上下文 / 引错行;合法引用也可能未锚定,故只提示、不删、不算坏)。
    透明:总追加一个 Verifier 段(两率 + 可疑 / 未锚定列表),不做静默删改。
    """
    from hyperion.services.patch.fetcher import diff_hunk_lines

    findings = state.get("findings") or []
    agg = state.get("aggregate") or {}

    valid_files = set()
    for f in findings:
        valid_files.update(f.get("changed_files") or [])

    # 行锚定:从 artifacts 的原始 diff 解析每个文件的改动行区间(汇总所有 PR 的 hunk)。
    hunks: dict[str, list[tuple[int, int]]] = {}
    for art in (state.get("artifacts") or []):
        diff = getattr(art, "diff", "") or ""
        if diff:
            for fh, ranges in diff_hunk_lines(diff).items():
                hunks.setdefault(fh, []).extend(ranges)

    to_check: list[tuple[str, dict]] = []
    for f in findings:
        for c in (f.get("citations") or []):
            to_check.append((f.get("title", "?"), c))
    for c in (agg.get("citations") or []):  # 跨 PR 综合的引用
        to_check.append(("(cross-summary)", c))

    bad: list[tuple[str, str]] = []          # file 不对(硬)
    unanchored: list[tuple[str, str]] = []    # file 对但 line 不在 hunk(软)
    n_with_line = 0  # 行锚定分母:文件通过 + 有数字 line + 该文件解到 hunk 的 citation 数
    n_anchored = 0
    for title, c in to_check:
        f = c.get("file")
        if not f:  # 无 file 的跳过(不算坏,LLM 偶尔只给 symbol)
            continue
        base = Path(f).name
        # file 级硬查(容错:全等 / basename / endswith 匹配,扛 diff 路径前缀差异)。
        if not any(f == vf or base == Path(vf).name or vf.endswith(f) for vf in valid_files):
            bad.append((title, f"{f}:{c.get('line', '?')}"))
            continue
        # 行级软查:有数字 line 才查;line 没给(只给 symbol)的跳过,不计入分母。
        line = c.get("line")
        if not isinstance(line, int):
            continue
        # hunk 文件名也可能有前缀差异,同样容错匹配一次取区间。
        hf = next((h for h in hunks
                   if f == h or base == Path(h).name or h.endswith(f) or f.endswith(h)), None)
        ranges = hunks.get(hf) if hf else None
        if not ranges:
            continue  # 文件通过但没解到 hunk(纯二进制 / 元数据 / diff 缺)→ 不计入行锚定分母
        n_with_line += 1
        if any(lo <= line <= hi for lo, hi in ranges):
            n_anchored += 1
        else:
            unanchored.append((title, f"{f}:{line}"))

    total = len(to_check)
    nbad = len(bad)
    file_rate = ((total - nbad) / total) if total else 1.0
    anchor_rate = (n_anchored / n_with_line) if n_with_line else None
    lines = ["", "## Verifier(citation 回查)",
             f"file 通过率: {total - nbad}/{total} ({file_rate:.0%}) — file 必须在某条 PR 的 changed_files 里"]
    if anchor_rate is not None:
        lines.append(f"行锚定率: {n_anchored}/{n_with_line} ({anchor_rate:.0%}) — "
                     "line 落在 diff hunk 改动区间(软:引上下文也可能未锚定)")
    lines.append("")
    if bad:
        lines += ["可疑(文件不在任何 PR 改动里,可能编造):",
                  *[f"- [{t}] {loc}" for t, loc in bad[:20]]]
    if unanchored:
        lines += ["未锚定(文件对,但行号不在改动区间 —— 可能引上下文 / 引错):",
                  *[f"- [{t}] {loc}" for t, loc in unanchored[:20]]]
    if not bad and not unanchored:
        tail = "; 行号均锚到改动区间。" if n_with_line else "。"
        lines.append("✅ 所有 citation 文件在改动里" + tail)
    return report_md.rstrip() + "\n" + "\n".join(lines) + "\n"
