# src/rootrecall/workflows/deep_research/_verify.py
"""deep_research · Verifier 核心(R3.3.2,窗口展示 · 用户手敲)。被 node_report 调用。

干什么(面向小白)
  报告每条结论挂证据脚注 {file, line, symbol, claim}。LLM 会"编脚注"——引真文件、报假符号/假行号。
  本 Verifier 翻书核对:打开 file → 确认 symbol 真存在 → 确认 line 落在 symbol 的行区间 [start,end] 内
  (Existence@Line)。三道都过才 ✓;否则标「疑似编造」。

为什么从「regex 抠报告」改成「直接吃 state 结构化 citations」
  R3.2 的 Verifier 从渲染后的报告文本 regex 反抠 `file:line`,symbol/claim 在渲染那一刻就丢了,
  只能查"文件存在"。现在直接读 state["findings"][].citations(子 agent 产的结构化证据),
  symbol 信息保住,才能做逐符号@行核验。

调研依据(均已核验真实)
  - DocAgent(arXiv 2504.08725):Existence Ratio = |已验|/|抽取|;但它只查 entity 在不在图,
    不查 line 落不落在符号区间。RootRecall 用 tree-sitter symbol table(带 start/end line)补这一刀。
  - "Cited but Not Verified"(arXiv 2605.06635):引用越多 fact-check 反降 ~42% → 光看引用数是错觉。

降级原则(不误杀真符号 —— pitfall #6 教训)
  - citation 缺 symbol/line → 退回"只查文件存在"(老行为)。
  - parse_file 返空/异常(未知后缀)→ 文件存在即过(symbol 核验跳过,不判假)。
  - symbol 按 qualified_name 或 name 匹配(子 agent 的 grep_symbol 输出 qualified_name)。
"""

from __future__ import annotations

from pathlib import Path

from rootrecall.services.code_index.parser import parse_file
from rootrecall.workflows.deep_research.state import DeepResearchState

# 核验等级(四档):strict=真·逐符号@行;line 在体外但 ±_LINE_TOLERANCE 内=near(近似,算通过);file=降级(只确认文件存在);bad=疑似编造。
_LEVEL_STRICT = "strict"
_LEVEL_NEAR = "near"
_LEVEL_FILE = "file"
_LEVEL_BAD = "bad"
# 行容差(P3.1.1):子 agent 常把行号引到函数体附近(调用点/声明行),±N 行内算「近似命中」而非幻觉。
_LINE_TOLERANCE = 5


def _check_one_citation(repo_root: Path, cite: dict) -> tuple[str, str]:
    """核一条 citation。返 (等级, 原因说明)。

    strict = 文件存在 + symbol 存在 + line ∈ [symbol.start, symbol.end];
    near   = line 在体外但 ±_LINE_TOLERANCE 内(子 agent 引到函数体附近 → 近似,不判幻觉);
    file   = 文件存在但降级(缺 symbol/line、或 parse_file 拿不到符号 → 不误杀);
    bad    = 核验失败(文件不存在 / symbol 找不到 / line 远离体内 → 疑似编造)。
    """
    file = cite.get("file") or ""
    fp = repo_root / file
    if not fp.exists():
        return _LEVEL_BAD, "文件不存在"
    if not fp.is_file():
        return _LEVEL_BAD, "不是文件"

    symbol = (cite.get("symbol") or "").strip()
    line = cite.get("line")
    # 缺 symbol 或 line → 没法做逐符号@行,退回老行为(只确认文件存在)。
    if not symbol or line is None:
        return _LEVEL_FILE, "文件存在(缺 symbol/line,退回老行为)"

    # parse_file 列出该文件所有符号(每个带行区间)。拿不到符号 → 降级,不误杀(防未知后缀)。
    try:
        syms = parse_file(fp)
    except Exception:  # noqa: BLE001 - parse 异常不判假
        return _LEVEL_FILE, "文件存在(parse_file 异常,跳过 symbol 核验)"
    if not syms:
        return _LEVEL_FILE, "文件存在(parse_file 无符号,可能未知后缀,跳过)"

    # 找 citation 声称的那个 symbol(qualified_name 优先,name 兜底)。
    hit = next((s for s in syms if s.qualified_name == symbol or s.name == symbol), None)
    if hit is None:
        return _LEVEL_BAD, f"文件存在但找不到符号 `{symbol}`(疑似编造)"

    # line 必须落在 symbol 的行区间内。
    try:
        ln = int(line)
    except (TypeError, ValueError):
        return _LEVEL_FILE, "文件存在 + 符号存在(line 非数字,跳过区间核验)"
    if hit.start_line <= ln <= hit.end_line:
        return _LEVEL_STRICT, f"✓ {hit.qualified_name} 占 {hit.start_line}-{hit.end_line} 行"
    # 不在体内、但在 ±_LINE_TOLERANCE 容差带内 → 近似(子 agent 常把行号引到函数体附近的调用点/声明,
    # 差几行不算幻觉);超出容差才判编造。
    if hit.start_line - _LINE_TOLERANCE <= ln <= hit.end_line + _LINE_TOLERANCE:
        return _LEVEL_NEAR, f"line {ln} 在 ±{_LINE_TOLERANCE} 容差内({hit.start_line}-{hit.end_line})"
    return _LEVEL_BAD, f"符号 `{symbol}` 存在但 line {ln} 远离体内({hit.start_line}-{hit.end_line})"


