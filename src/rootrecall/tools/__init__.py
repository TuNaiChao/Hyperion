"""RootRecall tool 层(harness 转向后只剩两块,2026-08-10 复核):

- mcp_memory.py: MCP server —— 9 个工具暴露给 coding agent(opencode/codex/claude code)。
- delegate.py:  老 bug_rca orchestrator 的委托封装(降级参考;主路径 opencode 自驱不经此)。

转向前的 @tool 包装层(sandbox/code_nav/memory + registry + platform/agent demo)已撤 ——
opencode 自带 read/grep/bash,RootRecall 底层能力经 MCP 暴露(不重造 @tool,踩坑#2)。
"""
