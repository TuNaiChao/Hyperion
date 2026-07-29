---
name: deepseek-structured-output-gotcha
description: "DeepSeek-v4-pro 思考模式不支持 tool_choice / response_format json_schema —— 结构化产出改用\"喂 Schema + 直出 JSON + 手动解析\""
metadata: 
  node_type: memory
  type: reference
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-07-29T01:24:37.814Z
---

DeepSeek-v4-pro(经 `https://api.deepseek.com`)思考模式默认开,LangChain `with_structured_output` 连踩两坑:

1. 默认(走 response_format json_schema)→ `400 This response_format type is unavailable now`(DeepSeek 不支持 json_schema)。
2. `method="function_calling"`(走 tool_calls)→ `400 Thinking mode does not support this tool_choice`(思考模式不接受强制 tool_choice)。

**解法**:不用 `with_structured_output`,改「提示词喂 JSON Schema(`Pydantic.model_json_schema()`)+ 模型直出 JSON + 手动抠最外层 `{...}` 解析」。实现见 `src/hyperion/services/memory/backends/native/extract.py`(`_JSON_PROMPT` + `_extract_json_object`)。

**Why:** 这条对 DeepSeek 的所有结构化产出通用,不只记忆抽取。**How to apply:** R2 委托产出契约([[agent-project-overview]] 的 `CodingAgentDelegate.StructuredResult` JSON)若用 DeepSeek 做后处理/校验,会踩同样坑 —— 直接复用这套喂-Schema 方案,别再试 with_structured_output。

另:embedding / rerank 走 **DashScope**(`text-embedding-v4` / `qwen3-rerank`),**不是 DeepSeek**;DashScope key 必须有效(401 = key 服务端不认,去 [DashScope 控制台](https://bailian.console.aliyun.com)重新复制 API-KEY)。对齐 deer-flow/生产级见 [[align-to-deerflow-production-grade]]。
