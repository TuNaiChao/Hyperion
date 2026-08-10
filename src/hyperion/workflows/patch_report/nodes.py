"""patch_report workflow 节点(P-A 1b)。

ingest / fetch_prs / report 是胶水(复用 GitHubFetcher + memory,本文件直接实现);
analyze 调核心 helper ——
  - ``_analyze_one_pr``:每 PR 一个分析任务(validate_patch + CodeGraph.analyze_changes +
    cited-reporter 产 PRFinding),在 ``_analyze.py`` 里(**窗口展示 · 用户手敲**)。
Checkpoint 4 实装 _aggregate(跨 PR 聚合)+ _verify(零幻觉回查)+ 真 render。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path

from hyperion.workflows.patch_report.state import PatchReportState, PRFinding

logger = logging.getLogger(__name__)


# ── 1. ingest:建工作区 + 注册 scope ───────────────────────────────────────


def node_ingest(state: PatchReportState) -> dict:
    """建轻量工作区(放报告)+ 注册记忆 scope。不改代码。"""
    codebase = state["codebase"]
    owner = state.get("owner") or "default"
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    workdir = Path("data/patch_report") / f"{codebase}__{ts}"
    workdir.mkdir(parents=True, exist_ok=True)
    from hyperion.services.memory.schema import Scope

    return {"workdir": str(workdir), "scope": Scope(owner=owner, codebase=codebase)}


# ── 2. fetch_prs:并发抓取每条 PR 的 diff + meta ────────────────────────────


async def node_fetch_prs(state: PatchReportState) -> dict:
    """并发抓取所有 PR(GitHubFetcher / GerritFetcher 同接口)。失败的 PR 跳过(不阻断批次)。"""
    from hyperion.services.patch.fetcher import from_config

    prs = state.get("prs") or []
    if not prs:
        logger.warning("patch_report: 没给 PR 列表,nothing to do。")
        return {"artifacts": []}

    fetcher = from_config()
    sem = asyncio.Semaphore(max(1, state.get("concurrency") or 3))

    async def _one(url: str):
        async with sem:
            try:
                return await fetcher.fetch(url)
            except Exception as e:  # noqa: BLE001 - 单条 PR 抓取失败不连坐(404/网络/限速)
                logger.warning("fetch 失败 %s: %s", url, e)
                return None

    arts = await asyncio.gather(*(_one(u) for u in prs))
    artifacts = [a for a in arts if a is not None]
    logger.info("fetch_prs: %d/%d PR 抓取成功", len(artifacts), len(prs))
    return {"artifacts": artifacts}


# ── 3. analyze:每 PR 一个分析任务并发跑(核心,窗口展示)─────────────────────


async def node_analyze(state: PatchReportState) -> dict:
    """调 ``_analyze_one_pr``:每 PR 一个分析任务并发(validate + CRG risk + cited-reporter → PRFinding)。

    核心算法(cited-reporter + 风险/主题/安全分层)在 hyperion.workflows.patch_report._analyze
    —— 窗口展示 · 用户手敲。单 PR 失败 → 降级一条 "(分析失败)" finding,不阻断批次。
    """
    from hyperion.workflows.patch_report._analyze import _analyze_one_pr

    artifacts = state.get("artifacts") or []
    if not artifacts:
        return {"findings": []}

    repo_root = state["repo_root"]
    codebase = state["codebase"]
    deep = bool(state.get("deep"))
    sem = asyncio.Semaphore(max(1, state.get("concurrency") or 3))

    async def _one(art):
        async with sem:
            try:
                return await _analyze_one_pr(art, repo_root=repo_root, codebase=codebase, deep=deep)
            except Exception as e:  # noqa: BLE001 - 单 PR 分析失败不连坐
                logger.warning("analyze 失败 %s: %s", getattr(art, "url", "?"), e)
                return PRFinding(url=getattr(art, "url", ""), title=getattr(art, "title", ""),
                                 summary=f"(分析失败: {e})")

    findings = list(await asyncio.gather(*(_one(a) for a in artifacts)))
    logger.info("analyze: %d PR findings", len(findings))
    return {"findings": findings}


# ── 4. aggregate:跨 PR 聚合(Checkpoint 4)──────────────────────────────────


def node_aggregate(state: PatchReportState) -> dict:
    """调 ``_aggregate.aggregate``:确定性分桶统计(数字来自结构)+ 一次 LLM cited 综合。"""
    from hyperion.workflows.patch_report._aggregate import aggregate

    findings = state.get("findings") or []
    agg = aggregate(findings, state.get("codebase", ""))
    st = agg["stats"]
    logger.info("aggregate: %d PRs → high_security=%d high_risk=%d",
                st["total_prs"], st["high_security_count"], st["high_risk_count"])
    return {"aggregate": agg}


# ── 5. report:渲染 cited 报告 + Verifier 回查 ──────────────────────────────


def node_report(state: PatchReportState) -> dict:
    """渲染 cited 报告(跨 PR 综合 + 每 PR deep-dive + sources)+ Verifier 回查 citation file。"""
    from hyperion.workflows.patch_report.report import render_patch_report, verify_and_append

    report_md = verify_and_append(render_patch_report(state), state)
    p = Path(state.get("workdir") or ".") / "report.md"
    p.write_text(report_md, encoding="utf-8")
    logger.info("报告写出: %s", p)
    return {"report_path": str(p)}


# ── 6. memorize:聚合结论 → codebase_fact 入记忆 ────────────────────────────


async def node_memorize(state: PatchReportState) -> dict:
    """聚合结论 + 高安全告警抽 codebase_fact 入记忆(后续 memory_recall 可命中)。

    async:MemoryService.memorize 是协程(graph 经 ainvoke 跑,async 节点会被 await)。
    """
    from hyperion.platform.config import get_app_config
    from hyperion.platform.models import create_chat_model
    from hyperion.services.memory.backends.native.extract import extract_items
    from hyperion.services.memory.manager import get_memory_service

    scope = state.get("scope")
    agg = state.get("aggregate") or {}
    report_path = state.get("report_path")
    if not scope or not report_path:
        return {"facts_memorized": 0}
    text = (agg.get("cross_summary") or "") + "\n" + (
        f"stats: {agg.get('stats')}\n高安全 PR: {agg.get('high_security_prs')}")
    if not text.strip():
        return {"facts_memorized": 0}
    cfg = get_app_config()
    role = cfg.model_roles.get("extractor") or cfg.model_roles.get("planner")
    model = create_chat_model(role) if role else create_chat_model(cfg.models[0].name)
    try:
        items = extract_items(text, repo=state["codebase"], scope=scope, model=model,
                              source=str(report_path), source_tier="inferred")
        await get_memory_service().memorize(items, scope)
        n = sum(1 for it in items if getattr(it, "kind", None) == "codebase_fact")
        logger.info("memorize: %d codebase_fact 入记忆(repo=%s)", n, state["codebase"])
        return {"facts_memorized": n}
    except Exception:  # noqa: BLE001 - 记忆失败不阻断报告产出(报告已写出)
        logger.warning("memorize 失败,报告已产出不受影响", exc_info=True)
        return {"facts_memorized": 0}
