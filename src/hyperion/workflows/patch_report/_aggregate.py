"""patch_report · 跨 PR 聚合(P-A 1b Checkpoint 4)。

收所有 PRFinding → ① 确定性分桶统计(按 theme/security_tier/module;**数字来自结构,非 LLM**)
+ ② 一次 LLM cited 综合(喂所有 findings 作证据 → {cross_summary, citations} 契约;抄 plan_modules 单批)。
诚实:数字是结构的(准);cross_summary 是 LLM(锚 file:line,Verifier 回查);安全告警标「建议人工复核」。

(注:本模块偏机械聚合 + 复用 cited-LLM 模式,非新算法 → 直接写,不窗口展示;区别于 _analyze_one_pr 那种新核心。)
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict

logger = logging.getLogger(__name__)


def aggregate(findings: list[dict], codebase: str = "") -> dict:
    """跨 PR 聚合 → ``{stats, cross_summary, citations, high_security_prs}``。

    ``findings``:node_analyze 产的 ``list[PRFinding]``(每条带 theme/security_tier/risk_score/modules/summary/citations)。
    """
    # ① 确定性分桶 + 统计(数字来自结构)
    by_theme = Counter(f.get("theme") or "function" for f in findings)
    by_tier = Counter(f.get("security_tier") or "none" for f in findings)
    high_security = [f for f in findings if f.get("security_tier") == "high"]
    high_risk = [f for f in findings if (f.get("risk_score") or 0) >= 0.5]
    # module(community)分桶:每个 finding 的 modules 列表 → 计 PR 数(同 finding 内去重)
    module_prs: dict = defaultdict(int)
    for f in findings:
        for m in set(f.get("modules") or []):
            module_prs[m] += 1
    hot_modules = sorted(module_prs.items(), key=lambda kv: -kv[1])[:10]
    stats = {
        "total_prs": len(findings),
        "by_theme": dict(by_theme),
        "by_tier": dict(by_tier),
        "high_security_count": len(high_security),
        "high_risk_count": len(high_risk),
        "hot_modules": [{"module": m, "pr_count": c} for m, c in hot_modules],
    }

    # ② 一次 LLM cited 综合(单批,抄 plan_modules:喂所有 findings → {cross_summary, citations})
    cross_summary, citations = _synthesize(findings, stats)
    return {
        "stats": stats,
        "cross_summary": cross_summary,
        "citations": citations,
        "high_security_prs": [f.get("title") for f in high_security],
    }


def _synthesize(findings: list[dict], stats: dict) -> tuple[str, list[dict]]:
    """一次 LLM:所有 findings 作证据 → {cross_summary(锚 file:line), citations}。失败降级(不抛)。"""
    from hyperion.platform.config import get_app_config
    from hyperion.platform.models import create_chat_model
    from hyperion.services.memory.backends.native.extract import _extract_json_object

    if not findings:
        return "", []
    evidence = _render_findings_evidence(findings, stats)
    cfg = get_app_config()
    role = cfg.model_roles.get("patch_analyzer") or cfg.model_roles.get("summarizer") or cfg.models[0].name
    model = create_chat_model(role)
    fallback = f"(跨 PR 综合降级)共 {stats['total_prs']} PRs;theme={stats['by_theme']};tier={stats['by_tier']}"
    try:
        msg = model.invoke([{"role": "user", "content": _AGG_PROMPT.format(evidence=evidence)}])
        raw = msg.content if isinstance(msg.content, str) else str(msg.content)
    except Exception as e:  # noqa: BLE001
        logger.warning("aggregate LLM 失败,降级: %s", e)
        return fallback, []
    data = _extract_json_object(raw)
    if not data:
        logger.warning("aggregate 抠不到 JSON,降级。")
        return fallback, []
    summary = str(data.get("cross_summary") or "")[:1200]
    cites = [c for c in (data.get("citations") or []) if isinstance(c, dict)]
    return summary, cites


def _render_findings_evidence(findings: list[dict], stats: dict) -> str:
    """把 findings + stats 渲染成 LLM 证据串(每条 finding 的 summary 是 cited,带 file:line)。"""
    lines = [
        "## 统计(确定性)",
        f"共 {stats['total_prs']} PRs;theme={stats['by_theme']};"
        f"security_tier={stats['by_tier']};high_security={stats['high_security_count']};"
        f"high_risk={stats['high_risk_count']}",
    ]
    if stats["hot_modules"]:
        lines.append("热模块(community): " + ", ".join(
            f"{m['module']}({m['pr_count']} PRs)" for m in stats["hot_modules"][:6]))
    lines.append("\n## 各 PR finding(cited,带 file:line)")
    for f in findings:
        lines.append(
            f"- [{f.get('theme', '?')}/{f.get('security_tier', '?')}|risk={f.get('risk_score', 0):.2f}] "
            f"{f.get('title', '?')}: {f.get('summary', '')[:240]}")
    return "\n".join(lines)


_AGG_PROMPT = """你在汇总一批 PR 的鉴定(每条 PR 已分析过),产出跨 PR 聚合结论。

### 证据 ###
{evidence}

### 要求 ###
只输出一个 JSON 对象(无 markdown 围栏、无解释),符合:
{{
  "cross_summary": "这批 PR 整体在干啥 + 重点风险 / 安全告警(≤500 字,结论尽量锚具体 PR 的 file:line)",
  "citations": [{{"file": "...", "line": <int 或 null>, "symbol": "...", "claim": "这条引用说明啥"}}]
}}
- 安全相关结论标注「建议人工复核」(自动分类非正式验证)。
- citations 的 file:line 必须来自上面证据里 PR 提到的(Verifier 会回查;编造的过不了)。"""
