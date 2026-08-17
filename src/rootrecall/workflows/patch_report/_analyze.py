"""patch_report · 单 PR 分析核心(P-A 1b,窗口展示 · 用户手敲)。

`_analyze_one_pr`:一条 PR(已 fetch 的 PatchArtifact)→ cited PRFinding。
light(默认)= 确定性收证 + 一次 LLM cited-reporter 调用(对标 deep_research _research_one_module
的 forced-summary 分支:喂结构化证据 + {summary,citations,theme} JSON 契约给裸模型)。
验证封顶:apply 门(validate_patch)+ CRG 风险(analyze_changes,graph-only);不跑 build/test/复现。
引用来自 PR diff(每条 file:line 都在 diff 里,Checkpoint 4 的 Verifier 回查 diff 上下文)。
"""
from __future__ import annotations

import logging

from rootrecall.workflows.patch_report.state import PRFinding

logger = logging.getLogger(__name__)

# ── 变更分类(theme)─────────────────────────────────────────────────────────
# 8 类,对齐 Conventional Commits v1.0.0 + Keep a Changelog 1.1.0 + 系统软件发行版特化。
# 比旧 4 类(security|function|refactor|perf)更细,是「发展趋势」专章的分桶骨架 + 安全横切信号。
# security 既是变更性质的一类,也触发安全告警章(见 _security_tier)。
THEMES = ("security", "bugfix", "feature", "config", "deps", "refactor", "perf", "other")
# 报告展示用中文标签(给 by_theme 渲染 + 发展趋势章)。
THEME_LABELS = {
    "security": "安全修复",
    "bugfix": "Bug 修复",
    "feature": "功能增强",
    "config": "配置变更",
    "deps": "依赖与打包",
    "refactor": "重构",
    "perf": "性能优化",
    "other": "其他",
}


async def _analyze_one_pr(art, *, repo_root: str, codebase: str) -> PRFinding:
    """★ 单 PR → cited PRFinding。

    1. apply 门:validate_patch(能否干净打到 repo)。
    2. CRG 风险/影响:CodeGraph.analyze_changes(changed_files=绝对路径, changed_ranges=从 diff hunk 算)
       → risk_score + changed_functions + review_priorities(graph-only,不 LLM)。
    3. cited-reporter:收证(diff + 风险 + apply)+ 一次 LLM → {summary(锚 file:line), citations, theme(8 类)}。
    4. 安全分层:theme=security → 至少 relevant(叠 CRG 安全词 + risk);theme 与 tier 对齐(治脱节 bug)。
    5. modules:改动函数的 community_id(按 module 分桶用)。
    CRG 缺/图没建 → 降级(无 risk/modules,继续 LLM 总结)。
    (曾有 deep 参数留 stretch,2026-08-14 删——空壳违背诚实信号原则;真需要逐 PR 深审时
    按 deep_research 的子 agent 模式实现,见 docs/p1-p2-improvement-backlog.md #8。)
    """
    # 1. apply 门
    from rootrecall.services.workspace.validate import validate_patch

    applies = bool(validate_patch(art.diff, forward_dir=repo_root).get("verified"))

    # 2. CRG 风险/影响
    risk_score = 0.0
    changed_funcs: list = []
    review_priorities: list = []
    try:
        from rootrecall.services.code_index.code_graph import CodeGraph

        abs_files, ranges = _diff_to_abs_ranges(art.diff, repo_root)
        ac = CodeGraph.open(codebase).analyze_changes(abs_files, changed_ranges=ranges)
        risk_score = float(ac.get("risk_score") or 0.0)
        changed_funcs = ac.get("changed_functions") or []
        review_priorities = ac.get("review_priorities") or []
    except Exception as e:  # noqa: BLE001 - CRG 缺/图没建 → 降级(无 risk,继续 LLM 总结)
        logger.warning("analyze_changes 不可用(%s): %s", codebase, e)

    # 3. cited-reporter(一次 LLM)—— 先拿 theme,再算 tier。
    #    旧顺序是「先 tier 再 LLM」,导致 tier 用 CRG 函数名词表、theme 用 LLM,两者脱节:
    #    PR 改的函数名不含安全词 → CRG 判 hits=False → 即使 LLM 读 diff 判 theme=security,tier 仍 none。
    #    改成「LLM 读 diff 先出 theme → tier 同时吃 theme + CRG + risk」(tier 与 theme 对齐)。
    evidence = _gather_evidence(art, applies, risk_score, review_priorities)
    summary, citations, theme = await _cited_summarize(evidence)

    # 4. 安全分层(theme=security 必升 relevant;再叠 CRG 安全词命中 + risk)
    security_tier = _security_tier(theme, changed_funcs, risk_score)

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

    from rootrecall.services.memory.ingest import _parse_diff_hunks

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


