"""memory MCP server —— 把记忆暴露给 delegate(委托, 指omp/opencode)现场查(R1)。

不是"MCP 驱动 delegate",而是"delegate 查 Hyperion":omp/opencode 干活时经 MCP 调
memory_recall 翻 Hyperion 累积的历史教训/事实(见 bug-rca-design.md §6 / memory-design.md §7c)。

用官方 mcp 包的 FastMCP(stdio transport)。需 `uv sync --extra mcp`。
启动:`hyperion mcp serve`(批次 7 加 CLI)或 `python -m hyperion.tools.mcp_memory`。
delegate 端在 .mcp.json 配本命令即可接入。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from hyperion.platform.config import get_app_config
from hyperion.services.memory.schema import Scope


def _scope() -> Scope:
    cfg = get_app_config()
    repo = getattr(cfg.code_index, "repo", None) or Path(cfg.sandbox.workspace).name
    return Scope(owner="default", codebase=repo)


def build_server():
    """构造 FastMCP server,暴露 memory_recall / memory_memorize(给 delegate 用)。"""
    from mcp.server.fastmcp import FastMCP

    from hyperion.services.memory import get_memory_service

    mcp = FastMCP("hyperion-memory")
    svc = get_memory_service()

    @mcp.tool()
    async def memory_recall(query: str, top_k: int = 5) -> str:
        """Recall from Hyperion's long-term memory: historical bug lessons / codebase facts
        relevant to the query, each with file:line provenance + confidence + recency.

        Call this BEFORE patching, to reuse prior root-causes/fixes for this codebase.
        """
        hits = await svc.recall(query, _scope(), top_k=top_k)
        if not hits:
            return f"No memory found for '{query}'."
        out = [f"Recalled {len(hits)} (by relevance):"]
        out += [h.render() for h in hits]
        return "\n".join(out)

    @mcp.tool()
    async def memory_memorize(kind: Literal["codebase_fact", "bug_lesson"], summary: str, file: str | None = None,
                              line: int | None = None, root_cause: str = "") -> str:
        """Write one knowledge item into Hyperion's long-term memory (cross-session reuse).

        kind: codebase_fact | bug_lesson. Prefer letting the bug_rca workflow auto-memorize;
        use this only for ad-hoc facts a delegate discovers on-site.
        """
        from hyperion.services.memory.schema import Evidence, KnowledgeItem, SourceTier

        scope = _scope()
        item = KnowledgeItem(
            kind=kind, repo=scope.codebase, scope=scope, summary=summary,
            root_cause=root_cause,
            evidence=([Evidence(file=file, line=line)] if file else []),
            source="mcp", source_tier=SourceTier.delegate,
        )
        n = await svc.memorize([item], scope)
        return f"memorized id={item.id} kind={kind} ({n} merged/added)"

    return mcp


def main() -> None:
    """MCP server 入口(stdio)。`hyperion mcp serve` 或 `python -m hyperion.tools.mcp_memory` 调。"""
    build_server().run()


if __name__ == "__main__":
    main()
