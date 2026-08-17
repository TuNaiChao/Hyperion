"""共享服务层(三条工作流共用)。

v2(2026-07-28 产品重规划)后的范围:

- code_index: ✅ 代码理解(已实现 P1.0–P1.5):tree-sitter 抽符号 + LanceDB 混合检索(语义 L1)
              + clangd 精确导航(L2)+ outline/eval
- memory:     🆕 记忆与持续学习(R1):MemoryService 契约 + 可换后端
              (v1 native = code_index + code-review-graph)+ Recall/Memorize

v0.1 的 log_symbolizer / static_analysis 在 v2 裁出 v1(日志/静态分析委托给 omp/opencode),
记入 .claude/memory/backlog-production-grade.md,需要时再加。

详见 docs/设计/architecture.md §5。
"""
