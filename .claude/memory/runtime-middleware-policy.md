---
name: runtime-middleware-policy
description: "R3 runtime 中间件策略——最小集 + 保扩展口(pull-by-need,不抄 deer-flow 30+)"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-07-30T08:18:00.216Z
---

R3 runtime 中间件**不追求 deer-flow 的 30+ 数量**,按 **pull-by-need** 加:只在 ① 真踩到该中间件防的故障,或 ② 它是某已计划功能的已知前置(如 DanglingToolCall 之于 Summarization)时加。**绝不因「deer-flow 有」就加。**

**Why**:deer-flow 35 个中间件大半服务其广产品面(web/IM/skills/MCP/鉴权/沙箱/前端/交互),Hyperion 的 scope 重叠处只在「长 agent 上下文管理 + 错误韧性」。一人/数月预算;中间件顺序敏感(langgraph after_model 反序派发),越加越难调试交互。

**How to apply(Hyperion 现实目标集,非 30+)**:
- R3.0 = ToolOutputBudget + TokenBudget(2,已成)。
- R3.2 = +Summarization +LoopDetection +DynamicContext + 伴生 DanglingToolCall / LLMErrorHandling / ToolErrorHandling(共 5-8)。
- R5 = 选配 ReadBeforeWrite / SystemMessageCoalescing / ModelLengthFinishReason / ToolProgress / TokenUsage(每个需真实故障触发)。

**扩展口已留好**(将来的 skills/MCP/多用户鉴权/沙箱/artifacts/前端 是 R4/R5 规划内,用户 2026-07-30 明确要保扩展):
① `create_hyperion_agent(middleware=...)` 接任意链;② create_agent 自动合并 middleware 的 `state_schema`(实测 extra_field 进 channels)—— 新中间件自带 state 不改 HyperionState;③ HyperionState 是 TypedDict 随时加字段;④ tool_output 的 `_resolve_outputs_dir` 已预留读 `state["sandbox"]["workspace"]`。
**唯一将来要升级**:`@Next/@Prev` 排序机制(deer-flow factory.py:357),链 >7 时移植;R3.0 用普通有序 list(factory `build_default_middlewares` 文档钉了主脊槽位)。
**记忆仍自建**:多用户时给 MemoryService 加 owner scope,不抄 deer-flow MemoryMiddleware(web-conversation 味,与 Hyperion code_index+CRG 味不同)。

关联:[[align-to-deerflow-production-grade]]、[[agent-project-overview]]。
