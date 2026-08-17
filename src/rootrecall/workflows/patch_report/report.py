"""patch_report · 报告渲染 + 轻量 Verifier(P-A 1b Checkpoint 4)。

render_patch_report:跨 PR 综合 + 安全告警 + 热模块 + 每 PR deep-dive + sources。
verify_and_append:每条 citation 的 file 必须在某条 PR 的 changed_files 里(硬,防 LLM 编造文件);
  另做行锚定软查 —— line 是否落在 diff hunk 改动区间(软,防引错行;合法引上下文也可能未锚定,故只提示不删)。
"""
from __future__ import annotations

import re
from pathlib import Path


def render_patch_report(state) -> str:
    """渲染 patch-report Markdown(结构数字 + LLM cross_summary + 发展趋势 + 每 PR deep-dive + sources)。"""
    from rootrecall.workflows.patch_report._analyze import THEME_LABELS

    findings = state.get("findings") or []
    agg = state.get("aggregate") or {}
    stats = agg.get("stats") or {}
    cross = agg.get("cross_summary") or "(无跨 PR 综合)"
    trend = agg.get("trend") or ""
    codebase = state.get("codebase", "")

    # 报告层去重注记:有同主题组时标 unique subjects(底层 finding 全保留,不删)
    n_uniq = stats.get("n_unique_subjects")
    dup_n = len(stats.get("duplicate_subject_groups") or [])
    uniq_note = f" · **{n_uniq} unique subjects**({dup_n} 组同主题)" if (n_uniq is not None and dup_n) else ""

    # theme 分布用中文标签(比裸 dict {'security':2} 易读)。
    by_theme = stats.get("by_theme") or {}
    theme_pic = ", ".join(
        f"{THEME_LABELS.get(t, t)}×{n}" for t, n in
        sorted(by_theme.items(), key=lambda kv: -kv[1])
    ) or "(无)"

    out = [
        f"# patch-report · {codebase}",
        "",
        f"**{stats.get('total_prs', len(findings))} PRs**{uniq_note} · "
        f"分类: {theme_pic} · security_tier={stats.get('by_tier', {})} · "
        f"high_security={stats.get('high_security_count', 0)} · high_risk={stats.get('high_risk_count', 0)}",
        "",
        "## 跨 PR 综合",
        cross,
        "",
    ]

    if trend:
        out += ["## 发展趋势", trend, ""]

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
    from rootrecall.services.patch.fetcher import diff_hunk_lines

    findings = state.get("findings") or []
    agg = state.get("aggregate") or {}

    valid_files = set()
    for f in findings:
        valid_files.update(f.get("changed_files") or [])

    # .patch 穿透(deepin 打包 PR 特有):PR 层只改 debian/patches/*.patch,但这些 .patch 文件
    # 内嵌了上游 C 代码的 unified diff(`+++ b/obexd/client/pbap.c`)。LLM 正确引用的是内嵌的 C 目标
    # 文件(语义准),却被字面硬查「file 必须在 changed_files」误报成编造。这里把 .patch 内嵌的目标
    # 文件也解进 valid_files,让 Verifier 认它们是合法引用(穿透 debian 打包层看真实改动)。
    embedded = set()
    for art in (state.get("artifacts") or []):
        diff = getattr(art, "diff", "") or ""
        if diff:
            embedded.update(_patch_embedded_files(diff))
    valid_files |= embedded

    # 行锚定:从 artifacts 的原始 diff 解析每个文件的改动行区间(汇总所有 PR 的 hunk)。
    # .patch 穿透:内嵌 diff 的 hunk 区间也算进来(否则 C 文件虽 file 通过但解不到 hunk,行锚定率失真)。
    hunks: dict[str, list[tuple[int, int]]] = {}
    for art in (state.get("artifacts") or []):
        diff = getattr(art, "diff", "") or ""
        if diff:
            for fh, ranges in diff_hunk_lines(diff).items():
                hunks.setdefault(fh, []).extend(ranges)
            # 嵌套:debian/patches/*.patch 内的 hunk(diff 里的 diff)。
            for fh, ranges in _patch_embedded_hunks(diff).items():
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
        # file 级硬查(容错:全等 / basename / 双向 endswith 匹配,扛 diff 路径前缀差异)。
        # 双向 endswith:citation 带前缀(valid 无)→ vf.endswith(f) 覆盖;
        #               valid 带前缀(citation 无)→ f.endswith(vf) 覆盖(原版漏了这个方向)。
        if not any(f == vf or base == Path(vf).name or vf.endswith(f) or f.endswith(vf) for vf in valid_files):
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