def _security_tier(theme: str, changed_funcs: list, risk_score: float) -> str:
    """none/relevant/high —— **theme 优先,叠 CRG 安全词 + risk**。

    旧版只看 CRG 函数名词表(SECURITY_KEYWORDS)+ risk,跟 LLM 判的 theme 脱节:
    改的函数名不含安全词就判 none,即使 LLM 读 diff 已判 theme=security(本批 PBAP/SDP 溢出 PR 全踩中)。
    现在以 theme 为主:theme=security → 至少 relevant(theme=security 且 risk 高 → high);
    theme 非 security 时退化到旧 CRG 词表 + risk 预筛(兜底抓 LLM 漏判的)。

    high = 该值得「人深审 / Checkpoint 4 LLM 深 CWE 分类」的子集。
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
    # theme=security:LLM 读 diff 已判为安全相关 → 至少 relevant;risk 高 → high。
    if theme == "security":
        return "high" if (risk_score >= 0.5 or hits) else "relevant"
    # theme 非 security:退化到旧 CRG 词表 + risk 预筛(兜底抓 LLM 漏判)。
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


async def _cited_summarize(evidence: str) -> tuple[str, list[dict], str]:
    """一次 LLM cited-reporter 调用 → (summary, citations, theme)。

    契约:只输出一个 JSON {summary, citations:[{file,line,symbol,claim}], theme}。
    summary 每条结论锚 diff 里的 file:line(Verifier 回查 diff);theme ∈ THEMES(8 类)。
    LLM/JSON 失败 → 降级(summary=evidence 首 300 字,空 citations,theme=other)。
    """
    from rootrecall.platform.config import get_app_config
    from rootrecall.platform.models import create_chat_model
    from rootrecall.services.memory.backends.native.extract import _extract_json_object

    cfg = get_app_config()
    role = cfg.model_roles.get("patch_analyzer") or cfg.model_roles.get("summarizer") or cfg.models[0].name
    model = create_chat_model(role)
    try:
        msg = await model.ainvoke([{"role": "user", "content": _CITED_PROMPT.format(evidence=evidence)}])
        raw = msg.content if isinstance(msg.content, str) else str(msg.content)
    except Exception as e:  # noqa: BLE001
        logger.warning("cited_summarize LLM 失败,降级: %s", e)
        return evidence[:300], [], "other"

    data = _extract_json_object(raw)
    if not data:
        logger.warning("cited_summarize 抠不到 JSON,降级。前 200 字: %s", raw[:200])
        return evidence[:300], [], "other"
    summary = str(data.get("summary") or "")[:800]
    # pyright 收窄 + 防御 LLM schema 方差(踩坑#5):只留 dict 型 citation,丢弃非 dict。
    citations: list[dict] = [c for c in (data.get("citations") or []) if isinstance(c, dict)]
    theme = str(data.get("theme") or "other")
    if theme not in THEMES:
        theme = "other"
    return summary, citations, theme


def _modules_for(changed_funcs: list, codebase: str) -> list:
    """改动函数的 community_id 列表(去重;按 module 分桶用)。CRG 缺 → []。"""
    if not changed_funcs:
        return []
    try:
        from rootrecall.services.code_index.code_graph import CodeGraph

        qns = [f.get("qualified_name") for f in changed_funcs if f.get("qualified_name")]
        cmap = CodeGraph.open(codebase).community_ids_for(qns)
        return sorted({v for v in cmap.values() if v is not None})
    except Exception:  # noqa: BLE001
        return []


_CITED_PROMPT = """你在分析一个 GitHub PR 的 diff,产出一条 cited 鉴定(给跨 PR 聚合报告用)。

### 证据 ###
{evidence}

### 要求 ###
只输出一个 JSON 对象(不要 markdown 围栏、不要解释),严格符合:
{{
  "summary": "这个 PR 干了啥 + 风险点(≤200 字,每条结论尽量锚 diff 里的 file:line)",
  "citations": [{{"file": "diff 里的文件路径", "line": <int 或 null>, "symbol": "涉及函数/符号", "claim": "这条引用说明啥"}}],
  "theme": "security | bugfix | feature | config | deps | refactor | perf | other"
}}
- citations 的 file:line 必须来自上面的 diff(Verifier 会回查;编造的过不了)。
- theme 选**最贴切的一个**(对齐 Conventional Commits + Keep a Changelog):
  - **security** 安全修复(溢出/UAF/空指针/权限绕过/CVE 等漏洞修补)
  - **bugfix** 非安全的 bug 修复(崩溃/逻辑错/行为异常)
  - **feature** 功能增强(新能力/新流程/行为新增)
  - **config** 配置变更(改默认值/开关/编译选项,不改代码逻辑)
  - **deps** 依赖与打包(增删构建依赖/打包脚本/install 列表/changelog)
  - **refactor** 重构(不改行为的代码整理)
  - **perf** 性能优化
  - **other** 其他(文档/测试/CI/杂项)
  **关键**:补丁是「修安全漏洞」就选 security(不论 PR 层是直接改 C 源还是落在 debian/patches/*.patch)。
- 拿不准的字段留空。"""
