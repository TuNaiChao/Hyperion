"""Hyperion CLI 入口(`uv run hyperion ...`)。

子命令:
  hyperion models                列出 config.yaml 中配置的模型
  hyperion run "问题"            跑一次 demo agent(P0)
  hyperion index <path> [name]   为一个代码仓库建/更新向量索引(P1)
  hyperion tools [--group X]     列出已加载的 agent 工具(验证声明式加载)
  hyperion lsp health|refs ...   L2 精确导航(clangd)自检 / 冒烟(P1.5)
  hyperion memory recall|add|ingest|list|consolidate|invalidate ...   记忆核心(R1)+ 文档摄取(R3.4)
  hyperion mcp serve             MCP server(把记忆暴露给 delegate,R1)
  hyperion bug-rca --repo X --trigger "..."   bug 根因定位 workflow(R2 ★MVP)
"""


from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from hyperion.platform.config import get_app_config


def cmd_models(args) -> int:
    """列出 config.yaml 中配置的模型与角色路由。"""
    cfg = get_app_config()
    if not cfg.models:
        print("(config.yaml 中未配置任何模型)")
        return 1
    for m in cfg.models:
        caps = []
        if m.supports_thinking:
            caps.append("thinking")
        if m.supports_vision:
            caps.append("vision")
        cap_str = f"  [{', '.join(caps)}]" if caps else ""
        print(f"- {m.name:20} {m.use:45}{cap_str}")
    if cfg.model_roles:
        print("\nroles:")
        for role, target in cfg.model_roles.items():
            print(f"  {role:16} -> {target}")
    return 0


def cmd_run(args) -> int:
    """跑一次 demo agent,打印最终回复。

    真正 invoke 会发起 LLM 请求,需要有效 API key(.env)。
    构造时校验 key:无 key 会抛 OpenAIError,这里兜底成可读提示。
    """
    # 延迟导入:run 路径较重(模型工厂 + 工具 + langgraph),只在用到时加载
    from hyperion.platform.agent import run_demo

    try:
        answer = run_demo(args.question, role=args.role, model=args.model)
    except Exception as e:  # noqa: BLE001 - CLI 顶层兜底,要给用户可读提示而非栈
        msg = str(e).lower()
        if any(k in msg for k in ("api_key", "api key", "credential", "missing")):
            print(
                "错误:缺少 LLM API key。请在项目根的 .env 填入(参照 .env.example),\n"
                "      例如 OPENAI_API_KEY=sk-... 或 DEEPSEEK_API_KEY=...",
                file=sys.stderr,
            )
            return 2
        print(f"运行出错:{e}", file=sys.stderr)
        return 1
    print(answer)
    return 0


def cmd_index(args) -> int:
    """为一个代码仓库建/更新向量索引(P1 代码理解服务)。

    用法:hyperion index <repo_path> [repo_name]
    例:hyperion index src/hyperion hyperion
       hyperion index ~/src/bluez bluez --force
    没给 repo_name → 用 repo_path 的目录名。⚠️ repo_name 必须和 config.code_index.repo
    (search_code 查的表名)一致,否则 search_code 会查空表。
    """
    from pathlib import Path

    from hyperion.services.code_index.embed import create_embedder
    from hyperion.services.code_index.index import build_index

    cfg = get_app_config()
    repo_path = Path(args.repo_path)
    if not repo_path.exists():
        print(f"错误:路径不存在: {repo_path}", file=sys.stderr)
        return 1
    repo_name = args.repo_name or repo_path.resolve().name
    vs_path = getattr(getattr(cfg.code_index, "vector_store", None), "path", "data/code_index")

    embedder = create_embedder(cfg.code_index.embedding)
    stats = build_index(repo_path, repo_name, embedder, vs_path, force=args.force)
    n = stats.get("indexed", stats.get("total_chunks", "?"))
    print(f"索引完成 [{stats.get('mode')}]:{repo_name}  {n} chunk  "
          f"commit={stats.get('repo_commit', '-')[:10]}")
    return 0


