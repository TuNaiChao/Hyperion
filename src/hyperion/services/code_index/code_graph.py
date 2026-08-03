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
from pathlib import Path

logger = logging.getLogger(__name__)


def _require_crg() -> None:
    """CRG 是否已装;没装给清晰指引(用 find_spec 探测,不触发真 import 报错)。"""
    if importlib.util.find_spec("code_review_graph") is None:
        raise ImportError(
            "CRG 结构图服务需要 code-review-graph。装它: uv sync --extra code-review-graph"
        )


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

    def stats(self) -> dict:
        """图统计(节点/边数等)给报告元数据用。GraphStats 形状以 CRG 版本为准,这里宽容转 dict。"""
        import dataclasses

        s = self._store.get_stats()
        # is_dataclass 对「实例」和「类」都返 True;排除类(只要实例),asdict 才接受。
        if dataclasses.is_dataclass(s) and not isinstance(s, type):
            return dataclasses.asdict(s)
        return {"raw": str(s)}

