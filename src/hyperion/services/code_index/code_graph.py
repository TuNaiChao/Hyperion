"""code_index · 结构图服务(R3.2):把 code-review-graph(CRG)包成深度调研用的「结构真相源」。

这一层干什么(面向小白)
------------------------
深度调研要回答"这系统怎么组成的、哪些是核心模块、哪里耦合过紧"。靠 LLM 瞎猜不靠谱,
得有**结构真相**——谁调用谁、谁是被大量依赖的枢纽、模块怎么聚类成社区。CRG(code-review-graph)
就是干这个的:tree-sitter 解析全仓 → SQLite 存"函数/类/调用/继承"图 → Leiden 社区检测 +
hub/bridge 分析。本文件把这套散在 CRG 三四个模块里的 API 包成几个调研直接能调的方法。

为什么自己包一层(而不让调研代码直接调 CRG)
- CRG 的建图分散成 full_build → detect_communities → store_communities 三步,查询又散在
  communities / analysis / graph 三个模块;调研代码不该每次重写这套编排。
- 统一错误处理:CRG 是可选 extra(`uv sync --extra code-review-graph`),没装时给清晰指引
  而不是一坨 import 报错。
- db 落点统一在 data/structgraph/<repo>/graph.db(Hyperion 自管,不污染 repo 目录)。

设计取舍
- **进程内 import**(免 MCP server、免 compile_commands;tree-sitter 即可)—— 与 memory 的
  structural.py 同源(都吃 code_review_graph 库),只是各用不同子模块:本文件用 incremental /
  communities / analysis;structural.py 用 tools.query 的 callers/callees。两者 db 路径目前各自
  独立(本文件走 data/structgraph/,structural 走 CRG 默认 .code-review-graph/),将来 memory
  recall 想复用同一张图再对齐路径(记 backlog)。
- **igraph 是 CRG 的可选 extra**:装了走 Leiden 社区(质量高),没装 CRG 内部静默降级成文件聚类
  (质量次但能用)。本层不强制要求 igraph。
- **非 git 仓也能建图**:full_build 优先 git ls-files,失败则回落到目录遍历(example/demo2/wpa
  不是 git 仓也能用)。
"""

from __future__ import annotations

import importlib.util
import logging
import os
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


_SAFE_GIT_REF = re.compile(r"^[A-Za-z0-9_.~^/@{}\-]+$")  # 防 git ref 注入(同 CRG changes.py)
_GIT_TIMEOUT = int(os.environ.get("HYPERION_GIT_TIMEOUT", "30"))  # 跨版本 git 命令超时(秒)


def _require_crg() -> None:
    """CRG 是否已装;没装给清晰指引(用 find_spec 探测,不触发真 import 报错)。"""
    if importlib.util.find_spec("code_review_graph") is None:
        raise ImportError(
            "CRG 结构图服务需要 code-review-graph。装它: uv sync --extra code-review-graph"
        )


def _pagerank(graph) -> dict:
    """CALLS 子图上的 PageRank —— 被越多重要函数调用 → 分越高,标识「结构上关键的函数」。

    分层降级取稳健(不强加 scipy 这种重依赖):
      1. 优先 ``nx.pagerank``(networkx 3.x 默认走 scipy 稀疏矩阵,大图快、省内存);
      2. scipy 没装(本机常见)→ 降级 ``_pagerank_python_pure``(自实现纯 python 幂迭代,
         直接吃邻接表、不建稠密矩阵,故**不 OOM**;大图慢但正确,小图瞬间)。
         —— 不再 import networkx 私有的 ``_pagerank_python``(下划线私有 API,静态分析报
         「未知导入符号」,且跨版本无保证),自实现等价算法,行为一致。

    两者都返 ``{node: score}``;无边(空图 / 种子孤立)→ ``{}``(调用方按 0.0 兜底)。
    """
    import networkx as nx

    if graph.number_of_edges() == 0:
        return {}
    try:
        return nx.pagerank(graph)
    except ModuleNotFoundError:
        return _pagerank_python_pure(graph)


