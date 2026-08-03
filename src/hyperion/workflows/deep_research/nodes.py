"""deep_research workflow 六节点(R3.2)。

ingest / index / plan / memorize 是胶水(复用 code_index + CRG + memory,本文件直接实现);
research / report 调两个核心 helper ——
  - ``_research_modules``:每模块 ReAct 子 agent 并行深挖 + cited-reporter(防幻觉)
  - ``_verify_report_citations``:报告写完回查每条 file:line 是否真实(DocAgent Verifier)
这两个是 R3.2 的核心算法,在 ``_research.py`` / ``_verify.py`` 里(窗口展示 · 用户手敲)。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from hyperion.platform.config import get_app_config
from hyperion.workflows.deep_research.report import render_report
from hyperion.workflows.deep_research.state import DeepResearchState, ModuleFinding, ModulePlan

logger = logging.getLogger(__name__)

# 最多调研多少个模块(CRG 可能给几百个社区;MVP 取最大的 N 个,余进报告附录)
_MAX_MODULES = 8


# ── 1. ingest:建工作区 + 注册 scope ───────────────────────────────────────


def node_ingest(state: DeepResearchState) -> dict:
    """本地路径 → 注册 scope + 建轻量工作区(不改代码;放报告 + structgraph db 各自目录)。"""
    codebase = state["codebase"]
    owner = state.get("owner") or "default"
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    workdir = Path("data/research") / f"{codebase}__{ts}"
    workdir.mkdir(parents=True, exist_ok=True)
    from hyperion.services.memory.schema import Scope

    return {"workdir": str(workdir), "scope": Scope(owner=owner, codebase=codebase)}


# ── 2. index:code_index 语义索引 + CRG 结构图 ─────────────────────────────


def node_index(state: DeepResearchState) -> dict:
    """建两套索引:code_index(语义检索)+ CodeGraph(结构图 + 社区 + hub)。

    复用 build_index(hyperion index 同款)+ CodeGraph.build。两者都慢(全仓解析),各跑一次。
    """
    from hyperion.services.code_index.code_graph import CodeGraph
    from hyperion.services.code_index.embed import create_embedder
    from hyperion.services.code_index.index import build_index

    repo_root = state["repo_root"]
    codebase = state["codebase"]
    cfg = get_app_config()

    # ① 语义索引(#58 已修:巨 C 文件不再 400)
    embedder = create_embedder(cfg.code_index.embedding)
    try:
        build_index(repo_root, codebase, embedder, force=False)
    except Exception:  # noqa: BLE001 - 索引失败不阻断结构图(可能已建过 / 部分 lang 失败)
        logger.warning("code_index 建索引失败,继续 CRG 结构图", exc_info=True)

    # ② CRG 结构图(+ 社区检测 + 持久化)
    cg = CodeGraph.build(repo_root, codebase)
    overview = cg.architecture_overview()
    stats = cg.stats()
    logger.info("CRG 建图完成: %s 个社区, %s 节点", len(overview.get("communities", [])), stats.get("total_nodes"))

    return {
        "index_built": True,
        "codegraph_stats": stats,
        "architecture_overview": overview,
    }


# ── 3. plan:CRG 社区 → 模块清单 ───────────────────────────────────────────


def node_plan(state: DeepResearchState) -> dict:
    """CRG communities + hub_nodes → 模块清单(社区 = 自然模块边界)。

    MVP 确定性:取最大的 N 个社区,各成一个 ModulePlan(member_files + key_symbols + 焦点模板)。
    LLM 命名 / storm 多视角发问 = pull-by-need(社区名 + 通用焦点够首版报告用)。
    """
    from hyperion.services.code_index.code_graph import CodeGraph

    codebase = state["codebase"]
    overview = state.get("architecture_overview", {})
    communities = list(overview.get("communities", []))
    # 大社区优先(成员多 = 该模块代码体量大、更值得深挖)
    communities.sort(key=lambda c: len(c.get("members", [])), reverse=True)
    communities = communities[:_MAX_MODULES]

    # hub 节点按 community_id 分桶,给每个模块填 key_symbols
    cg = CodeGraph.open(codebase)
    hubs = cg.hub_nodes(top_n=60)
    by_comm: dict = {}
    for h in hubs:
        cid = h.get("community_id")
        if cid is None:
            continue
        by_comm.setdefault(cid, []).append(h["qualified_name"])

    plan: list[ModulePlan] = []
    for comm in communities:
        cid = comm.get("id")
        plan.append(
            ModulePlan(
                name=comm.get("name") or f"community-{cid}",
                focus="梳理该模块的职责、公开接口(导出函数/类型)、关键数据结构与调用关系;指出值得注意的设计决策。",
                member_files=list(comm.get("members", []))[:40],
                key_symbols=by_comm.get(cid, [])[:15],
            )
        )
    logger.info("plan: %d 个模块(从 %d 个社区取 top-%d)", len(plan), len(overview.get("communities", [])), _MAX_MODULES)
    return {"plan": plan}


# ── 4. research:每模块 ReAct 子 agent 并行深挖(核心,窗口展示)─────────────


async def node_research(state: DeepResearchState) -> dict:
    """调 _research_modules:每模块一个 ReAct 子 agent 并行深挖,产出 cited findings。

    核心算法(子 agent fan-out + cited-reporter source registry + emit-concept 防幻觉)
    在 hyperion.workflows.deep_research._research —— 窗口展示 · 用户手敲。
    """
    from hyperion.workflows.deep_research._research import _research_modules

    findings: list[ModuleFinding] = await _research_modules(state)
    return {"findings": findings}


# ── 5. report:渲染 §5 + Verifier 回查 ─────────────────────────────────────


def node_report(state: DeepResearchState) -> dict:
    """渲染 §5 Markdown(系统架构 = CRG 图驱动)+ Verifier 回查每条 file:line。

    Verifier 核心(抽报告里每条引用回查代码/图,虚假者标红)在 _verify —— 窗口展示 · 用户手敲。
    """
    from hyperion.workflows.deep_research._verify import _verify_report_citations

    report_md = render_report(state)
    # Verifier:返 (修正后报告, 覆盖率统计);虚假引用标红/剔除
    report_md, verify_stats = _verify_report_citations(report_md, state)

    workdir = Path(state["workdir"])
    report_path = workdir / "report.md"
    report_path.write_text(report_md, encoding="utf-8")
    logger.info("报告写出: %s(Verifier: %s)", report_path, verify_stats)
    return {"report_path": str(report_path)}


# ── 6. memorize:抽 CodebaseFact 入记忆 ────────────────────────────────────


async def node_memorize(state: DeepResearchState) -> dict:
    """抽 CodebaseFact(kind 已在 schema)入记忆,带 commit SHA。复用 extract_items + memorize。

    闭环:这些结构化事实后续 bug-RCA 能 recall 命中(P1 调研 → P2 RCA 地基)。

    async:MemoryService.memorize 是协程(async def);同步调会得到一个没人 await 的
    coroutine → RuntimeWarning + 0 条入记忆(graph 经 ainvoke 跑,async 节点会被 await)。
    """
    from hyperion.platform.models import create_chat_model
    from hyperion.services.memory.backends.native.extract import extract_items
    from hyperion.services.memory.manager import get_memory_service

    scope = state["scope"]
    report_path = state.get("report_path")
    if not report_path:
        return {"facts_memorized": 0}
    report_text = Path(report_path).read_text(encoding="utf-8")

    cfg = get_app_config()
    # role=extractor 的模型(没有就首个);commit SHA 能取则取
    role_model = cfg.model_roles.get("extractor") or cfg.model_roles.get("planner")
    model = create_chat_model(role_model) if role_model else create_chat_model(cfg.models[0].name)
    commit_sha = _git_head_sha(state["repo_root"])

    try:
        items = extract_items(
            report_text,
            repo=state["codebase"],
            scope=scope,
            model=model,
            commit_sha=commit_sha,
            source=str(report_path),
            source_tier="inferred",  # 调研报告抽的事实属「推导」级(非 imported 原文)
        )
        svc = get_memory_service()
        await svc.memorize(items, scope)
        # 只把 codebase_fact 计入( extract 可能也抽 bug_lesson,但调研报告应主导 codebase_fact)
        n = sum(1 for it in items if getattr(it, "kind", None) == "codebase_fact")
        logger.info("memorize: %d 个 codebase_fact 入记忆(repo=%s)", n, state["codebase"])
        return {"facts_memorized": n}
    except Exception:  # noqa: BLE001 - 记忆失败不阻断报告产出(报告已写出)
        logger.warning("memorize 失败,报告已产出不受影响", exc_info=True)
        return {"facts_memorized": 0}


def _git_head_sha(repo_root: str) -> str:
    """取仓库 HEAD commit SHA(溯源用);非 git 仓 / git 缺失 → 空串。"""
    import subprocess

    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""