# ── .patch 穿透(deepin 打包 PR 特有)───────────────────────────────────────────

# .patch 文件的内嵌 diff 也用 unified diff 头:`+++ b/<path>`(或 `+++ <path>`)。
# 一条 PR diff 里 `+++ ` 行有两类:① PR 自己改的文件 ② debian/patches/*.patch 内容里的嵌套 `+++ `。
# fetcher._diff_changed_files 已经抽了①;这里要的是②(即 .patch 文本里出现的上游 C 目标文件)。
_EMBEDDED_NEW_FILE_RE = re.compile(r"^\+\+\+ b?/?(?P<f>\S+)")


def _patch_embedded_files(diff_text: str) -> set[str]:
    """从 PR diff 里解出 `debian/patches/*.patch` 文件内嵌的上游目标文件。

    deepin 打包流程:PR 改的是 debian/patches/*.patch,但这些 .patch 里嵌了上游 C 代码的
    unified diff(改 obexd/client/pbap.c 等)。LLM 正确引用的是内嵌的 C 目标,不是 .patch 文件名。
    本函数把 .patch 文件内部的 hunk(被 PR diff 以 `+diff --git`/`++++ b/x.c` 前缀嵌进来)解出来,
    让 Verifier 认这些 C 文件是合法引用。
    """
    out: set[str] = set()
    in_patch_file = False  # 当前是否在遍历一个 *.patch 文件的内容行
    for line in (diff_text or "").splitlines():
        # PR diff 自己的 +++ 行(目标文件头):若是 *.patch,标记进入其内容区;否则离开。
        if line.startswith("+++ "):
            rest = line[4:].strip().split("\t")[0]
            if rest.startswith("b/"):
                rest = rest[2:]
            in_patch_file = bool(rest) and rest.endswith(".patch")
            continue
        # 在 .patch 内容区里,被 `+` 前缀嵌进来的嵌套 diff 行(如 `++++ b/obexd/client/pbap.c`)。
        # .patch 内容在 PR diff 里每行带一个 `+`(added 行),剥掉它再看是否是嵌套 `+++ ` 文件头。
        if in_patch_file and line.startswith("+"):
            inner = line[1:]  # 剥 PR diff 的 added 前缀
            if inner.startswith("+++ "):
                m = _EMBEDDED_NEW_FILE_RE.match(inner)
                if m and m.group("f") != "/dev/null":
                    out.add(m.group("f"))
    return out


def _patch_embedded_hunks(diff_text: str) -> dict[str, list[tuple[int, int]]]:
    """从 .patch 内嵌 diff 解 hunk 行区间(供行锚定软查;与上方同穿透逻辑,但解 `@@ ` 行)。

    `.patch` 内容行带 `+` 前缀;内嵌的 `@@ -a,b +c,d @@` 在 PR diff 里呈 `+@@ ...`。剥前缀取新文件侧区间。
    """
    out: dict[str, list[tuple[int, int]]] = {}
    in_patch_file = False
    cur: str | None = None
    for line in (diff_text or "").splitlines():
        if line.startswith("+++ ") and not in_patch_file:
            rest = line[4:].strip().split("\t")[0]
            if rest.startswith("b/"):
                rest = rest[2:]
            in_patch_file = bool(rest) and rest.endswith(".patch")
            cur = None
            continue
        if not in_patch_file:
            continue
        if not line.startswith("+"):
            continue
        inner = line[1:]
        if inner.startswith("+++ "):
            m = _EMBEDDED_NEW_FILE_RE.match(inner)
            cur = m.group("f") if (m and m.group("f") != "/dev/null") else None
        elif inner.startswith("@@ ") and cur is not None:
            mm = re.match(r"^@@ -\d+(?:,\d+)? \+(?P<s>\d+)(?:,(?P<l>\d+))? @@", inner)
            if mm:
                start = int(mm.group("s"))
                length = int(mm.group("l")) if mm.group("l") else 1
                if length > 0:
                    out.setdefault(cur, []).append((start, start + length - 1))
    return out