def _pagerank_python_pure(graph, *, alpha: float = 0.85, max_iter: int = 200,
                          tol: float = 1.0e-6) -> dict:
    """纯 python PageRank(幂迭代)—— scipy 缺时的降级实现,不建稀疏/稠密矩阵(大图不 OOM)。

    标准 power iteration:dangling 节点(无出边的函数)的 rank 均匀回流给全网,保证
    概率和恒为 1。与 networkx ``pagerank`` 同算法、同语义(返 ``{node: score}``,和为 1),
    仅实现更朴素 —— 大图比 scipy 慢,但无重依赖、不 OOM,小图瞬间。

    参数(都有库默认,通常不用传):
      - ``alpha``:阻尼系数(默认 0.85,业界标准);
      - ``max_iter``:最大迭代轮(默认 200,足够收敛);
      - ``tol``:L1 收敛阈值(默认 1e-6)。
    """
    nodes = list(graph)
    n = len(nodes)
    if n == 0:
        return {}
    outdeg = {v: graph.out_degree(v) for v in nodes}  # 出度(dangling 节点 = 0)
    rank = {v: 1.0 / n for v in nodes}
    for _ in range(max_iter):
        dangling = sum(rank[v] for v in nodes if outdeg[v] == 0)  # dangling 总分,均匀回流全网
        new_rank = {}
        base = (1.0 - alpha) / n + alpha * dangling / n  # 每个节点的基础分(随机跳转 + dangling 回流)
        for v in nodes:
            s = base
            for u in graph.predecessors(v):  # u→v:u 把 rank/outdeg[u] 分给 v
                s += alpha * rank[u] / outdeg[u]
            new_rank[v] = s
        # L1 收敛:总分差 < tol 就停(不跑满 max_iter)
        if sum(abs(new_rank[v] - rank[v]) for v in nodes) < tol:
            rank = new_rank
            break
        rank = new_rank
    return rank


def _resolve_concern_files(graph, symbols: list[str]) -> set[str]:
    """把 concern 符号(bare 名或 qualified)解析成图里的文件路径集合。

    复用 call_chain 同款解析:精确匹配 > bare 名(``qn.split('::')[-1] == sym``)。
    解析不到的符号静默跳过(上层 note 汇总)。只调一次 ``_build_networkx_graph``,
    多符号共用,省去反复建图。
    """
    nxg = graph._store._build_networkx_graph()
    files: set[str] = set()
    qns: set[str] = set()
    for sym in symbols:
        if sym in nxg:
            qns.add(sym)
        else:
            hits = [n for n in nxg if str(n).split("::")[-1] == sym]
            if hits:
                qns.add(hits[0])  # 多个同名取首个(bare 名歧义上层 note 提示)
    for nd in graph._store._batch_get_nodes(qns):
        if nd.file_path:
            files.add(nd.file_path)
    return files


