"""native 后端 · 写路径(R1 backends/native/memorize.py)。

流水线(对应 memory-design.md §5 + mem0 Algorithm-1):
  extract(LLM 抽 KI) → embed(summary 向量) → link(连图边) → merge(冲突合并) → upsert
  + write-time 严格过滤(只存根因/模式/规则)。

合并策略(v1,借 mnemopi veracity + graphiti bi-temporal):
  - 重提(content_key 相同 → 同 id):Bayes 置信度累加 (1-cur)·tier·step,不新增。
  - 冲突(同 repo+kind+subject 但结论不同):新覆盖旧 —— 旧条设 invalid_at + superseded_by=新id
    (recency-wins + 显式失效,不物理删;研究称这是"最站得住脚的默认")。
  - 嵌向量:复用 code_index 的 embedder(embed=code_index);off 则不算(只走 BM25)。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from langchain.chat_models import BaseChatModel

from hyperion.services.memory.backends.native.store import MemoryStore
from hyperion.services.memory.schema import TIER_WEIGHT, KnowledgeItem, Scope, SourceTier

logger = logging.getLogger(__name__)


def _norm(text: str) -> str:
    """文本归一化(压空白+casefold),用于结论比对(与 schema 的 content_key 同口径)。"""
    return re.sub(r"\s+", " ", (text or "").strip()).casefold()


def _bayesian_update(cur: float, tier: SourceTier, step: float) -> float:
    """Bayes 置信度累加(借 mnemopi veracity):conf += (1-conf)·tier_weight·step,封顶 1.0。

    cur 越接近 1 增量越小(饱和);tier 越可信权重越大(委托=1.0,工具=0.5)。
    """
    return min(1.0, cur + (1.0 - cur) * TIER_WEIGHT.get(tier, 0.8) * step)


def _init_confidence(tier: SourceTier) -> float:
    """新条初始置信度 = tier_weight · 0.5(借 mnemopi:新 fact 初始 = weight·0.5)。"""
    return TIER_WEIGHT.get(tier, 0.8) * 0.5


def _embed_items(items: list[KnowledgeItem], embedder: Any) -> None:
    """给每条 KI 的 summary 算向量(原地填 .embedding)。embedder=None 跳过(只走 BM25)。

    v1:逐条 embed_query(N 条 = N 次 API;报告级 N 小,可接受)。批量优化记 backlog。
    """
    if embedder is None:
        return
    for it in items:
        if it.embedding is None and it.summary:
            try:
                it.embedding = embedder.embed_query(it.summary).tolist()
            except Exception:  # noqa: BLE001 - 嵌向量失败不阻断写入(降级只 BM25)
                logger.warning("memory.memorize: 嵌向量失败,降级只 BM25: %s", it.id)


def _link_related(item: KnowledgeItem, store: MemoryStore, scope: Scope) -> None:
    """连图边:找 scope 内 evidence 文件有交集的现有 active 项 → 填 related。

    v1 用文件交集做关联(便宜、有用);语义聚类连边放 consolidate/backlog。
    """
    if not item.evidence:
        return
    files = {e.file for e in item.evidence}
    related: list[str] = []
    for other in store.list_items(scope, kind=item.kind):
        if other.id == item.id:
            continue
        of = {e.file for e in other.evidence} | set(other.blast_radius_files)
        if files & of:
            related.append(other.id)
    item.related = list({*related, *item.related})[:20]  # 去重 + 封顶


def _same_subject(a: KnowledgeItem, b: KnowledgeItem) -> bool:
    """两条 KI 是否"同一主题"(v1 启发式)。

    bug_lesson:优先比 symptom(都非空且相等);symptom 任一为空(常见:CLI/MCP 写的 bug_lesson
    默认只填 root_cause 不填 symptom)→ 回退比首证据文件 + 行号邻近(同文件且行号差
    ≤5 行 = 大概率同 bug)。
    codebase_fact:比 kind_detail + 首证据文件 + 行号邻近。

    symptom 回退是 e2e 暴露的真修复:矛盾检测依赖 _same_subject,而实际写入路径 symptom 常为空,
    不回退会让矛盾对漏判(两条同 bug 不同根因的 bug_lesson symptom 都空 → 判不同主题 → 漏报)。
    行号邻近窗是第二次 e2e 暴露的修正:同文件 ≠ 同 bug(wpa 的 scan.c 前后脚几十个 bug),
    纯文件相等会把不相干两条误判成矛盾对;同 bug 两派诊断的证据锚点通常收在同一处,
    故收紧为"同文件且行号差 ≤ _NEARBY_LINE_WINDOW"。相邻不同 bug 仍可能误报 → 由
    "只标不裁"的人在环兜底(needs_review 只是提示裁决,不自动选边)。
    """
    if a.kind != b.kind:
        return False
    if a.kind == "bug_lesson":
        if a.symptom and b.symptom:
            return _norm(a.symptom) == _norm(b.symptom)
        # symptom 任一为空 → 回退:evidence 同文件且行号邻近(±窗口内)才算同主题。
        return _evidence_nearby(a, b)
    return a.kind_detail == b.kind_detail and _evidence_nearby(a, b)


# 行号邻近窗:evidence-file 回退判定里,两条证据锚点相差不超过多少行仍算"同一主题"。
# 5 行 ≈ 一个典型 if 守卫/日志语句的跨度;同 bug 两派诊断锚同一处,不同 bug 至少隔一个函数体。
_NEARBY_LINE_WINDOW = 5


def _evidence_nearby(a: KnowledgeItem, b: KnowledgeItem) -> bool:
    """两条 KI 的首证据是否同文件且行号邻近(evidence-file 回退的收紧条件)。"""
    if not a.evidence or not b.evidence:
        return False
    ea, eb = a.evidence[0], b.evidence[0]
    return ea.file == eb.file and abs((ea.line or 0) - (eb.line or 0)) <= _NEARBY_LINE_WINDOW


def _same_conclusion(a: KnowledgeItem, b: KnowledgeItem) -> bool:
    """结论是否一致(v1:bug_lesson 比 root_cause;codebase_fact 比 summary)。"""
    if a.kind == "bug_lesson":
        return bool(a.root_cause) and _norm(a.root_cause) == _norm(b.root_cause)
    return _norm(a.summary) == _norm(b.summary)


def _dedup_evidence(ev: list) -> list:
    """证据去重(按 file+line)。"""
    seen, out = set(), []
    for e in ev:
        k = (e.file, e.line)
        if k in seen:
            continue
        seen.add(k)
        out.append(e)
    return out


def _merge_on_remention(new: KnowledgeItem, store: MemoryStore, scope: Scope, step: float) -> KnowledgeItem:
    """处理一条新 KI 与现有记忆的关系:同事实重提→Bayes 合并增强;冲突→只追加(旧条保留可召回)。

    R3.5+(2026-08-06,对标 mem0 v3):**不再写入时 supersede** —— 同主题不同结论不再盖戳作废旧条,
    新旧都 active 并存,由检索侧 decay 排序体现"最新为主、旧作参考"(recall 不再过滤 superseded_by)。
    保留 merge:同 id 重提(同 content_key=同事实)仍合并增强(conf 累加 + evidence 并集 + 留旧 valid_at)。
    _same_subject/_same_conclusion 不再在写路调,留给检索侧去重分组(将来 pull-by-need)。
    返回"该 upsert 的条"(可能是合并后的 new);无 set_invalid 副作用(该原语只留手动 invalidate 用)。
    """
    existing = store.get(new.id)
    if existing and existing.active:
        # 重提(同 content_key → 同 id):Bayes 合并,留更早 created_at,并集 evidence/related。
        new.confidence = _bayesian_update(existing.confidence, new.source_tier, step)
        new.access_count = existing.access_count
        new.created_at = min(existing.created_at, new.created_at)
        new.valid_at = existing.valid_at
        new.related = list({*existing.related, *new.related})
        new.evidence = _dedup_evidence([*existing.evidence, *new.evidence])
        return new
    # 冲突(同 subject 不同 conclusion):只追加 —— 旧条不动、保持 active,recall 的 decay 排"最新为主"。
    return new


def memorize_items(
    items: list[KnowledgeItem],
    *,
    store: MemoryStore,
    embedder: Any = None,
    step: float = 0.3,
) -> int:
    """把一批已构造好的 KnowledgeItem 写入(嵌向量 + 连边 + 合并 + upsert)。返回写入条数。

    这是 workflow 出口 / memory_memorize 工具的落点。report→KI 的抽取见 extract_items。
    冲突(同主题不同结论)**只追加**不取代 —— 检索侧 decay 排"最新为主"(对标 mem0 v3,2026-08-06)。
    """
    if not items:
        return 0
    _embed_items(items, embedder)
    scope = items[0].scope
    to_upsert: list[KnowledgeItem] = []
    for it in items:
        if it.confidence == 0.0:
            it.confidence = _init_confidence(it.source_tier)
        _link_related(it, store, scope)
        merged = _merge_on_remention(it, store, scope, step)
        # 纠正链:新条声明了 corrects → 回填被纠正旧条的 corrected_by(检索降权用)。
        # corrects 是 transit 指令(agent 说「我纠正了这些旧条」),写入时消费掉,不入库。
        for target_id in getattr(merged, "corrects", None) or []:
            try:
                store.mark_corrected(target_id, corrected_by=merged.id)
            except Exception:  # noqa: BLE001 - 目标不存在/写失败不阻断 memorize(纠正链是降权不是硬门)
                logger.warning("memory.memorize: mark_corrected 失败(target=%s): 可能目标不存在", target_id)
        to_upsert.append(merged)
    return store.upsert(to_upsert)


def memorize_report(
    report_text: str,
    *,
    repo: str,
    scope: Scope,
    store: MemoryStore,
    model: BaseChatModel,
    embedder: Any = None,
    commit_sha: str | None = None,
    source: str = "",
    source_tier: SourceTier = SourceTier.inferred,
    step: float = 0.3,
) -> int:
    """从一份报告文本抽取并写入记忆(extract + memorize_items 的组合便捷入口)。"""
    from hyperion.services.memory.backends.native.extract import extract_items

    items = extract_items(
        report_text, repo=repo, scope=scope, commit_sha=commit_sha,
        source=source, source_tier=source_tier, model=model,
    )
    return memorize_items(items, store=store, embedder=embedder, step=step)