def cmd_tools(args) -> int:
    """列出 config.yaml 声明、registry 实际加载的 agent 工具(✓ 加载成功 / ✗ 失败)。"""
    from hyperion.tools.registry import get_available_tools

    cfg = get_app_config()
    try:
        tools = get_available_tools()  # 触发反射加载,加载不了会在这抛
    except Exception as e:  # noqa: BLE001 - CLI 顶层兜底
        print(f"错误:工具加载失败: {e}", file=sys.stderr)
        return 1
    loaded = {t.name for t in tools}
    rows = [tc for tc in cfg.tools if not args.group or tc.group == args.group]
    if not rows:
        print(f"(没有 group={args.group} 的工具)" if args.group else "(config.yaml 未声明工具)")
        return 1
    for tc in rows:
        print(f"{'✓' if tc.name in loaded else '✗'} {tc.name:16} [{tc.group}]")
    return 0


def cmd_lsp(args) -> int:
    """L2 精确导航(clangd/LSP)子命令:health 自检 / refs 冒烟。

      hyperion lsp health [repo_root]   检测 clangd + compile_commands 是否就位
      hyperion lsp refs <file> <line> <col> [repo_root]   冒烟:直接打一次 references
    """
    from pathlib import Path

    from hyperion.services.code_index.lsp import get_lsp_server, lsp_health

    cfg = get_app_config()
    repo = Path(args.repo_root).resolve() if args.repo_root else Path(cfg.sandbox.workspace).resolve()

    if args.lsp_cmd == "health":
        h = lsp_health(str(repo))
        print(h.render())
        return 0 if h.ok else 1

    if args.lsp_cmd == "refs":
        fpath = Path(args.file)
        if not fpath.is_file():
            print(f"错误:文件不存在: {args.file}", file=sys.stderr)
            return 1
        try:
            rel = fpath.resolve().relative_to(repo)
        except ValueError:
            rel = fpath
        try:
            sync = get_lsp_server(str(repo))
        except Exception as e:
            print(f"错误:启动 clangd 失败: {e}\n  先跑 `uv run hyperion lsp health`。", file=sys.stderr)
            return 1
        with sync.open_file(str(rel)):
            locs = sync.request_references(str(rel), args.line - 1, args.col - 1)
        if not locs:
            print(f"(无 references:{args.file}:{args.line}:{args.col})")
            return 0
        for loc in locs:
            uri = loc.get("uri", "")
            rng = loc.get("range", {}).get("start", {})
            p = uri[7:] if uri.startswith("file://") else uri
            print(f"{p}:{rng.get('line', 0) + 1}:{rng.get('character', 0) + 1}")
        return 0

    print(f"(未知 lsp 子命令: {args.lsp_cmd})", file=sys.stderr)
    return 1