def cross_version_diff(base_ref: str, head_ref: str, *, repo_path: str,
                       concern_files: list[str] | None = None,
                       concern_symbols: list[str] | None = None,
                       graph: CodeGraph | None = None,
                       top_commits: int = 30, max_diff_chars: int = 8000) -> dict:
    """跨版本对比 —— 同一个代码仓的两个 git ref(tag/commit/branch)之间,回答
    「旧版本(base)→ 新版本(head)之间,我关心的 concern 改了啥 / 修了没」。

    给 agent 的**确定性事实**(零 LLM):「修没修 / 怎么移植」的判断由 agent 综合本工具产出 +
    search_codebase + call_chain 做出 —— 本工具只管把 git 层的事实喂准。这是 harness 转向后的
    分工:工具出事实,判断归 agent(对标 deer-flow「round1 确定性脚本、后续 LLM 解释」)。

    干啥(面向小白)
    ----------------
    想象你修一个 5.50 版本的 bug,想知道 5.85 有没有已经修了、怎么修的。本工具干三件事:
      1) 列出 base..head 之间的提交(尤其触及 concern 的)——「中间到底改了哪些 commit」
         (对标 spec 的确定性门 ``git patch-id``/``git cherry``:MVP 用 git log);
      2) 给 concern 涉及文件的 ``git diff`` 文本——「具体代码怎么变的」,供 agent 读修法;
      3) (有结构图时)把 diff 映射到函数级——「哪些函数被触及了」。
    再附一个 ``git cherry`` 摘要(head 相对 base 的净新补丁数)——「backport 等价」的快速信号。

    为什么用 git 而不是结构 diff 工具(difftastic 之类)
      - 跨版本对比的正主就是 ``git diff/log/cherry``:结构化、零依赖、确定性。
      - difftastic/SemanticDiff 是「查看器」(产 AST 可视化),不做函数级分类、不打补丁,
        对「给 agent 喂事实」不如 git 直接。结构图只做可选的函数级富化。

    base_ref / head_ref:同一仓库的两个 git ref(如 ``"5.50"`` / ``"5.85"``,或 ``"HEAD~5"``/``"HEAD"``)。
    repo_path:仓库工作树的**绝对路径**(跑 git 命令的 cwd)。
    concern_files / concern_symbols:只关心这部分;symbols 会在图里解析成文件(需 ``graph``)。
        都不给则不收窄(全量 commit 列表 + 跳过全量 diff,防回巨大 diff)。
    graph:可选的 CodeGraph(有才做 concern_symbols 解析 + touched_functions 富化)。
    top_commits:commit 列表上限(默认 30)。max_diff_chars:concern_diff 回显上限(默认 8000)。

    返回 ``{refs, commits, commits_truncated, patch_equivalence, concern_diff, touched_functions, note}``。
    失败(非 git 仓 / ref 不存在 / repo_path 无效)→ 抛 ``ValueError``,工具层转友好串。
    """
    repo = Path(repo_path).resolve()
    if not repo.is_dir():
        raise ValueError(f"repo_path 不是目录: {repo_path}")
    for ref in (base_ref, head_ref):
        if not _SAFE_GIT_REF.match(ref):
            raise ValueError(f"非法 git ref(只允许字母数字 _ . / ~ ^ @ {{ }} -): {ref!r}")

    def _git(args: list[str], *, timeout: int = _GIT_TIMEOUT) -> str:
        # 跑一条 git 命令;非 0 退出 → 抛 ValueError(让工具层转友好串,绝不漏 traceback 给 agent)。
        try:
            r = subprocess.run(["git", *args], capture_output=True, stdin=subprocess.DEVNULL,
                               text=True, encoding="utf-8", errors="replace",
                               cwd=str(repo), timeout=timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError(f"git 命令执行失败({' '.join(args[:2])}…): {exc}") from exc
        if r.returncode != 0:
            raise ValueError(f"git 失败(rc={r.returncode}): {r.stderr.strip()[:300]}")
        return r.stdout

    # 1) 解析两 ref → sha(顺带验存在;不存在 git 报错 → 上面的 _git 转 ValueError)
    base_sha = _git(["rev-parse", "--verify", base_ref]).strip()
    head_sha = _git(["rev-parse", "--verify", head_ref]).strip()

    notes: list[str] = []

    # 2) concern 文件清单:显式给的 + symbols 在图里解析出的
    files: set[str] = set(concern_files or [])
    if concern_symbols and graph is not None:
        try:
            files |= _resolve_concern_files(graph, concern_symbols)
        except Exception as exc:  # noqa: BLE001 —— 图解析失败不致命,降级
            notes.append(f"concern_symbols 解析失败,已忽略: {exc}")
    elif concern_symbols and graph is None:
        notes.append("传了 concern_symbols 但没给 graph,无法解析成文件(给 graph 或改传 concern_files)。")

    pathspec = sorted(files) if files else None

    # 3) base..head 的提交(concern 收窄);确定性门(MVP 用 git log,逐 commit patch-id 留迭代)
    log_args = ["log", "--no-merges", "--format=%H%x1f%s", f"--max-count={top_commits}",
                f"{base_ref}..{head_ref}"]
    if pathspec:
        log_args += ["--", *pathspec]
    commits = []
    for line in _git(log_args).splitlines():
        if "\x1f" in line:
            sha, subject = line.split("\x1f", 1)
            commits.append({"sha": sha, "subject": subject})

    # 4) git cherry 摘要:head 相对 base 的净新 / 等价补丁数(backport 等价信号)
    new_in_head = equiv_in_base = 0
    try:
        for line in _git(["cherry", base_ref, head_ref]).splitlines():
            if line.startswith("+"):
                new_in_head += 1
            elif line.startswith("-"):
                equiv_in_base += 1
    except ValueError as exc:
        notes.append(f"git cherry 跳过: {exc}")
    patch_equivalence = {"new_in_head": new_in_head, "equivalent_in_base": equiv_in_base}

    # 5) concern 的 diff 文本(给 agent 读修法);没给 concern → 跳全量 diff(可能巨大)
    if pathspec:
        try:
            concern_diff = _git(["diff", f"{base_ref}..{head_ref}", "--", *pathspec],
                                timeout=max(_GIT_TIMEOUT, 60))[:max_diff_chars]
            if not concern_diff.strip():
                notes.append("concern 文件在 base..head 间无改动。")
        except ValueError as exc:
            concern_diff = ""
            notes.append(f"concern_diff 取失败: {exc}")
    else:
        concern_diff = ""
        notes.append("未给 concern,跳过全量 diff(要 diff 传 concern_files)。")

    # 6) touched_functions(有图才给):把 base..head diff 映射到函数节点
    touched = []
    if graph is not None:
        try:
            from code_review_graph.changes import map_changes_to_nodes, parse_git_diff_ranges
            ranges = parse_git_diff_ranges(str(repo), f"{base_ref}..{head_ref}")
            if pathspec:  # 只关心 concern 文件
                ranges = {f: rs for f, rs in ranges.items() if f in files}
            nodes = map_changes_to_nodes(graph._store, ranges)
            touched = [{"qualified_name": n.qualified_name, "file": n.file_path,
                        "line": n.line_start, "kind": n.kind} for n in nodes]
            notes.append("touched_functions 映射自 base..head diff 的 NEW(head)侧行号 —— "
                         "图须对应 head_ref 才行号对齐。")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"touched_functions 富化失败,已跳过: {exc}")

    return {
        "refs": {"base": base_ref, "head": head_ref, "base_sha": base_sha, "head_sha": head_sha},
        "commits": commits,
        "commits_truncated": len(commits) >= top_commits,
        "patch_equivalence": patch_equivalence,
        "concern_diff": concern_diff,
        "touched_functions": touched,
        "note": " | ".join(notes) if notes else "",
    }


