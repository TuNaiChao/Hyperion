"""Hyperion MCP server —— 把 Hyperion 的差异化能力做成工具,给 delegate(opencode)现场调。

不是"MCP 驱动 delegate",而是"delegate 查 Hyperion":opencode 干活时经 MCP 调本服务暴露的
工具(见 bug-rca-design.md §6 反向 MCP)。一组工具(harness 转向:精炼工具面,只做 coding agent
做不好/做不了的 —— 记忆/代码情报/影响面/补丁验证/补丁落盘/报告落盘;定位推理+改代码+日志切片都留给 opencode 的 read/grep/awk):
  - memory_recall     翻长期记忆(历史 bug 教训 / 代码库事实),带 file:line 溯源。
  - memory_memorize   写一条记忆(ad-hoc;报告/补丁走 workflow 自动记)。
  - search_codebase   语义+符号检索代码,**只回索引里真实存在的符号**(emit-concept 防幻觉)。
  - blast_radius      改动影响面(结构图 BFS:改这些文件会波及谁;harness 转向 D0)。
  - call_chain        符号中心的 N 跳调用链(仅 CALLS 边)+ PageRank 重要度(谁调它/它调谁;P1.5 caller/callee 进适配层)。
  - repo_map          全仓 PageRank 排名符号地图(Aider repomap 式,塞进 token 预算;俯瞰「哪些函数结构上最核心」;#38)。
  - cross_version_diff 同仓两 git ref 跨版本对比(base..head 提交门 + concern diff + 触及函数 + cherry 等价;feature 2b;git 为核图可选)。
  - merge_eval         上游 commit 合入评估(逐 commit 三态:已修/建议合/冲突;patch-id 等价 + apply 检查;低优#1;git 为核图可选,全程 local-git)。
  - validate_patch    补丁能否干净 apply(`git apply --check`,执行硬门零 LLM;harness 转向 D0)。
  - export_patch      把补丁落盘成 .patch 文件(交付硬门 —— 聊天不算交付;空 diff 自检;harness 转向 D1)。
  - export_report     把分析报告落盘成 .md 文件(交付硬门 —— 报告跟补丁一样要上盘;空内容自检;harness 转向 D1)。

防幻觉契约(§6.1 search_codebase):模型传一个**概念/自然语言**(不是猜的文件名/函数名),
工具从**真实索引**里检索 → 只回**索引中确实存在**的 file:symbol:line。因为结果来自实际索引,
模型拿不到一个编造的文件路径 —— 幻觉在结构上不可能。这正是 2026 主流(Claude Code 弃向量库
改 agentic search / Cursor codebase indexing):agent 发概念,工具回验过的真实符号。

入口:`hyperion mcp serve [--codebase NAME]`。需 `uv sync --extra mcp`。
  transport:stdio(默认,agent 拉起子进程 1:1)| http(`--transport http`,warm 长进程,
             多 agent 共用,省 cold-boot —— 解 ③;端点 http://host:port/mcp)。
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


def _retrieval_bundle():
    """懒构造 (embedder, store, reranker)——code_index 检索三件套(search_codebase 用)。

    reranker 可能为 None(provider=off)。2026-08-10 撤 code_nav @tool 层时从 tools/code_nav.py
    搬来内联(embedder/store/reranker 工厂自身带缓存,无需额外 lru_cache)。
    """
    from hyperion.services.code_index.embed import create_embedder
    from hyperion.services.code_index.retrieval import create_reranker
    from hyperion.services.code_index.store import LanceDBStore

    cfg = get_app_config()
    embedder = create_embedder(cfg.code_index.embedding)
    vs_cfg = getattr(cfg.code_index, "vector_store", None)
    vs_path = getattr(vs_cfg, "path", "data/code_index") if vs_cfg else "data/code_index"
    store = LanceDBStore(vs_path)
    reranker = create_reranker(getattr(cfg.code_index, "reranker", None))
    return embedder, store, reranker


def build_server(codebase: str | None = None, *, host: str | None = None, port: int | None = None):
    """构造 FastMCP server,暴露十三个 Hyperion 工具给 coding agent(opencode/codex/claude code)。

    codebase 在此解析一次,烘焙进各工具闭包当**默认值**;memory_recall / memory_memorize /
    search_codebase / blast_radius / call_chain / repo_map / cross_version_diff / merge_eval 另接受 per-call `codebase` 参数覆盖此默认(多库:
    同一 server 进程可切多个仓),不传则用这里的默认 repo。
    server 名 "hyperion" —— opencode 按 `<server>_<tool>` 给工具加前缀(如 hyperion_search_codebase)。
    host/port:仅 streamable-http transport 用(FastMCP 在构造时吃这俩 → settings → uvicorn 监听;
       `run()` 不接收 host/port)。stdio 模式忽略。不传 → 用 FastMCP 默认(127.0.0.1:8000)。
    """
    from mcp.server.fastmcp import FastMCP

    from hyperion.services.memory import get_memory_service

    repo = _resolve_codebase(codebase)
    # host/port 只在给定时透传给 FastMCP(stdio 模式用不上,但给了也无害)
    fastmcp_kwargs: dict = {}
    if host is not None:
        fastmcp_kwargs["host"] = host
    if port is not None:
        fastmcp_kwargs["port"] = port
    mcp = FastMCP("hyperion", **fastmcp_kwargs)
    svc = get_memory_service()

    # ── ① memory_recall:翻长期记忆(R1 已有,这里薄封一层 scope)────────────
    @mcp.tool()
    async def memory_recall(query: str, top_k: int = 5, kind: str | None = None,
                            codebase: str | None = None) -> str:
        """Recall from Hyperion's long-term memory: historical bug lessons / codebase facts
        relevant to the query, each with file:line provenance + confidence + recency.

        Call this BEFORE localizing/patching to reuse prior root-causes/fixes for this codebase.
        kind: optional filter — "bug_lesson" returns only past patches/fixes (excludes codebase
              facts + raw code); omit for all kinds. Multiplies fetch then filters, so the kind
              filter won't starve results (absorbs the former patch_search tool).
        codebase: override which codebase's memory to recall from (default = this server's
              codebase). Pass when the bug you're investigating belongs to a different repo than
              the server's default; recall is scope-isolated so it never crosses codebases.
        """
        # per-call codebase 覆盖(模板同 blast_radius 的 `codebase or repo`);不传 = 闭包默认 repo。
        active_repo = codebase or repo
        active_scope = Scope(owner="default", codebase=active_repo)
        # 给了 kind → 多取再按 kind 过滤(留余量,对齐原 patch_search 的做法);否则按 top_k 直取。
        fetch_k = max(top_k * 3, top_k) if kind else top_k
        hits = await svc.recall(query, active_scope, top_k=fetch_k)
        if kind:
            hits = [h for h in hits if (h.kind or "") == kind][:top_k]
        if not hits:
            tag = f", kind={kind}" if kind else ""
            return f"No memory found for '{query}' (codebase={active_repo}{tag})."
        tag = f", kind={kind}" if kind else ""
        out = [f"Recalled {len(hits)} (by relevance, codebase={active_repo}{tag}):"]
        out += [h.render() for h in hits]
        return "\n".join(out)

    # ── ② memory_memorize:写一条记忆(报告/补丁走 workflow 自动记,这是 ad-hoc 入口)──
    @mcp.tool()
    async def memory_memorize(kind: Literal["codebase_fact", "bug_lesson"], summary: str,
                              file: str | None = None, line: int | None = None,
                              root_cause: str = "",
                              fix_patch: str = "",
                              symptom: str = "",
                              blast_radius_files: list[str] | None = None,
                              commit_sha: str | None = None,
                              tags: list[str] | None = None,
                              codebase: str | None = None) -> str:
        """Write one knowledge item into Hyperion's long-term memory (cross-session reuse).

        kind: codebase_fact | bug_lesson. Prefer letting the bug_rca/patch_review flow auto-memorize;
        use this only for ad-hoc facts/lessons a delegate discovers on-site.

        For a patch/PR analysis (kind=bug_lesson): pass fix_patch (the unified diff). The item is then
        content-addressed by the PATCH text (not the summary), so re-memorizing the same patch MERGES
        (confidence bump) instead of duplicating. Pair with blast_radius_files + commit_sha + tags
        (e.g. ["patch_insight"]) so the lesson is searchable and provenance-traceable. Put your
        verdict (intent / correctness / merge recommendation) in summary + root_cause.
        codebase: override which codebase's memory to write into (default = this server's codebase).
              Pass when the lesson belongs to a different repo than the server's default; the item is
              scoped (id namespaced + filtered) by this codebase, so it won't pollute others.
        """
        from hyperion.services.memory.schema import Evidence, KnowledgeItem, SourceTier, make_id

        # per-call codebase 覆盖(模板同 blast_radius);不传 = 闭包默认 repo/scope。
        active_repo = codebase or repo
        active_scope = Scope(owner="default", codebase=active_repo)
        blast_radius_files = blast_radius_files or []
        tags = tags or []
        # 给了 fix_patch → id 按补丁内容算(对齐 ingest.py:415),同补丁重复 memorize 走合并而非新增。
        kid = make_id(active_scope, kind, fix_patch) if fix_patch else ""

        item = KnowledgeItem(
            id=kid,
            kind=kind, repo=active_repo, scope=active_scope, summary=summary, root_cause=root_cause,
            symptom=symptom, fix_patch=fix_patch,
            blast_radius_files=list(dict.fromkeys(blast_radius_files)),
            commit_sha=commit_sha, tags=tags,
            evidence=([Evidence(file=file, line=line)] if file else []),
            source="mcp", source_tier=SourceTier.delegate,
        )
        n = await svc.memorize([item], active_scope)
        return f"memorized id={item.id} kind={kind} codebase={active_repo} ({n} merged/added)"

    # ── ③ search_codebase:语义+符号检索(防幻觉:只回索引里真实存在的符号)──────
    @mcp.tool()
    async def search_codebase(query: str, top_k: int = 5, codebase: str | None = None) -> str:
        """Semantic + symbol search over this codebase's index (BM25 + vector + RRF + rerank).

        Pass a CONCEPT / natural-language query (e.g. "p2p scan result routing", "radio work
        lifecycle free"), NOT a guessed file/function name. Returns ONLY symbols that REALLY EXIST
        in the indexed codebase — each with file:line + symbol + score + first line. Because the
        result comes straight from the actual index, you cannot be handed a hallucinated path.

        Cheaper + more precise than grepping the whole tree by hand. Needs the codebase indexed
        (`uv run hyperion index <path> <name>`); returns a "not indexed" hint otherwise.
        codebase: override which codebase's index to search (default = this server's codebase).
              Pass when the code you're looking for lives in a different repo than the server's
              default; the index is table-per-repo, so each codebase is searched in isolation.
        """
        from hyperion.services.code_index.retrieval import retrieve
        # per-call codebase 覆盖(模板同 blast_radius);不传 = 闭包默认 repo。
        active_repo = codebase or repo
        try:
            embedder, store, reranker = _retrieval_bundle()  # 模块级检索单例(embedder/store/reranker)
        except Exception as e:  # noqa: BLE001 —— 依赖没装好给可操作错误串,不抛崩整个 server
            return f"search_codebase 初始化失败(检查 config.code_index / .env): {e}"

        try:
            if store.count(active_repo) == 0:  # 表不存在或为空
                return (f"代码库 '{active_repo}' 还没建索引(表空)。先建:"
                        f"`uv run hyperion index <仓库路径> {active_repo}`。")
        except Exception:
            return (f"代码库 '{active_repo}' 还没建索引。先建:"
                    f"`uv run hyperion index <仓库路径> {active_repo}`。")

        try:
            result = retrieve(query, active_repo, embedder, store, reranker, top_k=top_k)
        except Exception as e:  # noqa: BLE001
            return f"检索失败: {e}"

        if not result.hits:
            return f"未找到与 '{query}' 相关的代码(检索路径 {result.out_mode},codebase={active_repo})。"
        out = [f"检索路径 {result.out_mode} · top-{len(result.hits)}(均为索引内真实符号,codebase={active_repo})"]
        for h in result.hits:
            first = h.text.splitlines()[0][:120] if h.text.splitlines() else ""
            out.append(f"\n{h.file}:{h.start_line}-{h.end_line}  ({h.kind} {h.symbol})  score={h.score:.3f}\n  {first}")
        return "\n".join(out)

    # ── ⑤ blast_radius:改动影响面(结构图 BFS —— 改这些文件会波及谁)──────────
    # harness 转向:把 CodeGraph.impact_radius 暴露成工具,让 agent 改代码前查"动了这些会断哪"。
    @mcp.tool()
    async def blast_radius(changed_files: list[str], codebase: str | None = None) -> str:
        """Structural blast-radius: given a set of changed files, return what else gets hit
        (callers / callees / dependents via code-graph BFS) — the "if I touch these, what breaks" view.

        Pass the file paths a patch/PR modifies. Graph-driven, no LLM. Needs the codebase graph built
        (`uv run hyperion index <path> <name>`); returns a "not built" hint otherwise.
        codebase: override which codebase's graph (default = this server's codebase).
        """
        try:
            from hyperion.services.code_index.code_graph import CodeGraph
        except Exception as e:  # noqa: BLE001 —— code-review-graph 未装给可操作提示
            return (f"blast_radius 不可用:结构图后端未装。装它: `uv sync --extra code-review-graph`\n  ({e})")
        target = codebase or repo
        if not changed_files:
            return "未传 changed_files(传会被改动的文件路径列表)。"
        try:
            cg = CodeGraph.open(target)
            result = cg.impact_radius(list(changed_files))
        except FileNotFoundError:
            return (f"代码库 '{target}' 的结构图未建(data/structgraph/{target}/graph.db 不在)。"
                    f"先建:`uv run hyperion index <仓库路径> {target}`。")
        except Exception as e:  # noqa: BLE001
            return f"算影响面失败({target}): {e}"
        import json
        body = json.dumps(result, ensure_ascii=False, default=str)
        return f"blast-radius(codebase={target},输入 {len(changed_files)} 文件):\n{body[:8000]}"

    # ── ⑤b call_chain:符号中心的 N 跳调用链(仅 CALLS 边 + PageRank;P1.5 caller/callee 进适配层)
    # 和 blast_radius 互补:blast_radius = 文件种子·全边·「改这些会波及谁」(blast);
    # call_chain = 符号种子·仅 CALLS 边·「这个函数的调用上下文 + 谁结构上重要」(chain)。
    # bug-RCA / 调研里 agent 定位根因、判断改动影响时最想要的「调用链」视图;图驱动,零 LLM。
    @mcp.tool()
    async def call_chain(symbol: str, direction: str = "both", depth: int = 2,
                         top_n: int = 15, codebase: str | None = None) -> str:
        """Call chain for a function: who calls it / what it calls (N hops along CALL edges only),
        each node ranked by PageRank importance.

        Pass a function/method name (bare like ``wpa_supplicant_init`` or qualified
        ``wpa_supplicant.c::wpa_supplicant_init``). Returns the N-hop caller/callee subtree along CALL
        edges only, each node with file:line, hop count, and a PageRank score (a function called by many
        important functions scores higher). Use it to understand a function's call context when localizing
        a root cause or assessing a change — "how does execution reach here, and which callers matter".

        Complement to blast_radius: blast_radius is file-seed + all-edge "what breaks if I touch these";
        call_chain is symbol-seed + CALLS-only "who calls / is called by this function, ranked".
        direction: callers (who calls it) / callees (what it calls) / both (default).
        depth:     hop count (default 2, capped at 5 to bound large graphs).
        top_n:     max nodes per direction after sorting (hop asc, pagerank desc); default 15.
        codebase:  override which codebase's graph (default = this server's codebase).
        Needs the codebase graph built; returns a "not built" hint otherwise.
        """
        try:
            from hyperion.services.code_index.code_graph import CodeGraph
        except Exception as e:  # noqa: BLE001 —— code-review-graph 未装给可操作提示
            return (f"call_chain 不可用:结构图后端未装。装它: `uv sync --extra code-review-graph`\n  ({e})")
        target = codebase or repo
        try:
            cg = CodeGraph.open(target)
            result = cg.call_chain(symbol, direction=direction, depth=depth, top_n=top_n)
        except FileNotFoundError:
            return (f"代码库 '{target}' 的结构图未建(data/structgraph/{target}/graph.db 不在)。"
                    f"先建:`uv run hyperion index <仓库路径> {target}`。")
        except ValueError as e:  # symbol 解析不到 / direction 非法 → 友好串,不抛
            return f"call_chain 没法算({target}, symbol={symbol}): {e}"
        except Exception as e:  # noqa: BLE001
            return f"算调用链失败({target}, symbol={symbol}): {e}"
        import json
        body = json.dumps(result, ensure_ascii=False, default=str)
        return (f"call-chain(codebase={target}, symbol={symbol}, direction={direction}, "
                f"depth={depth}):\n{body[:8000]}")

    # ── ⑤c cross_version_diff:跨版本对比(同仓两 git ref 间;git 为核,图可选富化)────
    # 回答「旧版本(base)→ 新版本(head),我关心的 concern 改了啥 / 修了没」:
    # base..head 提交门 + concern 的 git diff + (有图)触及函数 + git cherry 等价摘要。
    # 确定性事实,零 LLM(「修没修」判断归 agent)。比 blast_radius/call_chain 强:没图也能跑 git 核。
    @mcp.tool()
    async def cross_version_diff(base_ref: str, head_ref: str, repo_path: str,
                                 concern_files: list[str] | None = None,
                                 concern_symbols: list[str] | None = None,
                                 top_commits: int = 30,
                                 codebase: str | None = None) -> str:
        """Cross-version diff between two git refs of the same repo: what changed base..head,
        especially around your concern. Returns intervening commits (deterministic gate),
        the concern's git diff (so you can read the fix), optional touched-functions (graph),
        and a git-cherry patch-equivalence summary. Deterministic, no LLM — the 'is it fixed /
        how to port' judgment is yours, using this output + search_codebase + call_chain.

        base_ref/head_ref: two git refs in the SAME repo (e.g. '5.50'/'5.85', or 'HEAD~5'/'HEAD').
        repo_path: absolute path to the repo working tree (cwd for git). concern_files/symbols:
        scope to these (symbols resolved via the graph if available). top_commits: commit cap.
        codebase: override which codebase's graph is used for enrichment (default = server's).
        Needs only the git repo; graph is optional enrichment (runs git core even without it).
        """
        from hyperion.services.code_index.code_graph import CodeGraph
        from hyperion.services.code_index.code_graph import cross_version_diff as _cvd
        target = codebase or repo
        # 图可选:开得到就富化,开不到(未建 / CRG 未装)→ None,git 核照跑
        graph = None
        try:
            graph = CodeGraph.open(target)
        except Exception:  # noqa: BLE001 —— FileNotFoundError/ImportError 都降级,不致命
            pass
        try:
            result = _cvd(base_ref, head_ref, repo_path=repo_path, concern_files=concern_files,
                          concern_symbols=concern_symbols, graph=graph, top_commits=top_commits)
        except ValueError as e:  # 坏 ref / 非 git 仓 / repo_path 无效 → 友好串,不抛
            return f"cross_version_diff 没法算(repo={repo_path}, {base_ref}..{head_ref}): {e}"
        except Exception as e:  # noqa: BLE001
            return f"跨版本对比失败(repo={repo_path}, {base_ref}..{head_ref}): {e}"
        import json
        body = json.dumps(result, ensure_ascii=False, default=str)
        return (f"cross-version-diff(repo={repo_path}, codebase={target}, "
                f"{base_ref}..{head_ref}):\n{body[:8000]}")

    # ── ⑤e merge_eval:上游 commit 合入评估(逐 commit 三态:已修/建议合/冲突;git 为核图可选)
    # 维护 fork 时,把上游一段 commit 范围逐个评估「该不该合入」:patch-id 等价(已修,git --cherry-mark)
    # + 能否干净 apply(冲突)+ 触及文件/函数(CRG 可选)。确定性地板;「相不相关」归 agent(CRG 查)。
    # 全程 local-git:上游须先 fetch 进本仓让 ref 可见(agent 跑 git remote add + fetch)。
    @mcp.tool()
    async def merge_eval(upstream_base_ref: str, upstream_head_ref: str, fork_ref: str,
                         repo_path: str, concern_files: list[str] | None = None,
                         max_commits: int = 50, codebase: str | None = None) -> str:
        """Upstream-commit merge evaluation: for each commit in an upstream range, decide whether to
        backport it into the fork. Deterministic per-commit tri-state (no LLM): already_fixed (a git
        patch-id-equivalent commit already exists in the fork, via git --cherry-mark), recommend_merge
        (not in fork, applies cleanly), conflict (not in fork, apply fails), uncertain. Also returns
        touched files/functions.

        This is the deterministic FLOOR — the 'is the fork actually affected / does it need this fix'
        relevance judgment is YOURS, using touched files/functions + search_codebase + call_chain
        ('can apply' != 'fork needs it'; a fork may lack the bug/feature entirely).

        Fully local-git. YOU must first fetch the upstream into the repo so the refs resolve:
        `git -C <repo> remote add upstream <url> && git -C <repo> fetch upstream --no-tags` (idempotent;
        your job, not this tool's). Before calling, `git -C <repo> checkout <fork_ref>` and ensure a
        clean worktree — applies_cleanly checks apply against the CURRENT worktree.

        upstream_base_ref/upstream_head_ref: upstream commit range (two git refs in repo_path, e.g.
            last-sync-point and upstream/master). fork_ref: fork branch to compare against (e.g. release/eagle).
        repo_path: absolute path of the repo working tree (cwd for git). concern_files: scope to commits
            touching these files. max_commits: scan cap (default 50). codebase: graph for touched-function
            enrichment (optional; default = this server's codebase).
        Needs only the git repo; graph is optional enrichment (runs without it).
        """
        from hyperion.services.code_index.code_graph import CodeGraph
        from hyperion.services.code_index.code_graph import merge_eval as _me
        target = codebase or repo
        # 图可选:开得到就富化,开不到(未建 / CRG 未装)→ None,git 核照跑
        graph = None
        try:
            graph = CodeGraph.open(target)
        except Exception:  # noqa: BLE001 —— FileNotFoundError/ImportError 都降级,不致命
            pass
        try:
            result = _me(upstream_base_ref, upstream_head_ref, fork_ref=fork_ref, repo_path=repo_path,
                         concern_files=concern_files, max_commits=max_commits, graph=graph)
        except ValueError as e:  # 坏 ref / 非 git 仓 / repo_path 无效 → 友好串,不抛
            return (f"merge_eval 没法算(repo={repo_path}, fork={fork_ref}, "
                    f"{upstream_base_ref}..{upstream_head_ref}): {e}")
        except Exception as e:  # noqa: BLE001
            return (f"合入评估失败(repo={repo_path}, fork={fork_ref}, "
                    f"{upstream_base_ref}..{upstream_head_ref}): {e}")
        import json
        body = json.dumps(result, ensure_ascii=False, default=str)
        s = result.get("summary", {})
        return (f"merge-eval(repo={repo_path}, fork={fork_ref}, codebase={target}, "
                f"{upstream_base_ref}..{upstream_head_ref}): "
                f"total={s.get('total', 0)} | already_fixed={s.get('already_fixed', 0)} "
                f"| recommend_merge={s.get('recommend_merge', 0)} | conflict={s.get('conflict', 0)} "
                f"| uncertain={s.get('uncertain', 0)}\n{body[:8000]}")

    # ── ⑤d repo_map:PageRank 排名的全仓符号地图(Aider repomap 式;#38)──────────
    # 和 call_chain 互补:call_chain = 一个符号的调用上下文(手电筒照一条路);
    # repo_map = 全仓最重要符号俯瞰图(卫星图),委托前给 agent 全局视角 / 调研「关键模块」骨架。
    @mcp.tool()
    async def repo_map(map_tokens: int = 2048, codebase: str | None = None) -> str:
        """Whole-repo symbol map ranked by PageRank importance (Aider-style repo map), packed into a token budget.

        Returns a bird's-eye view of which functions are structurally most central across the WHOLE repo
        (not one symbol's neighborhood — that's call_chain). Runs PageRank over the full call graph: a
        function called by many important functions ranks higher (= a core hub). Top symbols are greedily
        packed into ``map_tokens`` (default 2048), grouped by file into a tree. Use it to give yourself a
        global view before localizing a root cause, or as the 'key modules' skeleton for a research report.

        Complement to call_chain: call_chain is one symbol's call context (flashlight down one path);
        repo_map is the whole-repo importance overview (satellite map). Also distinct from hub_nodes
        (degree-based top-15 flat list) — repo_map is PageRank (centrality) based, larger, and tree-grouped.
        map_tokens: token budget for the map (default 2048).
        codebase:   override which codebase's graph (default = this server's codebase).
        Needs the codebase graph built; returns a 'not built' hint otherwise.
        """
        try:
            from hyperion.services.code_index.code_graph import CodeGraph
        except Exception as e:  # noqa: BLE001 —— code-review-graph 未装给可操作提示
            return (f"repo_map 不可用:结构图后端未装。装它: `uv sync --extra code-review-graph`\n  ({e})")
        target = codebase or repo
        try:
            cg = CodeGraph.open(target)
            result = cg.repo_map(map_tokens=map_tokens)
        except FileNotFoundError:
            return (f"代码库 '{target}' 的结构图未建(data/structgraph/{target}/graph.db 不在)。"
                    f"先建:`uv run hyperion index <仓库路径> {target}`。")
        except Exception as e:  # noqa: BLE001
            return f"算仓库地图失败({target}): {e}"
        import json
        body = json.dumps(result, ensure_ascii=False, default=str)
        return (f"repo-map(codebase={target}, map_tokens={map_tokens}):"
                f" {result.get('n_symbols', 0)} symbols / {result.get('n_files', 0)} files"
                f"{' (truncated by budget)' if result.get('truncated') else ''}\n{body[:8000]}")

    # ── ⑥ validate_patch:补丁能否干净 apply(执行硬门,零 LLM)────────────────
    # harness 转向:把 validate_patch 暴露成工具,agent 改完/拿到 PR diff 后过这道硬门再信。
    @mcp.tool()
    async def validate_patch(patch: str, repo_path: str) -> str:
        """Execution gate (non-LLM): does this unified-diff patch apply cleanly to the repo working tree?

        Runs `git apply --check` forward (strict → --3way → patch -p1 fallback) — a deterministic hard
        gate before trusting a patch. Returns applies + method + git diagnostic. Use it to confirm a
        patch/PR you're about to merge, or a fix you just wrote, actually fits the target repo.
        repo_path: absolute path of the repo working tree to check against. (No reverse check here —
        that needs the already-patched tree; the bug_rca workflow has the full forward+reverse validate.)
        """
        from pathlib import Path

        from hyperion.services.workspace.validate import validate_patch as _validate

        if not Path(repo_path).is_dir():
            return f"repo_path 不是目录: {repo_path}"
        try:
            r = _validate(patch, forward_dir=repo_path)  # reverse_dir=None:本工具只 forward --check
        except Exception as e:  # noqa: BLE001
            return f"validate_patch 执行失败: {e}"
        applies = bool(r.get("verified"))
        method = r.get("forward_method")
        log = (r.get("log") or "").strip()[-600:]
        flag = "✅ 能干净 apply" if applies else "❌ apply 失败(路径/格式/context 不匹配)"
        return f"{flag}\nmethod={method}  applies={applies}\n诊断:\n{log}"

    # ── ⑦ export_patch:把补丁落盘成 .patch 文件(交付硬门 —— 聊天回复不算交付)────────
    # bug-RCA 跑完,agent 的改动若只在聊天里 = 没交付。这步把 git diff 写成磁盘文件,且自检
    # 非空(治"agent 改错树 / 假装改完"——纯 bash `git diff > file` 会静默吞掉空 diff,2026 调研:
    # deer-flow 用结构化 present_files tool + 事后交付验证,正是治这个)。格式 unified diff(git diff),
    # 对齐整条管线(validate 用 git apply / ingest 解析 unified diff / report 渲染 ```diff);不污染 repo
    # (无需建 commit —— format-patch 留生产级迭代)。落 data/bug_rca/<repo>.patch(最新一份快照,
    # 同 bug_rca workflow 约定;同仓重跑覆盖,历史在记忆库)。
    # apply 验证**不在这做** —— forward --check 对"已改过的树"必失败(见 validate.py:context 已变,
    # 反向 --check 才证必要);那是 validate_patch(第⑥步,对干净树)的活。export 只保证"有非空 diff 落盘"。
    @mcp.tool()
    async def export_patch(repo_path: str, out_dir: str = "data/bug_rca") -> str:
        """Finalize your fix as an on-disk .patch file — a bug-RCA run is NOT complete until the
        patch is on disk (chat is not a deliverable).

        Captures ALL your uncommitted changes in repo_path (``git add -A && git diff --cached``,
        including new files), writes the unified diff to ``<out_dir>/<repo-name>.patch``, and REFUSES
        to write an empty diff — catches "edited the wrong tree / changes not saved / gitignored",
        failures a bare ``git diff > file`` silently swallows. Run ``validate_patch`` first to confirm
        the diff applies; this tool only guarantees a non-empty patch lands on disk at the canonical path.

        repo_path: absolute path of the repo whose working tree holds your fix.
        out_dir:   output directory (default ``data/bug_rca`` = "latest snapshot" location, matching
                   the bug_rca workflow convention; created if missing).
        """
        import subprocess
        from pathlib import Path

        repo = Path(repo_path)
        if not repo.is_dir():
            return f"repo_path 不是目录: {repo_path}"

        def _git(args: list[str]) -> tuple[int, str, str]:
            p = subprocess.run(
                ["git", "-C", str(repo), *args],
                capture_output=True, text=True, timeout=60,
            )
            return p.returncode, p.stdout, p.stderr

        try:
            rc, _, err = _git(["rev-parse", "--is-inside-work-tree"])
            if rc != 0:
                return f"repo_path 不是 git 工作树: {(err or '').strip()[-300:]}"
            # git add -A 再 diff --cached:含新增文件(对齐 bug_rca workflow 的 observe 约定)。
            # 副作用:会 stage repo_path 的改动(可 git reset 撤;agent 已在改其工作树,同量级)。
            _git(["add", "-A"])
            rc, diff, err = _git(["diff", "--cached"])
            if rc != 0:
                return f"git diff 失败: {(err or diff).strip()[-300:]}"
        except (OSError, subprocess.SubprocessError) as e:  # noqa: BLE001 —— git 不可用给可操作错误
            return f"export_patch 执行失败(git 不可用?): {e}"

        if not diff.strip():
            return ("❌ 空 diff:git 看不到你的改动。可能改错了树(repo_path 指错)、改动没保存、"
                    "或被 .gitignore 忽略。export_patch 不写空补丁 —— 回去确认你真的改对了文件。")

        repo_name = repo.name
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        patch_path = out / f"{repo_name}.patch"
        patch_path.write_text(diff, encoding="utf-8")
        n = len(diff.splitlines())
        return f"✅ 已落盘\npath={patch_path}\nlines={n}  (unified diff;apply 验证见 validate_patch)"

    # ── ⑨ export_report:把分析报告落盘成 .md 文件(交付硬门 —— 报告跟补丁一样要上盘)────
    # 跟 export_patch 对称:补丁内容是 git 生成的(工具自己 diff),报告内容是 agent 生成的(传 content)。
    # bug-RCA 跑完,agent 若只在聊天里吐报告 = 没交付(跟"只在聊天里说改好了"同理)。这步把报告写成磁盘文件,
    # 自检非空(治"agent 假装写报告 / 传空串糊弄")。落 data/bug_rca/<repo>-rca.md(对齐 orchestrator 的
    # render_report 约定;同仓重跑覆盖,历史在记忆库)。报告是**最终交付物**,排在 memorize 之后写 ——
    # 要含 memorize 返回的 id(证明教训已沉淀),才算完整闭环。
    @mcp.tool()
    async def export_report(content: str, repo_path: str, out_dir: str = "data/bug_rca") -> str:
        """Finalize your analysis report as an on-disk .md file — a bug-RCA run is NOT complete until
        the report is on disk (same deliverable bar as the patch; chat is not a deliverable).

        Writes your markdown report to ``<out_dir>/<repo-name>-rca.md`` and REFUSES empty/trivial
        content — catches "forgot to write a report / passed a placeholder". Write the patch first
        (``export_patch``, step ⑦) AND memorize the lesson (``memorize``, step ⑧) first, then write
        this report so it can cite the on-disk ``.patch`` path and the returned memorize id.

        content:   the full markdown report (root cause + evidence + patch summary + validate result +
                   patch path + memorize id).
        repo_path: absolute path of the repo (used only to derive the report filename).
        out_dir:   output directory (default ``data/bug_rca``; created if missing).
        """
        from pathlib import Path

        if not content or not content.strip():
            return ("❌ 空报告:没传内容(或只传空白)。报告跟补丁一样是交付物 —— 写好根因/证据/补丁要点/"
                    "validate 结果/patch 路径/memorize id 再调。export_report 不写空报告。")
        # 报告落盘不强依赖 git / repo 目录存在(内容自包含),只取 repo_path 的目录名做文件名;
        # 空路径兜底 "report",绝不因 repo_path 小瑕疵挡住报告上盘(交付物宁可落盘)。
        name = Path(repo_path).name if repo_path and repo_path.strip() else ""
        repo_name = name or "report"
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        report_path = out / f"{repo_name}-rca.md"
        report_path.write_text(content, encoding="utf-8")
        n = len(content.splitlines())
        return f"✅ 已落盘\npath={report_path}\nlines={n}  (markdown 报告)"

    # ── ⑩ fetch_patch:PR 链接 → diff + meta(P-A 1a,取快递)────────────────────
    # 给一个 GitHub PR 链接,抓回 diff + title/body/changed_files/merge_commit_sha。opencode 能 curl,
    # 但这里带 token 鉴权(私有/限速)+ 失败重试 + 结构化拆包(踩坑#2 辩护:agent 通用 curl 不知 token/remotes)。
    @mcp.tool()
    async def fetch_patch(url: str) -> str:
        """Fetch a GitHub PR's diff + metadata (title/body/changed_files/merge_commit_sha).

        Give a PR URL (github.com/<owner>/<repo>/pull/<num>). Returns the unified diff plus PR metadata
        so you can then ``validate_patch`` / assess it. Uses GITHUB_TOKEN if set
        (private repos / rate limits). Network errors / 404 / non-GitHub URL → friendly error string.
        """
        from hyperion.services.patch.fetcher import from_config

        try:
            art = await from_config().fetch(url)
        except Exception as e:  # noqa: BLE001 - 网络错/404/非 GitHub URL 给可操作串,不崩整个调用
            return f"fetch_patch 失败({url}): {e}"
        meta = (f"title: {art.title}\nmerge_commit_sha: {art.merge_commit_sha}\n"
                f"changed_files({len(art.changed_files)}): {', '.join(art.changed_files[:20])}")
        if art.body:
            meta += f"\nbody: {art.body[:500]}"
        return f"source={art.source_kind}  url={art.url}\n{meta}\n\n--- diff ---\n{art.diff}"

    # ── ⑪ ensure_repo:本地没有 → auto-clone(P-A 1a,借样机)────────────────────
    # 鉴定要一台"样机"(代码仓)。本地没有 → 按 config.patch.git.remotes 配的地址 clone。
    # 踩坑#2 辩护:opencode 会 git clone,但只去公网;用户的"自定义 git 连接"(内网镜像/SSH)它不知道。
    @mcp.tool()
    async def ensure_repo(name_or_url: str) -> str:
        """Resolve a codebase to a local path, auto-cloning if missing.

        Give a repo name (looked up in ``config.patch.git.remotes``), a git URL, or an existing local
        path. Returns the local absolute path; reuses an existing clone in ``data/repos/<name>``
        (idempotent — won't re-clone). Use before ``validate_patch`` when the repo
        isn't already local.
        """
        from hyperion.services.repos.resolver import ensure_repo as _ensure

        try:
            path, cloned = _ensure(name_or_url)
        except Exception as e:  # noqa: BLE001 - clone 失败(认证/不存在/网络)给可操作串,不崩
            return f"ensure_repo 失败({name_or_url}): {e}"
        tag = "新 clone" if cloned else "命中本地(未 clone)"
        return f"✅ repo_path={path}  ({tag})"

    return mcp


def main() -> None:
    """MCP server 入口(stdio 默认)。`hyperion mcp serve` 或 `python -m hyperion.tools.mcp_memory` 调。

    http(streamable-http)模式走 CLI `hyperion mcp serve --transport http`(cmd_mcp 里建带 host/port 的 server)。
    """
    build_server().run()


if __name__ == "__main__":
    main()
