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


# ── 4. report:渲染 cited 报告(Checkpoint 3 临时桩;Checkpoint 4 换真 aggregate+verify+render)──


def node_report(state: PatchReportState) -> dict:
    """【Checkpoint 3 临时桩】把 findings 写成 md;Checkpoint 4 换成真跨 PR 聚合 + cited 渲染 + Verifier 回查。"""
    workdir = Path(state.get("workdir") or ".")
    findings = state.get("findings") or []

    lines = [
        "# patch-report(Checkpoint 3 临时输出)",
        "",
        f"**{len(findings)} PRs**(完整 cited 报告 + 跨 PR 聚合 + Verifier 回查在 Checkpoint 4 实装):",
        "",
    ]
    for f in findings:
        lines.append(
            f"- **{f.get('title', '(无标题)')}** "
            f"[applies={f.get('applies')} risk={f.get('risk_score', '?')} "
            f"tier={f.get('security_tier', '?')} theme={f.get('theme', '?')}]"
            f"  \n  {f.get('summary', '')[:200]}"
        )
    md = "\n".join(lines)
    p = workdir / "report.md"
    p.write_text(md, encoding="utf-8")
    logger.info("临时报告写出: %s(Checkpoint 4 替换为完整 cited 报告)", p)
    return {"report_path": str(p)}