def _verify_report_citations(report_md: str, state: DeepResearchState) -> tuple[str, dict]:
    """逐符号@行核验每条 citation(Existence@Line)。返 (报告 + Verifier 章节, stats)。

    输入源 = state["findings"][].citations(结构化,保 symbol);不再 regex 抠报告文本。
    """
    repo_root = Path(state["repo_root"])
    findings = state.get("findings") or []
    plan = state.get("plan") or []

    # 把所有 findings 的 citations 摊平核验(记来源模块,方便定位)
    to_check: list[tuple[str, dict]] = []  # (module, cite)
    for f in findings:
        for c in f.get("citations") or []:
            to_check.append((f.get("module") or "?", c))

    strict = near = file_only = bad_n = 0
    bad: list[tuple[str, dict, str]] = []  # (module, cite, 原因)
    for mod, cite in to_check:
        level, why = _check_one_citation(repo_root, cite)
        if level == _LEVEL_STRICT:
            strict += 1
        elif level == _LEVEL_NEAR:
            near += 1
        elif level == _LEVEL_FILE:
            file_only += 1
        else:
            bad_n += 1
            bad.append((mod, cite, why))

    total = len(to_check)
    verified = strict + near + file_only  # 算通过:严格 + ±容差近似 + 文件存在降级
    # Existence@Line Ratio = 严格 symbol@line 通过 / 总(DocAgent Existence Ratio 的行级强化)。
    existence_at_line = round(strict / total, 3) if total else 0.0
    # 含 ±_LINE_TOLERANCE 容差(子 agent 常引到函数体附近,容差内不算幻觉)。
    existence_at_line_lenient = round((strict + near) / total, 3) if total else 0.0
    module_coverage = round(sum(1 for f in findings if f.get("citations")) / len(plan), 2) if plan else 0.0

    stats = {
        "citations": total,
        "verified": verified,
        "unverified": bad_n,
        "existence_at_line": existence_at_line,  # 严格(防幻觉硬指标)
        "existence_at_line_lenient": existence_at_line_lenient,  # 含 ±N 容差
        "symbol_strict": strict,
        "near": near,
        "module_coverage": module_coverage,  # 保留:模块广度
    }

    # Verifier 章节(透明:始终附,让读者看到核验跑过、结果如何)
    lines = [
        "",
        "## Verifier(逐符号@行回查)",
        "",
        f"- 引用总数 **{total}**;通过 **{verified}**(严格 symbol@line **{strict}** + ±{_LINE_TOLERANCE} 容差近似 **{near}**);疑似幻觉 **{bad_n}**。",
        f"- **Existence@Line Ratio = {existence_at_line:.1%}**(严格;含 ±{_LINE_TOLERANCE} 容差 = **{existence_at_line_lenient:.1%}**)。",
        f"- 模块覆盖率(产出带 citation 的模块占比):**{module_coverage:.0%}**。",
        "- ⚠️ 调研警示「Cited but Not Verified」(arXiv 2605.06635):引用数本身不降幻觉,每条都需翻书核。",
    ]
    if bad:
        lines += ["", "⚠️ 以下引用核验失败(疑似编造,需人工复核):"]
        for mod, cite, why in bad:
            lines.append(f"- [{mod}] `{cite.get('file', '?')}:{cite.get('line', '?')}` `{cite.get('symbol', '')}` — {why}")
    else:
        lines.append("- ✅ 所有引用通过逐符号@行核验。")
    report_md = report_md.rstrip() + "\n" + "\n".join(lines) + "\n"

    return report_md, stats
