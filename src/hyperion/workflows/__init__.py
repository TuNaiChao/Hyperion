"""三条工作流图(v2 重规划)。

- bug_rca:       ★MVP(R2):召回 → Agentless 定位漏斗 → 组装精确上下文 → 委托 omp/opencode → 报告 → 沉淀
- deep_research: 代码仓 → 架构/模块文档(R3):Aider repomap + code-review-graph + Reporter
- pr_tracker:    上游 PR 跟踪 + 合入建议(R4):cron + GraphQL + map-reduce + 决策卡

三条都复用共享服务(code_index + memory)+ 委托接口(CodingAgentDelegate)。
详见 docs/设计/architecture.md §3/§6/§7,以及 bug-rca-design.md / deep-research-design.md。
"""
