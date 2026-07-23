"""Workflow graphs.

- bug_rca:       hypothesize -> locate -> verify -> report -> memorize (StateGraph)
- deep_research: supervisor + subagents + adversarial verify + sandbox test
- pr_tracker:    cron + GraphQL + Send map-reduce + merge scorecard

See docs/architecture.md §6. (Implementations land in P2 / P4 / P5.)
"""
