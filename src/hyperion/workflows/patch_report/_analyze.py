"""patch_report · 单 PR 分析核心(P-A 1b,窗口展示 · 用户手敲)。

`_analyze_one_pr`:一条 PR(已 fetch 的 PatchArtifact)→ cited PRFinding。
light(默认)= 确定性收证 + 一次 LLM cited-reporter 调用(对标 deep_research _research_one_module
的 forced-summary 分支:喂结构化证据 + {summary,citations,theme} JSON 契约给裸模型)。
验证封顶:apply 门(validate_patch)+ CRG 风险(analyze_changes,graph-only);不跑 build/test/复现。
引用来自 PR diff(每条 file:line 都在 diff 里,Checkpoint 4 的 Verifier 回查 diff 上下文)。
"""
from __future__ import annotations

import logging

from hyperion.workflows.patch_report.state import PRFinding

logger = logging.getLogger(__name__)


async def _analyze_one_pr(art, *, repo_root: str, codebase: str, deep: bool = False) -> PRFinding:
    """★ 单 PR → cited PRFinding。

    1. apply 门:validate_patch(能否干净打到 repo)。
    2. CRG 风险/影响:CodeGraph.analyze_changes(changed_files=绝对路径, changed_ranges=从 diff hunk 算)
       → risk_score + changed_functions + review_priorities(graph-only,不 LLM)。
    3. 安全分层:SECURITY_KEYWORDS 命中 + risk_score 预筛 → none/relevant/high。
    4. cited-reporter:收证(diff + 风险 + apply)+ 一次 LLM → {summary(锚 file:line), citations, theme}。
    5. modules:改动函数的 community_id(按 module 分桶用)。
    CRG 缺/图没建 → 降级(无 risk/modules,继续 LLM 总结)。deep(ReAct 子 agent 深挖)留 stretch。
    """
    # 1. apply 门
    from hyperion.services.workspace.validate import validate_patch

    applies = bool(validate_patch(art.diff, forward_dir=repo_root).get("verified"))

    # 2. CRG 风险/影响
    risk_score = 0.0
    changed_funcs: list = []
    review_priorities: list = []
    try:
        from hyperion.services.code_index.code_graph import CodeGraph

        abs_files, ranges = _diff_to_abs_ranges(art.diff, repo_root)
        ac = CodeGraph.open(codebase).analyze_changes(abs_files, changed_ranges=ranges)
        risk_score = float(ac.get("risk_score") or 0.0)
        changed_funcs = ac.get("changed_functions") or []
        review_priorities = ac.get("review_priorities") or []
    except Exception as e:  # noqa: BLE001 - CRG 缺/图没建 → 降级(无 risk,继续 LLM 总结)
        logger.warning("analyze_changes 不可用(%s): %s", codebase, e)

    # 3. 安全分层(keyword + risk 预筛)
    security_tier = _security_tier(changed_funcs, risk_score)

    # 4. cited-reporter(一次 LLM)
    evidence = _gather_evidence(art, applies, risk_score, review_priorities)
    summary, citations, theme = await _cited_summarize(evidence, security_tier)

    # 5. modules(改动函数的 community)
    modules = _modules_for(changed_funcs, codebase)

    return PRFinding(
        url=art.url, title=art.title, applies=applies, risk_score=risk_score,
        security_tier=security_tier, theme=theme, modules=modules,
        changed_files=list(art.changed_files), summary=summary, citations=citations,
    )


# ── helpers ────────────────────────────────────────────────────────────────


def _diff_to_abs_ranges(diff: str, repo_root: str) -> tuple[list[str], dict[str, list[tuple[int, int]]]]:
    """从 unified diff 抽「绝对路径文件 + 行范围」喂 CRG analyze_changes。

    CRG 的 qualified_name 是「绝对路径::symbol」(集成测实证),故 file 要拼 repo_root。
    行范围:每个 hunk 从 new_start 起、跨 hunk 体里的 context+added 行(覆盖改动区,够 CRG 映射到所属函数)。
    复用 ingest._parse_diff_hunks(它已解 `@@ -a,b +c,d @@` + 文件头)。
    """
    from pathlib import Path

    from hyperion.services.memory.ingest import _parse_diff_hunks

    ranges: dict[str, list[tuple[int, int]]] = {}
    abs_files: list[str] = []
    for h in _parse_diff_hunks(diff):
        rel = h.get("file") or ""
        if not rel:
            continue
        abs_f = str(Path(repo_root, rel))
        if abs_f not in ranges:
            ranges[abs_f] = []
            abs_files.append(abs_f)
        new_start = int(h.get("new_start") or 0)
        body = h.get("body") or ""
        span = sum(1 for ln in body.splitlines() if ln.startswith(" ") or ln.startswith("+"))
        ranges[abs_f].append((new_start, new_start + max(span, 1)))
    return abs_files, ranges


