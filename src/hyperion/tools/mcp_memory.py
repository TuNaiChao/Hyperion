"""Hyperion MCP server —— 把 Hyperion 的差异化能力做成工具,给 delegate(opencode)现场调。

不是"MCP 驱动 delegate",而是"delegate 查 Hyperion":opencode 干活时经 MCP 调本服务暴露的
工具(见 bug-rca-design.md §6 反向 MCP)。四个工具:
  - memory_recall     翻长期记忆(历史 bug 教训 / 代码库事实),带 file:line 溯源。
  - memory_memorize   写一条记忆(ad-hoc;报告/补丁走 workflow 自动记)。
  - search_codebase   语义+符号检索代码,**只回索引里真实存在的符号**(emit-concept 防幻觉)。
  - filter_logs       大日志按 关键字∩时间窗 过滤成有界摘录。

防幻觉契约(§6.1 search_codebase):模型传一个**概念/自然语言**(不是猜的文件名/函数名),
工具从**真实索引**里检索 → 只回**索引中确实存在**的 file:symbol:line。因为结果来自实际索引,
模型拿不到一个编造的文件路径 —— 幻觉在结构上不可能。这正是 2026 主流(Claude Code 弃向量库
改 agentic search / Cursor codebase indexing):agent 发概念,工具回验过的真实符号。

入口:`hyperion mcp serve [--codebase NAME]`(stdio transport)。需 `uv sync --extra mcp`。
--codebase:查哪个代码库的索引/记忆(= LanceDB 表名 + memory scope);不传则按
            config.code_index.repo → 进程 cwd 目录名 兜底(opencode 常在项目根拉起 MCP)。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from hyperion.platform.config import get_app_config
from hyperion.services.memory.schema import Scope


def _resolve_codebase(explicit: str | None) -> str:
    """定查哪个代码库:--codebase > HYPERION_CODEBASE env > config.code_index.repo > cwd 目录名。

    HYPERION_CODEBASE 由 delegate(opencode 父进程)注入、opencode 透传给 MCP 子进程
    (local server 的 environment 字段不展开 {env:},靠进程 env 继承 —— 2026-08-03 源码核实)。
    """
    import os
    if explicit:
        return explicit
    env_cb = os.environ.get("HYPERION_CODEBASE")
    if env_cb:
        return env_cb
    cfg = get_app_config()
    repo = getattr(cfg.code_index, "repo", None)
    if repo:
        return repo
    return Path.cwd().name


def build_server(codebase: str | None = None):
    """构造 FastMCP server,暴露四个工具给 delegate(opencode)。

    codebase 在此一次解析,烘焙进各工具闭包(工具内不再各自猜仓名)。
    server 名 "hyperion" —— opencode 按 `<server>_<tool>` 给工具加前缀(如 hyperion_search_codebase)。
    """
    from mcp.server.fastmcp import FastMCP

    from hyperion.services.memory import get_memory_service

    repo = _resolve_codebase(codebase)
    scope = Scope(owner="default", codebase=repo)
    mcp = FastMCP("hyperion")
    svc = get_memory_service()

    # ── ① memory_recall:翻长期记忆(R1 已有,这里薄封一层 scope)────────────
    @mcp.tool()
    async def memory_recall(query: str, top_k: int = 5) -> str:
        """Recall from Hyperion's long-term memory: historical bug lessons / codebase facts
        relevant to the query, each with file:line provenance + confidence + recency.

        Call this BEFORE localizing/patching to reuse prior root-causes/fixes for this codebase.
        """
        hits = await svc.recall(query, scope, top_k=top_k)
        if not hits:
            return f"No memory found for '{query}' (codebase={repo})."
        out = [f"Recalled {len(hits)} (by relevance, codebase={repo}):"]
        out += [h.render() for h in hits]
        return "\n".join(out)

    # ── ② memory_memorize:写一条记忆(报告/补丁走 workflow 自动记,这是 ad-hoc 入口)──
    @mcp.tool()
    async def memory_memorize(kind: Literal["codebase_fact", "bug_lesson"], summary: str,
                              file: str | None = None, line: int | None = None,
                              root_cause: str = "") -> str:
        """Write one knowledge item into Hyperion's long-term memory (cross-session reuse).

        kind: codebase_fact | bug_lesson. Prefer letting the bug_rca workflow auto-memorize;
        use this only for ad-hoc facts a delegate discovers on-site.
        """
        from hyperion.services.memory.schema import Evidence, KnowledgeItem, SourceTier

        item = KnowledgeItem(
            kind=kind, repo=repo, scope=scope, summary=summary, root_cause=root_cause,
            evidence=([Evidence(file=file, line=line)] if file else []),
            source="mcp", source_tier=SourceTier.delegate,
        )
        n = await svc.memorize([item], scope)
        return f"memorized id={item.id} kind={kind} ({n} merged/added)"

    # ── ③ search_codebase:语义+符号检索(防幻觉:只回索引里真实存在的符号)──────
    @mcp.tool()
    async def search_codebase(query: str, top_k: int = 5) -> str:
        """Semantic + symbol search over this codebase's index (BM25 + vector + RRF + rerank).

        Pass a CONCEPT / natural-language query (e.g. "p2p scan result routing", "radio work
        lifecycle free"), NOT a guessed file/function name. Returns ONLY symbols that REALLY EXIST
        in the indexed codebase — each with file:line + symbol + score + first line. Because the
        result comes straight from the actual index, you cannot be handed a hallucinated path.

        Cheaper + more precise than grepping the whole tree by hand. Needs the codebase indexed
        (`uv run hyperion index <path> <name>`); returns a "not indexed" hint otherwise.
        """
        from hyperion.services.code_index.retrieval import retrieve
        from hyperion.tools.code_nav import _retrieval_bundle

        try:
            embedder, store, reranker = _retrieval_bundle()  # 复用 code_nav 的单例(embedder/store/reranker)
        except Exception as e:  # noqa: BLE001 —— 依赖没装好给可操作错误串,不抛崩整个 server
            return f"search_codebase 初始化失败(检查 config.code_index / .env): {e}"

        try:
            if store.count(repo) == 0:  # 表不存在或为空
                return (f"代码库 '{repo}' 还没建索引(表空)。先建:"
                        f"`uv run hyperion index <仓库路径> {repo}`。")
        except Exception:
            return (f"代码库 '{repo}' 还没建索引。先建:"
                    f"`uv run hyperion index <仓库路径> {repo}`。")

        try:
            result = retrieve(query, repo, embedder, store, reranker, top_k=top_k)
        except Exception as e:  # noqa: BLE001
            return f"检索失败: {e}"

        if not result.hits:
            return f"未找到与 '{query}' 相关的代码(检索路径 {result.out_mode},codebase={repo})。"
        out = [f"检索路径 {result.out_mode} · top-{len(result.hits)}(均为索引内真实符号,codebase={repo})"]
        for h in result.hits:
            first = h.text.splitlines()[0][:120] if h.text.splitlines() else ""
            out.append(f"\n{h.file}:{h.start_line}-{h.end_line}  ({h.kind} {h.symbol})  score={h.score:.3f}\n  {first}")
        return "\n".join(out)

    # ── ④ filter_logs:大日志 关键字∩时间窗 → 有界摘录(省 token)──────────────
    @mcp.tool()
    async def filter_logs(log_path: str, keywords: list[str] | None = None,
                          since: str | None = None, until: str | None = None,
                          max_lines: int = 400) -> str:
        """Filter a large log file down to the relevant lines (keywords AND time-window), capped.

        Pass the log file path + the failure time window (HH:MM:SS, read from the issue) to get a
        surgical excerpt instead of grepping ~16k lines by hand (slow + token-heavy). The full log
        stays on disk at log_path for you to read directly if this excerpt is too narrow.

        keywords: ANDed with the time window. ⚠️ for runtime logs, issue-derived keywords are often
        CODE symbols that don't substring-match log prose — prefer the time window alone for logs;
        reserve keywords for when you know they are log vocabulary.
        """
        from hyperion.services.trigger_parser.log_filter import filter_log_window

        p = Path(log_path)
        if not p.is_file():
            return f"日志文件不存在: {log_path}"
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return f"读日志失败: {e}"
        excerpt = filter_log_window(text, keywords, since=since, until=until, max_lines=max_lines)
        if not excerpt:
            return f"过滤后 0 行(调整 keywords/since/until;全量日志仍在 {log_path})。"
        n = len(excerpt.splitlines())
        return f"过滤出 {n} 行(上限 {max_lines};全量日志仍在 {log_path}):\n\n{excerpt}"

    return mcp


def main() -> None:
    """MCP server 入口(stdio)。`hyperion mcp serve` 或 `python -m hyperion.tools.mcp_memory` 调。"""
    build_server().run()


if __name__ == "__main__":
    main()
