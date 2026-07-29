"""native 后端 · LLM 知识抽取(R1 backends/native/extract.py)。

干什么
----
给一份报告(bug-RCA 报告 / 代码仓调研报告),让 LLM 按 schema 抽出"该沉淀的记忆"——
CodebaseFact / BugLesson。这是 memorize 的第一步,也是 write-time 过滤(只存根因/模式/规则,
不存日志流水)——脏数据进索引后 rerank 也救不回来,所以入口要严。

怎么做
----
LangChain 的 model.with_structured_output(Pydantic):把抽取 schema 绑成模型的强制产出契约
(返回 pydantic 对象,不解析 prose)。模型经工厂 create_chat_model(role="memory_extractor",
走便宜模型)。DeepSeek 走 OpenAI 兼容,structured output 走 function-calling(支持)。

对应:deer-flow deermem 的 LLM extraction + mem0 Algorithm-1 的 extract 阶段。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Literal

from langchain.chat_models import BaseChatModel
from pydantic import BaseModel, Field

from hyperion.services.memory.schema import Evidence, KnowledgeItem, Scope, SourceTier

logger = logging.getLogger(__name__)


# ── LLM 强制产出的 schema(with_structured_output 绑它)──


class _ExtractedEvidence(BaseModel):
    file: str
    line: int | None = None
    snippet: str = ""


class _ExtractedItem(BaseModel):
    """LLM 抽出来的一条候选知识项(还没补 scope/repo/embedding,memorize 再补)。"""

    kind: Literal["codebase_fact", "bug_lesson"] = Field(description="codebase_fact=代码仓调研事实;bug_lesson=bug 根因教训")
    summary: str = Field(description="一句话人读摘要(检索+注入用,要精准、可独立看懂,≤120字)")
    detail: str = ""
    symptom: str = ""
    root_cause: str = ""
    fix_patch: str = ""
    blast_radius_files: list[str] = Field(default_factory=list)
    kind_detail: Literal["module", "symbol", "architecture"] = "module"
    evidence: list[_ExtractedEvidence] = Field(default_factory=list)
    confidence: float = Field(default=0.6, ge=0.0, le=1.0, description="0..1,LLM 自评这条有多可信")


class _ExtractionResult(BaseModel):
    """一次抽取的产出:一组候选知识项(可为空 —— 没料可抽就空)。"""

    items: list[_ExtractedItem] = Field(default_factory=list)


_EXTRACTION_PROMPT = """你在为代码库的"长期记忆"抽取知识。从下面这份报告里,抽出**值得跨会话复用**的知识项
(只抽根因/模式/规则/关键架构事实;**不要**抽日志流水、临时现象、一次性细节)。

两类:
- bug_lesson:一个 bug 的根因 + 修法 + 影响面(报告里"根因""修复""补丁"段落)。
- codebase_fact:一个模块/符号/架构的关键设计事实(报告里"架构""模块说明""关键实现"段落)。

要求:
- summary 要精准、能脱离报告独立看懂(它是检索+注入的核心);≤120 字。
- evidence 尽量锚到 file:line + 原文片段(报告里引用的代码位置)。
- 拿不准要不要抽的,就不抽(宁缺毋滥;脏数据进记忆后救不回来)。
- 没有值得抽的就返回空 items。
"""


# 把 schema 喂进提示词,让模型照着填 —— 不依赖 provider 的 structured-output 支持。
# DeepSeek-v4-pro 思考模式不支持 tool_choice,也不支持 response_format json_schema;
# "提示词喂 Schema + 模型直出 JSON + 手动解析"这条对任何 provider/模型都稳。
_SCHEMA_HINT = json.dumps(_ExtractionResult.model_json_schema(), ensure_ascii=False, indent=2)

_JSON_PROMPT = (
    _EXTRACTION_PROMPT
    + "\n\n输出要求:只输出一个 JSON 对象,严格符合下面的 JSON Schema;不要 markdown 代码围栏、"
    "不要任何解释文字。没有值得抽的就输出 {\"items\": []}。\n\nJSON Schema:\n"
    + _SCHEMA_HINT
)


def _extract_json_object(text: str) -> dict | None:
    """从模型回复里抠 JSON 对象:去 markdown 围栏,取最外层 {…} 再 json.loads。抠不到返 None。"""
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^\s*```(?:json)?\s*", "", t, flags=re.IGNORECASE)  # 去开围栏 ```json
    t = re.sub(r"\s*```\s*$", "", t)  # 去闭围栏
    m = re.search(r"\{.*\}", t, flags=re.S)  # 取最外层 {...}(贪婪、跨行)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _to_ki(
    raw: _ExtractedItem,
    *,
    repo: str,
    scope: Scope,
    commit_sha: str | None,
    source: str,
    source_tier: SourceTier,
) -> KnowledgeItem:
    """把 LLM 抽出的候选 → KnowledgeItem(补 scope/repo/溯源/置信度)。"""
    return KnowledgeItem(
        kind=raw.kind,
        repo=repo,
        scope=scope,
        summary=raw.summary.strip(),
        detail=raw.detail,
        symptom=raw.symptom,
        root_cause=raw.root_cause,
        fix_patch=raw.fix_patch,
        blast_radius_files=raw.blast_radius_files,
        kind_detail=raw.kind_detail,
        commit_sha=commit_sha,
        evidence=[Evidence(file=e.file, line=e.line, snippet=e.snippet) for e in raw.evidence],
        source=source,
        source_tier=source_tier,
        confidence=max(0.0, min(1.0, raw.confidence)),
    )


def extract_items(
    report_text: str,
    *,
    repo: str,
    scope: Scope,
    model: BaseChatModel,
    commit_sha: str | None = None,
    source: str = "",
    source_tier: SourceTier = SourceTier.inferred,
) -> list[KnowledgeItem]:
    """让 LLM 从报告文本抽出知识项(提示词喂 Schema + 模型直出 JSON + 手动解析)。返回 KI 列表(未嵌向量)。

    为什么不用 with_structured_output:DeepSeek-v4-pro 思考模式不支持 tool_choice,
    也不支持 response_format json_schema;改"喂 Schema + 直出 JSON"对所有 provider 都稳。
    report_text 太短/没料 → 直接返 [](不调 LLM 浪费钱)。
    """
    text = (report_text or "").strip()
    if len(text) < 40:  # 太短,没料可抽
        return []
    try:
        msg = model.invoke([
            {"role": "system", "content": _JSON_PROMPT},
            {"role": "user", "content": text},
        ])
        raw = msg.content if isinstance(msg.content, str) else str(msg.content)
        data = _extract_json_object(raw)
        if data is None:
            logger.warning("memory.extract: 模型回复里抠不到 JSON,跳过。原始回复前 200 字: %s", raw[:200])
            return []
        result = _ExtractionResult.model_validate(data)
    except Exception as e:  # noqa: BLE001 - 抽取失败(provider 限流/网络/格式)→ 不崩,返空
        logger.warning("memory.extract: 抽取失败,跳过该报告: %s", e)
        return []
    out = [
        _to_ki(it, repo=repo, scope=scope, commit_sha=commit_sha, source=source, source_tier=source_tier)
        for it in result.items
        if it.summary.strip()
    ]
    logger.debug("memory.extract: 报告 %d 字 → 抽出 %d 条", len(text), len(out))
    return out