def _security_tier(changed_funcs: list, risk_score: float) -> str:
    """SECURITY_KEYWORDS(名字命中)+ risk_score 预筛 → none/relevant/high。

    high = 命中安全词 且 risk 高(才值得人深审 / Checkpoint 4 的 LLM 深 CWE 分类只对 high 子集)。
    CRG 缺(无 changed_funcs)→ 只看 risk(降级)。
    """
    try:
        from code_review_graph.constants import SECURITY_KEYWORDS
        kws = SECURITY_KEYWORDS
    except Exception:  # noqa: BLE001
        kws = ()
    hits = any(
        any(kw in ((f.get("name") or "") + " " + (f.get("qualified_name") or "")).lower() for kw in kws)
        for f in changed_funcs
    )
    if hits and risk_score >= 0.5:
        return "high"
    if hits or risk_score >= 0.5:
        return "relevant"
    return "none"


def _gather_evidence(art, applies: bool, risk_score: float, review_priorities: list) -> str:
    """收 cited-reporter 的证据串:PR 元信息 + apply + 风险 + 高风险函数 + diff 摘录。"""
    diff_excerpt = (art.diff or "")[:4000]  # 防爆上下文(截断;LLM 引用前部 hunk 够)
    rp = "\n".join(
        f"- {f.get('qualified_name', '?')} (risk={f.get('risk_score', 0):.2f})"
        for f in review_priorities[:8]
    ) if review_priorities else ""
    return (
        f"PR: {art.title}\nURL: {art.url}\n"
        f"applies(能否干净打上): {applies}\n"
        f"overall risk_score: {risk_score:.2f}\n"
        + (f"高风险改动函数:\n{rp}\n" if rp else "")
        + (f"\nPR body:\n{(art.body or '')[:600]}\n" if art.body else "")
        + f"\n--- diff(摘录)---\n{diff_excerpt}\n"
    )


async def _cited_summarize(evidence: str, security_tier: str) -> tuple[str, list[dict], str]:
    """一次 LLM cited-reporter 调用 → (summary, citations, theme)。

    契约:只输出一个 JSON {summary, citations:[{file,line,symbol,claim}], theme}。
    summary 每条结论锚 diff 里的 file:line(Verifier 回查 diff);theme ∈ security|function|refactor|perf。
    LLM/JSON 失败 → 降级(summary=evidence 首 300 字,空 citations)。
    """
    from hyperion.platform.config import get_app_config
    from hyperion.platform.models import create_chat_model
    from hyperion.services.memory.backends.native.extract import _extract_json_object

    cfg = get_app_config()
    role = cfg.model_roles.get("patch_analyzer") or cfg.model_roles.get("summarizer") or cfg.models[0].name
    model = create_chat_model(role)
    try:
        msg = await model.ainvoke([{"role": "user", "content": _CITED_PROMPT.format(evidence=evidence, tier=security_tier)}])
        raw = msg.content if isinstance(msg.content, str) else str(msg.content)
    except Exception as e:  # noqa: BLE001
        logger.warning("cited_summarize LLM 失败,降级: %s", e)
        return evidence[:300], [], "function"

    data = _extract_json_object(raw)
    if not data:
        logger.warning("cited_summarize 抠不到 JSON,降级。前 200 字: %s", raw[:200])
        return evidence[:300], [], "function"
    summary = str(data.get("summary") or "")[:800]
    # pyright 收窄 + 防御 LLM schema 方差(踩坑#5):只留 dict 型 citation,丢弃非 dict。
    citations: list[dict] = [c for c in (data.get("citations") or []) if isinstance(c, dict)]
    theme = str(data.get("theme") or "function")
    if theme not in ("security", "function", "refactor", "perf"):
        theme = "function"
    return summary, citations, theme


def _modules_for(changed_funcs: list, codebase: str) -> list:
    """改动函数的 community_id 列表(去重;按 module 分桶用)。CRG 缺 → []。"""
    if not changed_funcs:
        return []
    try:
        from hyperion.services.code_index.code_graph import CodeGraph

        qns = [f.get("qualified_name") for f in changed_funcs if f.get("qualified_name")]
        cmap = CodeGraph.open(codebase).community_ids_for(qns)
        return sorted({v for v in cmap.values() if v is not None})
    except Exception:  # noqa: BLE001
        return []


_CITED_PROMPT = """你在分析一个 GitHub PR 的 diff,产出一条 cited 鉴定(给跨 PR 聚合报告用)。

### 证据 ###
{evidence}

### 安全分层(预筛结果)###
security_tier = {tier}(none/relevant/high;high = 命中安全词且风险高)

### 要求 ###
只输出一个 JSON 对象(不要 markdown 围栏、不要解释),严格符合:
{{
  "summary": "这个 PR 干了啥 + 风险点(≤200 字,每条结论尽量锚 diff 里的 file:line)",
  "citations": [{{"file": "diff 里的文件路径", "line": <int 或 null>, "symbol": "涉及函数/符号", "claim": "这条引用说明啥"}}],
  "theme": "security | function | refactor | perf"
}}
- citations 的 file:line 必须来自上面的 diff(Verifier 会回查;编造的过不了)。
- 拿不准的字段留空。theme 选最贴切的(安全相关→security,新功能→function,重构→refactor,性能→perf)。"""
