---
name: delegate-already-localizes
description: "建 Hyperion 侧能力前先问:opencode(delegate)是不是已经会?会→别建平行管线,把 Hyperion 独有的做成 MCP 工具给它调"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-07-31T08:35:44.861Z
---

**建任何 Hyperion 侧能力(定位/检索/过滤/分析)前,先问一句:委托目标 opencode 是不是已经会这个?**

- **会** → 别建平行管线(= double work,白烧 token);把 Hyperion **独有的、opencode 缺/搞不便宜**的能力(记忆 / code_index 廉价检索 / 日志过滤)做成 **MCP 工具**给 opencode 按需调。
- **不会/搞不便宜** → 才值得 Hyperion 自建(且优先做成工具,而非写死固定管线阶段)。

**Why:** bug-RCA 一度自建 Agentless 式定位漏斗(file→function→line)+ regex 关键字 + 方案A 预筛,结果与 opencode **重复定位**(double localization),整套砍掉改工具驱动(见 [[pitfall-log]] #2)。根因同 [[rerank-mechanism-where-it-shines]] 的 #1:**设计前没核前提**(opencode 本就会定位)。Hyperion 是**委托型** agent(把活派给 opencode),不是 Agentless 那种**独立** agent —— 借鉴 Agentless 漏斗前要先对齐架构形态。

**How to apply:** ① 设计 Hyperion 能力前先判"opencode 会不会?"(它会 grep/read/edit + 自主探索,定位/改码是它的强项)。② Hyperion 护城河 = opencode 缺的:记忆(跨会话 P3)+ code_index 廉价语义检索(opencode 只能 grep 烧 frontier turn)+ 日志过滤 + 调度/验证/报告 —— 做成 MCP 工具(`hyperion mcp serve`),opencode 经 MCP 调,prompt 提示优先用。③ "工具 > 固定流程"(2026 共识:deer-flow 2.0/Claude Code/OpenHands 全是 lead agent + 工具),但**固定/廉价的检索活在工具内部**(search_codebase 内部跑固定 BM25+rerank),控制流工具驱动。和 [[avoid-overengineering]] 配合。
