---
name: route2-call-chain-handoff
description: "2026-08-11 路线 #2(feature 2a call_chain)完成 —— CRG 多跳 CALLS-only BFS + PageRank;新增第 10 个 MCP 工具。"
metadata:
  node_type: memory
  type: project
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-08-11T02:46:16.660Z
---

**2026-08-11 路线 #2「feature 2a 调用链 `call_chain`」完成。** 多库地基(#1)已解锁,这是三件套里「代码情报」的新工具。

**新增**:
- `CodeGraph.call_chain(symbol, *, direction="both", depth=2, top_n=15)`(`code_graph.py`):P1.5 caller/callee 首次请进适配层(填 `__init__.py` 当年标的「延后」)。算法全在 networkx 层 —— 解析符号(精确>bare 名>子串)→ 取缓存全图 `_build_networkx_graph()` 过滤 `kind=="CALLS"` 边建子图 → PageRank → 双向有界 BFS(callers=predecessors / callees=successors,自写避开 `nx.ancestors/descendants` 无界 transitive)→ `_store._batch_get_nodes(set)` 批量 enrich file/line/kind → 按(跳数升序→PageRank 降序)截 top_n。
- `call_chain` MCP 工具(`mcp_memory.py`,第 10 个):镜像 blast_radius 的壳(per-call `codebase or repo`)+ 优雅降级(CRG 未装 / 图未建 FileNotFoundError / symbol 找不到+direction 非法 ValueError → 友好串,绝不抛 traceback)。

**与 blast_radius 的分工**(互补,不是重复):blast_radius = 文件种子 + 全边类型 + 「波及面」(改这些文件→谁受波及);call_chain = 符号种子 + 仅 CALLS 边 + 「调用链 + 重要度」(这个函数的调用上下文)。

**关键 gotcha(踩了):`nx.pagerank` 在 networkx 3.6.1 默认走 scipy 稀疏矩阵后端,本机 venv 没装 scipy → `ModuleNotFoundError`。** 不强加 scipy 重依赖,改写 `_pagerank(graph)` 辅助分层降级:优先 `nx.pagerank`(别的机装了 scipy 就走它,大图快);scipy 缺则降级 networkx 内置 `_pagerank_python`(纯 python 幂迭代、直接吃邻接表不建稠密矩阵 → **大图不 OOM**,慢但正确;私有 API 但跨多版本稳定)。numpy 2.5.1 在装(lancedb/networkx 依赖),但 `_pagerank_numpy` 用**稠密矩阵**(大图 OOM),故弃用、选 `_pagerank_python`。

**验证**:8 测绿(`test_code_graph` 全套含新 `call_chain_small_repo` 正向 + `bad_direction` 校验;`test_mcp_tools` 的 `call_chain_not_built` + `bad_direction` 降级),ruff 干净。**happy-path probe 实证**(小 fixture):`call_chain("alpha")` callers=`b.py::caller`+`a.py::Foo.method`(hop1),callees=`beta`+`gamma`(hop1)→`delta`(hop2 via gamma),file:line+pagerank 齐全 —— 证明 CRG 对 Python fixture **确实产出 CALLS 边**,核心链路全通。

**CRG API 摸底修正**:计划写的是 `get_nodes_by_qualified_names`,实际方法是私有 `_batch_get_nodes(self, qualified_names: set[str]) -> list[GraphNode]`(graph.py:1476,自带 SQLite 变量数分批);GraphNode 字段 `qualified_name/file_path/line_start/kind` 直读确认。边带 `kind` 属性确认(`graph.py:1552 g.add_edge(source,target,kind=kind)`)。

**Why / How to apply:** 调用链 = bug-RCA/调研定位根因最想要的视图,纯静态正合「不编译/不复现」方针。下一步路线 #3(feature 2b `cross_version_diff` 跨版本 diff,依赖本 2a 的符号/图能力)。关联 [[multi-codebase-per-call-handoff]]、[[toolset-after-audit-2026-08-10]]、[[harness-route-review-2026-08-07]]。

**⚠️ 工具 gotcha(非代码):** 后台并行起两个 `uv run` 会**争同一个 `.venv` 同步**;且 shell cwd 可能漂到 `code-review-graph/` 子项目(它是独立 uv project)→ `uv run` 误用 code-review-graph 的 pyproject/venv,`tests/...` 相对路径找不到(pytest exit 4 no tests ran)。**解法**:uv run 串行(别并行)+ 开跑前 `cd /home/tnc/Desktop/Agent/Hyperion && pwd` 确认在项目根。
