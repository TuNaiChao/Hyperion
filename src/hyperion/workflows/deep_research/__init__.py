"""deep_research workflow(P1 代码仓深度调研,R3.2)。

六节点线性流水线:ingest → index → plan → research → report → memorize。
复用 bug_rca 的 StateGraph 模式 + 共享底座(code_index / CRG / memory)。
设计见 docs/设计/deep-research-design.md。
"""
