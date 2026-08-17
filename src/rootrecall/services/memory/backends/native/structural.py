"""native 后端 · 结构路适配器(R1 backends/native/structural.py)。

把 code-review-graph(CRG)的结构图能力包成 recall 的"structural 路"。
CRG 是 Python 库(import code_review_graph),SQLite 存结构图,给 blast-radius / callers / callees。
默认 Noop(不接);配 native.structural: crg + `uv sync --extra code-review-graph` 才启用。

为什么单独一个 Protocol:recall 的结构路是可选的(R1 eval 在小仓演示;wpa/bluez 的 C 图
R3 前 tree-sitter-c 补)。Protocol 解耦 —— 换别的结构引擎只实现接口,recall 不改。
"""

from __future__ import annotations

import logging
import re
from typing import Protocol

from rootrecall.services.memory.schema import RecallHit

logger = logging.getLogger(__name__)


class StructuralBackend(Protocol):
    """结构路接口:query → 相关结构节点(callers/callees / blast-radius)→ RecallHit。"""

    def blast_radius(self, query: str, *, repo: str, limit: int) -> list[RecallHit]: ...


class NoopStructuralBackend:
    """默认:不接结构图,返 [](recall 走 memory + code 两路)。"""

    def blast_radius(self, query: str, *, repo: str, limit: int) -> list[RecallHit]:
        return []


class CrgStructuralBackend:
    """code-review-graph 适配器(需 `uv sync --extra code-review-graph` + 已对 repo 建图)。

    建图(一次性):from code_review_graph.tools.build import build_or_update_graph
                  build_or_update_graph(full_rebuild=True, repo_root=<path>, postprocess='full')
    blast_radius(query):从 query 抽标识符 → 在图里 query_graph(callers_of/callees_of)→
    包成 RecallHit(source=structural)。

    注:CRG 的 get_impact_radius 吃 changed_files 不吃自然语言;这里先按符号名查邻居。
    query→符号的语义化(LLM 抽符号)+ get_impact_radius 真爆裂面 放 backlog(R3)。
    CRG 返回形状以实测为准(此处对 results/nodes 双兜底)。
    """

    def __init__(self, repo_root: str):
        # 构造时校验依赖(没装给清晰指引,不崩 import)。用 find_spec 探测而非真 import,
        # 既不触发 IDE 的"无法解析导入",也不留 ruff 的 unused-import。
        import importlib.util

        if importlib.util.find_spec("code_review_graph") is None:
            raise ImportError(
                "CRG 结构路需要 code-review-graph。装它: uv sync --extra code-review-graph"
            )
        self._repo_root = repo_root

    def blast_radius(self, query: str, *, repo: str, limit: int) -> list[RecallHit]:
        from code_review_graph.tools.query import query_graph  # pyright: ignore[reportMissingImports]  (可选 extra,未装时 __init__ 已拦)

        tokens = [t for t in _identifiers(query) if len(t) >= 3][:5]
        if not tokens:
            return []
        out: list[RecallHit] = []
        seen: set[str] = set()
        for tok in tokens:
            for pat in ("callers_of", "callees_of"):
                try:
                    res = query_graph(pat, tok, repo_root=self._repo_root, max_results=limit)
                except Exception:  # noqa: BLE001 - 单个符号查失败跳过,不阻断
                    continue
                nodes = (res.get("results") or res.get("nodes") or []) if isinstance(res, dict) else []
                for node in nodes[:limit]:
                    qn = node.get("qualified_name") or node.get("name") or ""
                    if not qn or qn in seen:
                        continue
                    seen.add(qn)
                    out.append(RecallHit(
                        summary=f"{pat}: {qn}",
                        score=0.3,  # 结构路固定低分(RRF 里靠多路一致出现才上位)
                        source="structural", repo=repo,
                        file=node.get("file_path"),
                        line_start=node.get("line_start"),
                    ))
                    if len(out) >= limit:
                        return out
        return out


def _identifiers(query: str) -> list[str]:
    """从查询里抽出可能的代码标识符(CamelCase / snake_case / 全小写词)。"""
    return re.findall(r"[A-Za-z_][A-Za-z0-9_]+", query or "")