def _cmd_memory_ingest(args, scope, repo) -> int:
    """`hyperion memory ingest <path>` —— 摄取文档 → 记忆(R3.4)。

      hyperion memory ingest <报告.md> [--kind auto|report|patch] [--source-tier imported|stated|inferred]
      hyperion memory ingest <补丁.patch> [--commit-sha SHA]
    按扩展名自动分流:.md/.txt/.pdf → 报告路(extract + memorize,长文自动分块);
    .patch/.diff → 补丁路(PatchIngestPipeline,retrieve-then-summarize)。
    """
    import asyncio
    from pathlib import Path

    from hyperion.services.memory.ingest import ingest_document
    from hyperion.services.memory.schema import SourceTier

    p = Path(args.path)
    if not p.exists():
        print(f"错误:文件不存在: {p}", file=sys.stderr)
        return 1
    tier_map = {"imported": SourceTier.imported, "stated": SourceTier.stated, "inferred": SourceTier.inferred}
    stier = tier_map.get(args.source_tier or "imported", SourceTier.imported)
    try:
        stats = asyncio.run(ingest_document(
            p, scope=scope, repo=repo, source_tier=stier,
            commit_sha=args.commit_sha, kind=args.kind,
        ))
    except NotImplementedError as e:
        print(f"(该路径暂未实现: {e})", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 - CLI 顶层兜底
        print(f"ingest 出错:{e}", file=sys.stderr)
        return 1

    route = stats.get("route")
    if route == "patch":
        print(f"补丁摄取:{p.name} → 产 {stats.get('items_produced', 0)} 条 → 写入 {stats.get('wrote', 0)} 条(scope={repo})。")
    else:
        warn = f"  ⚠️ {stats['warn']}" if stats.get("warn") else ""
        print(f"报告摄取:{p.name} → {stats.get('chunks', 0)} 块 → 写入 {stats.get('wrote', 0)} 条(scope={repo})。{warn}")
    return 0


def cmd_memory(args) -> int:
    """记忆核心子命令(R1):recall 翻记忆 / add 记一条(或从报告抽)/ list / consolidate / invalidate。

      hyperion memory recall "<query>" [--top-k N] [--repo X]
      hyperion memory add --kind bug_lesson --summary "..." [--file F --line L] [--root-cause "..."]
      hyperion memory add --from-report <报告.md> [--commit-sha SHA]
      hyperion memory ingest <文档或补丁> [--kind auto|report|patch] [--source-tier imported]  # R3.4
      hyperion memory list [--kind K] [--include-invalid]
      hyperion memory consolidate          # 巩固:升级 mental_model
      hyperion memory invalidate <id>
    """
    import asyncio
    from pathlib import Path

    from hyperion.services.memory import get_memory_service
    from hyperion.services.memory.schema import Scope

    cfg = get_app_config()
    repo = args.repo or getattr(cfg.code_index, "repo", None) or Path(cfg.sandbox.workspace).name
    scope = Scope(owner="default", codebase=repo)
    svc = get_memory_service()
    sub = args.memory_cmd

    if sub == "recall":
        hits = asyncio.run(svc.recall(args.query, scope, top_k=args.top_k))
        if not hits:
            print(f"(记忆里没找到与 '{args.query}' 相关的历史教训/事实)")
            return 0
        print(f"检索到 {len(hits)} 条(按相关度降序):")
        for h in hits:
            print(h.render())
        return 0

    if sub == "add":
        if args.from_report:
            text = Path(args.from_report).read_text(encoding="utf-8")
            try:
                n = asyncio.run(svc.memorize_report(
                    text, scope, repo=repo, commit_sha=args.commit_sha, source=args.from_report))
            except NotImplementedError as e:
                print(f"错误:当前 memory 后端不支持从报告抽取: {e}", file=sys.stderr)
                return 2
            print(f"从报告抽出并写入 {n} 条知识项(scope={repo})。")
            return 0

        from hyperion.services.memory.schema import Evidence, KnowledgeItem, SourceTier

        if not args.summary:
            print("错误:直接记一条需要 --summary(或用 --from-report 从报告抽)。", file=sys.stderr)
            return 2
        ev = [Evidence(file=args.file, line=args.line)] if args.file else []
        item = KnowledgeItem(
            kind=args.kind, repo=repo, scope=scope, summary=args.summary,
            root_cause=args.root_cause or "", detail=args.detail or "", evidence=ev,
            source="cli", source_tier=SourceTier.stated,
        )
        n = asyncio.run(svc.memorize([item], scope))
        print(f"已记入(id={item.id}, kind={args.kind}, 合并/新增 {n} 条)。")
        return 0

    if sub == "ingest":
        # 摄取外部文档(bug 报告/调研报告 .md/.txt/.pdf 或补丁 .patch/.diff)→ 记忆(R3.4)。
        return _cmd_memory_ingest(args, scope, repo)

    if sub == "list":
        items = asyncio.run(svc.list_items(scope, kind=args.kind, include_invalid=args.include_invalid))
        if not items:
            print(f"(scope={repo} 无知识项)")
            return 0
        for it in items:
            flag = "" if it.active else "  [失效]"
            ev = f" @{it.evidence[0].file}:{it.evidence[0].line}" if it.evidence else ""
            print(f"- [{it.kind}] {it.summary[:80]}{ev}  conf={it.confidence:.2f} acc={it.access_count}{flag}  ({it.id})")
        return 0

    if sub == "consolidate":
        stats = asyncio.run(svc.consolidate(scope))
        print(f"巩固完成:扫 {stats.get('scanned', 0)},升级 mental_model {stats.get('promoted', 0)}。")
        return 0

    if sub == "invalidate":
        ok = asyncio.run(svc.invalidate(args.id, scope, reason=args.reason or ""))
        print(f"{'已失效' if ok else '未找到/已失效'}: {args.id}")
        return 0 if ok else 1

    print(f"(未知 memory 子命令: {sub})", file=sys.stderr)
    return 1


def cmd_mcp(args) -> int:
    """启动 MCP server(把 Hyperion 能力做成工具给 coding agent 调;stdio 或 http)。

    需 `uv sync --extra mcp`。transport:
      - stdio(默认):agent 拉起子进程 1:1 接入(delegate 老路径 / 本地单机最简)。
      - http:warm 长进程,多 agent 共用,省每 bug 重启加载 ~1.2GB 的 cold-boot(③)。
        先 `hyperion mcp serve --transport http` 跑起来,再把 opencode/codex 指向
        http://<host>:<port>/mcp。
    """
    import os as _os

    from hyperion.platform.config import _default_config_path, get_app_config
    from hyperion.tools.mcp_memory import build_server

    # MCP server 被 opencode 拉起时 cwd 通常是 workspace/code(≠ Hyperion 根)→ config 里相对
    # data/ 路径(memory SQLite / code_index LanceDB)会写进 workspace(污染补丁 + 记忆不持久)。
    # chdir 到 Hyperion 根(config.yaml 所在),让相对路径解析回正轨。MCP server 是独立进程,
    # 工具都用绝对路径/名查(log_path 绝对、codebase 走 env、index 走 repo 表名),不依赖 cwd。
    _os.chdir(_default_config_path().parent.parent)

    # transport 优先级:CLI 标志 > config.mcp.transport > 默认 stdio。http 模式要把 host/port
    # 焊进 FastMCP 构造(run() 不收 host/port,见 mcp_memory.build_server 注释)。
    mcp_cfg = get_app_config().mcp
    transport = (getattr(args, "transport", None) or mcp_cfg.transport).lower()
    http_mode = transport in ("http", "streamable-http", "streamable_http")
    build_kwargs: dict = {}
    if http_mode:
        build_kwargs["host"] = args.host or mcp_cfg.host
        build_kwargs["port"] = args.port or mcp_cfg.port

    try:
        server = build_server(codebase=args.codebase, **build_kwargs)
    except ImportError as e:
        print(f"错误:MCP 依赖未装。装它: uv sync --extra mcp\n  ({e})", file=sys.stderr)
        return 2

    if http_mode:
        print(f"Hyperion MCP(streamable-http)→ http://{build_kwargs['host']}:{build_kwargs['port']}/mcp"
              f"  (Ctrl-C 停;opencode/codex 指过来即可)", file=sys.stderr)
        server.run(transport="streamable-http")  # 阻塞:uvicorn 服务
    else:
        server.run()  # 阻塞:stdio 循环
    return 0


def cmd_bug_rca(args) -> int:
    """bug-RCA workflow(R3.1 ★MVP):输入 repo + trigger(和/或 log)→ 报告 + 补丁。

      hyperion bug-rca --repo <path> --trigger "<线索>" [--log <日志文件>]
    六步:ingest→recall_lessons→[delegate_localize_loop(opencode 自定位 + MCP 工具)]→assemble_repair
         →[delegate_repair_loop]→report+memorize。真调 opencode(delegate),较慢(数分钟)。

    ⚠️ post-pivot(2026-08-06)参考路径:bug-RCA 主路径已改成 opencode + bug-rca skill +
    hyperion MCP 工具(见 docs/设计/harness-pivot-design.md)。本命令保留向后兼容,仍可用。
    """
    import asyncio

    from hyperion.workflows.bug_rca.graph import run

    print("⚠️ 提示:bug-RCA 主路径已转向 opencode + bug-rca skill + hyperion MCP 工具"
          "(tool+skill server / 领域 harness)。本 orchestrator 命令保留向后兼容,仍可跑。"
          "见 docs/设计/harness-pivot-design.md。", file=sys.stderr)

    if not args.trigger and not args.log:
        print("错误:至少给 --trigger(线索)或 --log(日志文件)之一。", file=sys.stderr)
        return 2
    try:
        final = asyncio.run(run(args.repo, args.trigger, log_path=args.log))
    except Exception as e:  # noqa: BLE001 - CLI 顶层兜底
        print(f"bug-RCA 运行出错:{e}", file=sys.stderr)
        return 1
    print(f"报告:{final.get('report_path', '-')}")
    print(f"补丁:{final.get('patch_path', '-')}")
    print(f"验证:{final.get('verified', False)}")
    return 0


def cmd_research(args) -> int:
    """deep_research workflow(R3.2 P1):输入 repo → 架构/模块调研报告 + CodebaseFact 入记忆。

      hyperion research --repo <path> --codebase <name> [--owner <owner>]
    六步:ingest→index(code_index + CRG)→plan(社区)→research(每模块 ReAct 子 agent)
         →report(§5 + Verifier)→memorize(CodebaseFact)。真调模型建子 agent,较慢(数分钟)。
    """
    import asyncio

    from hyperion.workflows.deep_research.graph import run

    try:
        final = asyncio.run(run(args.repo, codebase=args.codebase, owner=args.owner))
    except Exception as e:  # noqa: BLE001 - CLI 顶层兜底
        print(f"deep_research 运行出错:{e}", file=sys.stderr)
        return 1
    print(f"报告:{final.get('report_path', '-')}")
    print(f"CodebaseFact 入记忆:{final.get('facts_memorized', 0)} 条")
    return 0


def cmd_patch_report(args) -> int:
    """patch_report workflow(P-A 1b):一组 PR → fetch → 逐 PR 分析 → 跨 PR 聚合 → cited 报告。

      hyperion patch-report --prs <url...> --repo <path> --codebase <name> [--deep] [--concurrency 3]
    ingest→fetch_prs→analyze(validate+CRG risk+cited-reporter)→aggregate(分桶+LLM 综合)
         →report(cited+Verifier)→memorize(codebase_fact)。
    诚实:CRG 图需先 `hyperion index` 建;GitHub 匿名限速(建议配 GITHUB_TOKEN)。
    """
    import asyncio

    from hyperion.workflows.patch_report.graph import run

    try:
        final = asyncio.run(run(args.prs, repo=args.repo, codebase=args.codebase,
                                owner=getattr(args, "owner", "default"),
                                deep=getattr(args, "deep", False),
                                concurrency=getattr(args, "concurrency", 3)))
    except Exception as e:  # noqa: BLE001 - CLI 顶层兜底
        print(f"patch_report 运行出错:{e}", file=sys.stderr)
        return 1
    print(f"报告:{final.get('report_path', '-')}")
    agg = (final.get("aggregate") or {}).get("stats") or {}
    print(f"PRs:{agg.get('total_prs', 0)} · high_security={agg.get('high_security_count', 0)} · "
          f"CodebaseFact 入记忆:{final.get('facts_memorized', 0)} 条")
    return 0


def main(argv: list[str] | None = None) -> int:
    # 把 .env 读进环境变量;必须在任何 config/$VAR 解析之前
    load_dotenv()

    parser = argparse.ArgumentParser(prog="hyperion", description="Hyperion agent")
    sub = parser.add_subparsers(dest="cmd")

    sub_models = sub.add_parser("models", help="列出 config.yaml 中配置的模型")
    sub_models.set_defaults(func=cmd_models)

    sub_run = sub.add_parser("run", help="跑一次 demo agent")
    sub_run.add_argument("question", help="要问 agent 的问题")
    sub_run.add_argument("--model", default=None, help="指定模型名(默认走 role 路由)")
    sub_run.add_argument("--role", default="default", help="模型角色(默认 default)")
    sub_run.set_defaults(func=cmd_run)

    sub_index = sub.add_parser("index", help="为一个仓库建/更新向量索引")
    sub_index.add_argument("repo_path", help="仓库根目录路径")
    sub_index.add_argument("repo_name", nargs="?", default=None,
                           help="索引名(默认取目录名;须与 code_index.repo 一致)")
    sub_index.add_argument("--force", action="store_true", help="强制全量重建")
    sub_index.set_defaults(func=cmd_index)

    sub_tools = sub.add_parser("tools", help="列出已加载的 agent 工具")
    sub_tools.add_argument("--group", default=None, help="只看某 group(如 code / file:read / sandbox)")
    sub_tools.set_defaults(func=cmd_tools)

    sub_lsp = sub.add_parser("lsp", help="L2 精确导航(clangd):health 自检 / refs 冒烟")
    sub_lsp_sub = sub_lsp.add_subparsers(dest="lsp_cmd", required=True)
    sub_lsp_health = sub_lsp_sub.add_parser("health", help="检测 clangd + compile_commands 是否就位")
    sub_lsp_health.add_argument("repo_root", nargs="?", default=None, help="仓库根(默认 workspace)")
    sub_lsp_refs = sub_lsp_sub.add_parser("refs", help="冒烟:打一次 references")
    sub_lsp_refs.add_argument("file", help="文件路径")
    sub_lsp_refs.add_argument("line", type=int, help="行号(1-based)")
    sub_lsp_refs.add_argument("col", type=int, help="列号(1-based)")
    sub_lsp_refs.add_argument("repo_root", nargs="?", default=None, help="仓库根(默认 workspace)")
    sub_lsp.set_defaults(func=cmd_lsp)

    sub_memory = sub.add_parser("memory", help="记忆核心:recall/add/list/consolidate/invalidate")
    sub_memory_sub = sub_memory.add_subparsers(dest="memory_cmd", required=True)
    m_recall = sub_memory_sub.add_parser("recall", help="翻记忆(多路召回)")
    m_recall.add_argument("query", help="自然语言查询")
    m_recall.add_argument("--top-k", type=int, default=5)
    m_recall.add_argument("--repo", default=None, help="代码库(默认 config.code_index.repo)")
    m_add = sub_memory_sub.add_parser("add", help="记一条(或 --from-report 从报告抽)")
    m_add.add_argument("--kind", default="bug_lesson", choices=["bug_lesson", "codebase_fact"])
    m_add.add_argument("--summary", default=None, help="一句话摘要(直接记时必填)")
    m_add.add_argument("--root-cause", default="")
    m_add.add_argument("--detail", default="")
    m_add.add_argument("--file", default=None)
    m_add.add_argument("--line", type=int, default=None)
    m_add.add_argument("--from-report", default=None, help="从报告文件抽(走 LLM extract)")
    m_add.add_argument("--commit-sha", default=None)
    m_add.add_argument("--repo", default=None)
    m_ingest = sub_memory_sub.add_parser("ingest", help="摄取文档(bug 报告/调研报告/补丁)→ 记忆(R3.4)")
    m_ingest.add_argument("path", help="文档路径(.md/.txt/.pdf/.patch/.diff)")
    m_ingest.add_argument("--kind", default="auto", choices=["auto", "report", "patch"],
                          help="auto=按扩展名判定(补丁走 retrieve-then-summarize,报告走 extract)")
    m_ingest.add_argument("--source-tier", default="imported", choices=["imported", "stated", "inferred"],
                          help="来源可信度(默认 imported)")
    m_ingest.add_argument("--commit-sha", default=None)
    m_ingest.add_argument("--repo", default=None)
    m_list = sub_memory_sub.add_parser("list", help="列知识项")
    m_list.add_argument("--kind", default=None)
    m_list.add_argument("--include-invalid", action="store_true")
    m_list.add_argument("--repo", default=None)
    m_consol = sub_memory_sub.add_parser("consolidate", help="巩固(升级 mental_model)")
    m_consol.add_argument("--repo", default=None)
    m_inv = sub_memory_sub.add_parser("invalidate", help="失效一条")
    m_inv.add_argument("id")
    m_inv.add_argument("--reason", default="")
    m_inv.add_argument("--repo", default=None)
    sub_memory.set_defaults(func=cmd_memory)

    sub_mcp = sub.add_parser("mcp", help="MCP server(把 Hyperion 能力做成工具给 coding agent 调)")
    sub_mcp_sub = sub_mcp.add_subparsers(dest="mcp_cmd", required=True)
    mcp_serve = sub_mcp_sub.add_parser("serve", help="启动 MCP server(stdio 默认;--transport http 起 warm 长进程)")
    mcp_serve.add_argument("--codebase", default=None, help="查哪个代码库的索引/记忆(= 建索引时的 name);默认 config.code_index.repo")
    mcp_serve.add_argument("--transport", default=None, choices=["stdio", "http"], help="stdio(默认,子进程 1:1)| http(streamable-http,warm 多客户端,解 cold-boot)")
    mcp_serve.add_argument("--host", default=None, help="http 绑定地址(默认 127.0.0.1;config.mcp.host)")
    mcp_serve.add_argument("--port", type=int, default=None, help="http 端口(默认 8765;config.mcp.port)")
    sub_mcp.set_defaults(func=cmd_mcp)

    sub_bug = sub.add_parser("bug-rca", help="bug 根因定位 workflow(★MVP,委托 opencode)")
    sub_bug.add_argument("--repo", required=True, help="仓库根目录")
    sub_bug.add_argument("--trigger", default=None, help="bug 线索(日志摘要/问题描述/漏洞关键句);纯日志驱动可省")
    sub_bug.add_argument("--log", default=None, help="原始日志文件路径(交给 opencode 用 grep/awk 按时间窗切)")
    sub_bug.set_defaults(func=cmd_bug_rca)

    sub_res = sub.add_parser("research", help="代码仓深度调研 workflow(P1,产架构/模块报告 + CodebaseFact)")
    sub_res.add_argument("--repo", required=True, help="仓库根目录")
    sub_res.add_argument("--codebase", required=True, help="仓库名(= 建索引 name;CRG db / 记忆 scope.codebase 用)")
    sub_res.add_argument("--owner", default="default", help="记忆 scope.owner(默认 default;多用户 R4)")
    sub_res.set_defaults(func=cmd_research)

    sub_pr = sub.add_parser("patch-report", help="P-A 1b 批量 PR 聚合报告(一组 PR → 跨 PR 质量/安全/功能报告)")
    sub_pr.add_argument("--prs", required=True, nargs="+",
                        help="PR URL 列表(GitHub github.com/.../pull/N;Gerrit 同接口)")
    sub_pr.add_argument("--repo", required=True, help="代码仓根(CRG 图 + validate_patch 用;需先 hyperion index)")
    sub_pr.add_argument("--codebase", required=True, help="仓库名(CRG db / 记忆 scope.codebase)")
    sub_pr.add_argument("--owner", default="default", help="记忆 scope.owner(默认 default)")
    sub_pr.add_argument("--deep", action="store_true", help="高风险/security 子集走 ReAct 深审(默认 light)")
    sub_pr.add_argument("--concurrency", type=int, default=3, help="并发抓取/分析(默认 3,GitHub 限速友好)")
    sub_pr.set_defaults(func=cmd_patch_report)

    args = parser.parse_args(argv)
    if getattr(args, "func", None):
        return args.func(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
