"""三条工作流图(v2 重规划;2026-08-07 pivot 后 bug_rca 降级留参考,主路径走 opencode + skill + MCP)。

- bug_rca:       降级参考(R2):老六节点委托管线;主路径 = opencode + bug-rca skill + MCP 工具
- deep_research: 代码仓 → 架构/模块文档(R3):Aider repomap + code-review-graph + Reporter
- patch_report:  批量 PR 聚合报告(P-A 1b):fetch → analyze → aggregate → report → memorize

复用共享服务(code_index + memory)+ 委托接口(仅 bug_rca 老 pathway 用 CodingAgentDelegate)。
详见 docs/设计/architecture.md §3/§6/§7,以及 bug-rca-design.md / deep-research-design.md。
"""
