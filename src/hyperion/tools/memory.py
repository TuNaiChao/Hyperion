"""memory 工具 —— agent 主动查/写记忆(@tool 包装,deer-flow tool 模式)。

两个工具(对应 memory-design.md §7b):
  - memory_recall   :按 query 翻记忆(多路召回 → top-k),返每条 摘要+溯源+置信。
  - memory_memorize :手动记一条知识项。

约定同 tools/code_nav.py:@tool(parse_docstring=True)、首参 description、重依赖懒导入。
scope = (owner='default', codebase=<config.code_index.repo 或 workspace 名>),与 search_code 同源。

async 说明:recall/memorize 是 async(ABC 契约);CLI/sync 工具上下文无运行中 loop,
直接 asyncio.run 即可(LangGraph sync invoke 不起 loop;async invoke 把 sync 工具丢线程池,
线程内也无 loop)。MCP 走 async 详见 tools/mcp_memory.py。
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
from pathlib import Path

from langchain_core.tools import tool

from hyperion.platform.config import get_app_config
from hyperion.services.memory.schema import Scope


def _scope() -> Scope:
    """当前记忆 scope:v1 单 owner='default';codebase 取 config.code_index.repo 或 workspace 名(与 search_code 一致)。"""
    cfg = get_app_config()
    repo = getattr(cfg.code_index, "repo", None) or Path(cfg.sandbox.workspace).name
    return Scope(owner="default", codebase=repo)


@lru_cache(maxsize=1)
def _svc():
    """懒取记忆单例(get_memory_service);只建一次。"""
    from hyperion.services.memory import get_memory_service

    return get_memory_service()


@tool("memory_recall", parse_docstring=True)
def memory_recall_tool(description: str, query: str, top_k: int = 5) -> str:
    """翻 Hyperion 的长期记忆:按 query 多路召回(语义+结构)→ top-k 历史 bug 教训/代码事实。
    每条带 溯源(file:line)+ 置信度 + 时效。新会话先翻它,省推导、省 token。

    Args:
        description: 为什么要翻记忆(简短)。
        query: 自然语言查询,如"WiFi 扫描挂起"或"radio work 机制"。
        top_k: 返回几条(默认 5)。
    """
    try:
        hits = asyncio.run(_svc().recall(query, _scope(), top_k=top_k))
    except Exception as e:  # noqa: BLE001 - 工具层兜底,给可操作错误串而非栈
        return f"错误:记忆召回失败(检查 config.memory / .env): {e}"
    if not hits:
        return f"记忆里没找到与 '{query}' 相关的历史教训/事实。"
    out = [f"检索到 {len(hits)} 条(按相关度降序):"]
    out += [h.render() for h in hits]
    return "\n".join(out)


@tool("memory_memorize", parse_docstring=True)
def memory_memorize_tool(description: str, kind: str, summary: str,
                         repo: str | None = None, file: str | None = None, line: int | None = None,
                         root_cause: str = "", detail: str = "") -> str:
    """手动记一条知识到长期记忆(跨会话复用)。优先用 bug_rca/deep_research workflow 自动沉淀;
    这个工具是 agent 主动记的补充。

    Args:
        description: 为什么要记(简短)。
        kind: codebase_fact(代码事实)| bug_lesson(bug 教训)。
        summary: 一句话人读摘要(检索+注入核心,要精准、能独立看懂)。
        repo: 代码库标识;不填用当前 scope 的 codebase。
        file: 证据文件(相对仓根);不填则无证据锚点(不推荐)。
        line: 证据行号(1-based)。
        root_cause: bug_lesson 的根因(kind=bug_lesson 时填)。
        detail: 展开正文(可选)。
    """
    from hyperion.services.memory.schema import Evidence, KnowledgeItem, SourceTier

    if kind not in ("codebase_fact", "bug_lesson"):
        return f"错误:kind 必须是 codebase_fact 或 bug_lesson,得到 {kind!r}"
    scope = _scope()
    item = KnowledgeItem(
        kind=kind, repo=repo or scope.codebase, scope=scope,
        summary=summary, detail=detail, root_cause=root_cause,
        evidence=([Evidence(file=file, line=line)] if file else []),
        source="memory_memorize_tool", source_tier=SourceTier.stated,
    )
    try:
        n = asyncio.run(_svc().memorize([item], scope))
    except Exception as e:  # noqa: BLE001
        return f"错误:记忆写入失败: {e}"
    return f"已记入记忆(id={item.id}, kind={kind}, 合并/新增 {n} 条)。"