class CodeGraph:
    """一个代码仓的结构图句柄(建一次,查多次)。

    典型用法:
        cg = CodeGraph.build(repo_root="/path/to/wpa", repo_name="wpa")  # 建图(慢,一次性)
        overview = cg.architecture_overview()   # 社区清单 + 跨社区耦合告警
        hubs = cg.hub_nodes(top_n=15)           # 高连接枢纽(被大量调用 / 大量调用)
        bridges = cg.bridge_nodes(top_n=15)     # betweenness 瓶颈(断了多社区失联)
    """

    def __init__(self, store, repo_name: str):
        # store 是 code_review_graph.graph.GraphStore 实例。这里不写死类型注解,避免缺 extra 时
        # 本文件 import 就崩(查询方法内部才 import CRG,与 structural.py 一致的做法)。
        self._store = store
        self.repo_name = repo_name

    # ── 建图 ──────────────────────────────────────────────────────────────

    @classmethod
    def build(
        cls,
        repo_root: str | Path,
        repo_name: str,
        *,
        base_dir: str = "data/structgraph",
        min_community_size: int = 2,
    ) -> CodeGraph:
        """建图(一次性,慢):解析全仓 → 存节点/边 → Leiden 社区检测 → 持久化。

        返回建好、可直接查询的 CodeGraph。db 落 <base_dir>/<repo_name>/graph.db。
        """
        _require_crg()
        from code_review_graph.communities import detect_communities, store_communities
        from code_review_graph.graph import GraphStore
        from code_review_graph.incremental import full_build

        db_path = Path(base_dir) / repo_name / "graph.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        store = GraphStore(db_path)

        logger.info("CRG full_build 开始: %s → %s", repo_root, db_path)
        build_stats = full_build(Path(repo_root), store)
        logger.info("CRG 建图完成: %s", build_stats)

        # 社区检测 + 持久化:overview / hub / bridge 读节点的 community_id,必须先 store_communities
        communities = detect_communities(store, min_size=min_community_size)
        stored = store_communities(store, communities)
        logger.info("CRG 社区: 检测 %d 个,持久化 %d 个", len(communities), stored)

        return cls(store, repo_name)

    @classmethod
    def open(cls, repo_name: str, *, base_dir: str = "data/structgraph") -> CodeGraph:
        """打开已建好的图(不重建,省去 full_build)。db 不存在 → FileNotFoundError。"""
        _require_crg()
        from code_review_graph.graph import GraphStore

        db_path = Path(base_dir) / repo_name / "graph.db"
        if not db_path.exists():
            raise FileNotFoundError(f"结构图未建,先 CodeGraph.build(...): {db_path}")
        return cls(GraphStore(db_path), repo_name)

    # ── 查询(薄封装 CRG 分析 API)────────────────────────────────────────

    def architecture_overview(self) -> dict:
        """架构总览:社区清单 + 跨社区耦合边 + 高耦合告警(>10 条边的社区对)。

        报告「系统架构」章节的数据源(图驱动,非 LLM 瞎编)。
        """
        from code_review_graph.communities import get_architecture_overview

        return get_architecture_overview(self._store)

    def communities(self) -> list[dict]:
        """社区清单(≈ 模块边界):每个社区 = 一组被 Leiden 聚到一起的结构节点。"""
        from code_review_graph.communities import get_communities

        return get_communities(self._store)

    def hub_nodes(self, top_n: int = 15) -> list[dict]:
        """hub 节点(in+out 度最高):核心函数 / 被大量依赖的枢纽。报告「结构风险」用。"""
        from code_review_graph.analysis import find_hub_nodes

        return find_hub_nodes(self._store, top_n=top_n)

    def bridge_nodes(self, top_n: int = 15) -> list[dict]:
        """bridge 节点(betweenness 最高):多处最短路径必经的瓶颈,断了多社区失联。"""
        from code_review_graph.analysis import find_bridge_nodes

        return find_bridge_nodes(self._store, top_n=top_n)

    def impact_radius(self, changed_files: list[str]) -> dict:
        """改动影响面(BFS):给定一批改动文件,返回受波及的节点/文件/边(blast-radius)。

        深度/节点上限用 CRG 默认(MAX_IMPACT_DEPTH / MAX_IMPACT_NODES)。
        """
        return self._store.get_impact_radius(changed_files)

    # ── P1.5 caller/callee 调用链(首次请进适配层,填 __init__.py 的「延后」)─────

    def call_chain(self, symbol: str, *, direction: str = "both",
                   depth: int = 2, top_n: int = 15) -> dict:
        """符号中心的 N 跳调用链(沿 CALLS 边)+ PageRank 重要度。

        给一个函数名,回答「谁调用它 / 它调用谁,N 跳之内,哪些结构上重要」——
        bug-RCA / 调研时定位根因、判断改动影响最想要的「调用链」视图。

        和 impact_radius(blast_radius)的分工:
          - impact_radius = 文件种子 + 全边类型 + 「波及面」(我改这些文件 → 谁受波及);
          - call_chain    = 符号种子 + 仅 CALLS 边 + 「调用链 + 重要度」(这个函数的调用上下文)。

        symbol:函数/方法名。bare 名(如 wpa_supplicant_init)或 qualified
               (如 wpa_supplicant.c::wpa_supplicant_init)都行,内部解析到图节点。
        direction:"callers"(谁调它,沿 CALLS 逆边)/ "callees"(它调谁,沿 CALLS 正边)/
                  "both"(默认,两边都给)。
        depth:跳数(默认 2,封顶 5 防大图节点爆炸)。
        top_n:每个方向返回的节点上限(按「跳数升序 → PageRank 降序」排后取),默认 15。

        返回 ``{symbol, resolved, direction, depth, callers:[...], callees:[...],
        truncated, note}``,每个节点是 ``{qualified_name, file, line, kind, hop, pagerank}``。
        symbol 解析不到节点 → 抛 ValueError(工具层转友好串)。

        实现全在 networkx 层(复用 store 的缓存全图,只过滤 CALLS 边),不逐边 SQL,大图友好。
        PageRank 在 CALLS-only 子图上跑 —— 被越多重要函数调用 → 分越高,标识「结构上关键的函数」。
        """
        import networkx as nx

        if direction not in ("callers", "callees", "both"):
            raise ValueError(f"direction 需为 callers / callees / both,收到 {direction!r}")
        depth = max(1, min(int(depth), 5))  # 封顶 5 防大图爆炸;至少 1 跳

        # 1) 解析符号 → qualified_name(精确 > bare 名 > 子串兜底)
        nxg = self._store._build_networkx_graph()
        if symbol in nxg:
            seed, note = symbol, ""
        else:
            bare_hits = [n for n in nxg if str(n).split("::")[-1] == symbol]
            if len(bare_hits) == 1:
                seed, note = bare_hits[0], f"resolved bare name '{symbol}' → '{bare_hits[0]}'"
            elif len(bare_hits) > 1:
                seed = bare_hits[0]
                note = (f"bare name '{symbol}' 有 {len(bare_hits)} 个匹配,取首个 '{seed}';"
                        f"其余: {', '.join(bare_hits[1:5])}")
            else:
                sub_hits = [n for n in nxg if symbol in str(n)]
                if len(sub_hits) == 1:
                    seed, note = sub_hits[0], f"resolved by substring '{symbol}' → '{sub_hits[0]}'"
                elif len(sub_hits) > 1:
                    seed = sub_hits[0]
                    note = f"substring '{symbol}' 有 {len(sub_hits)} 个匹配,取首个 '{seed}'"
                else:
                    raise ValueError(
                        f"符号 '{symbol}' 在图里找不到(试 bare 名或 qualified path/file.c::func)"
                    )

        # 2) CALLS-only 子图(复用缓存全图,只留 kind=CALLS 的边;节点随之)
        calls = nx.DiGraph()
        calls.add_edges_from(
            (u, v) for u, v, d in nxg.edges(data=True) if d.get("kind") == "CALLS"
        )

        # 种子可能只在别的边类型里出现(没有 CALLS 边)→ 不在 calls 子图 → 无调用关系,返空链
        if seed not in calls:
            return {"symbol": symbol, "resolved": seed, "direction": direction, "depth": depth,
                    "callers": [], "callees": [], "truncated": False,
                    "note": (note + " | " if note else "") + f"'{seed}' 无 CALLS 边(不被调也不调谁)"}

        # 3) PageRank 在 CALLS 子图上(被越多重要函数调用 → 分越高);分层降级见 _pagerank
        scores: dict[str, float] = _pagerank(calls)

        # 4) N 跳有界 BFS(自写,避开 nx.ancestors/descendants 的无界 transitive 爆炸)
        def _bfs(neighbors_fn, start: str) -> list[tuple[str, int]]:
            # neighbors_fn:calls.successors(callees 正向)/ calls.predecessors(callers 逆向)
            seen: dict[str, int] = {start: 0}
            frontier = [start]
            for hop in range(1, depth + 1):
                nxt = []
                for node in frontier:
                    for nb in neighbors_fn(node):
                        if nb not in seen:
                            seen[nb] = hop
                            nxt.append(nb)
                frontier = nxt
                if not frontier:
                    break
            return [(n, h) for n, h in seen.items() if n != start]  # 丢种子本身

        callers_raw = _bfs(calls.predecessors, seed) if direction in ("callers", "both") else []
        callees_raw = _bfs(calls.successors, seed) if direction in ("callees", "both") else []

        # 5) enrich 节点元数据(批量查 file/line/kind;_batch_get_nodes 自带 SQLite 变量数分批)
        all_qns = {n for n, _ in callers_raw} | {n for n, _ in callees_raw}
        meta = {nd.qualified_name: nd for nd in self._store._batch_get_nodes(all_qns)}

        # 6) 组装:每方向按(跳数升序 → PageRank 降序)排,截 top_n
        def _build(rows: list[tuple[str, int]]) -> tuple[list[dict], bool]:
            ordered = sorted(rows, key=lambda nh: (nh[1], -scores.get(nh[0], 0.0)))
            truncated = len(ordered) > top_n
            out = []
            for n, h in ordered[:top_n]:
                nd = meta.get(n)
                out.append({
                    "qualified_name": n,
                    "file": getattr(nd, "file_path", None),
                    "line": getattr(nd, "line_start", None),
                    "kind": getattr(nd, "kind", None),
                    "hop": h,
                    "pagerank": round(scores.get(n, 0.0), 6),
                })
            return out, truncated

        callers, trunc_c = _build(callers_raw)
        callees, trunc_x = _build(callees_raw)
        return {"symbol": symbol, "resolved": seed, "direction": direction, "depth": depth,
                "callers": callers, "callees": callees, "truncated": trunc_c or trunc_x, "note": note}

    # ── P-A 1b 批量聚合用的改动分析(扩 wrap CRG changes.py,R4.1.2)──────────────

    def analyze_changes(self, changed_files: list[str], *,
                        changed_ranges: dict[str, list[tuple[int, int]]] | None = None,
                        repo_root: str | None = None, base: str = "HEAD~1",
                        include_churn: bool = False) -> dict:
        """改动分析(批量 PR 聚合用):一批改动文件 → 风险分 + 改动函数 + 受影响流 + 测试缺口 + 复审优先级。

        wrap CRG `analyze_changes`(changes.py):六因子 ``risk_score``(flow 参与 / 社区跨越 / 测试覆盖 /
        SECURITY_KEYWORDS 名字命中 +0.20 / 调用方数 / 改动频率)+ ``changed_functions``(每函数带 risk)+
        ``affected_flows`` + ``test_gaps`` + ``review_priorities``(top-10 by risk)。图里没有的文件 → 空结果,不崩。
        给每条 PR 算一个 risk_score 用于安全分层(高风险/security 子集才送 LLM 深 CWE 分类,省 token)。

        changed_ranges:``{file: [(start,end),...]}`` 行范围(从 PR diff 的 hunk 算)。给了直接用;
            没给 + 给了 repo_root → CRG 跑 ``git diff <base>`` 自己解(本机非 git 仓或想用 PR diff 时传这个)。
        """
        from code_review_graph.changes import analyze_changes as _crg_analyze

        return _crg_analyze(self._store, changed_files, changed_ranges=changed_ranges,
                            repo_root=repo_root, base=base, include_churn=include_churn)

    def community_ids_for(self, qualified_names: list[str]) -> dict[str, int | None]:
        """批量查「符号 → 社区(module)」映射(批量 PR 聚合按 module 分桶用)。

        wrap CRG ``GraphStore.get_community_ids_by_qualified_names``(graph.py,批量 450)。返
        ``{qualified_name: community_id}``;community_id 相同的符号归同一模块/社区。图缺或符号不在图 → 该项 None。
        """
        if not qualified_names:
            return {}
        return self._store.get_community_ids_by_qualified_names(qualified_names)

    def stats(self) -> dict:
        """图统计(节点/边数等)给报告元数据用。GraphStats 形状以 CRG 版本为准,这里宽容转 dict。"""
        import dataclasses

        s = self._store.get_stats()
        # is_dataclass 对「实例」和「类」都返 True;排除类(只要实例),asdict 才接受。
        if dataclasses.is_dataclass(s) and not isinstance(s, type):
            return dataclasses.asdict(s)
        return {"raw": str(s)}

