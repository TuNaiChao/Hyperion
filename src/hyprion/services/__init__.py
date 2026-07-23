"""Shared services layer (consumed by all three workflows).

- code_index:      tree-sitter + ctags + LanceDB hybrid retrieval
- memory:          LangGraph Store + mem0 + Graphiti; Recall / Memorize pipelines
- log_symbolizer:  addr2line / btmon / wpa -> source lines
- static_analysis: sparse / smatch / coccinelle wrappers

See docs/architecture.md §5. (Implementations land in P1 / P3.)
"""
