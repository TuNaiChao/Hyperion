---
name: suggestion-b-token-summarization-handoff
description: 建议 B 落地:摘要触发改 token 感知 trigger=tokens:32000(对齐 deer-flow);fraction 实测排除(profile=None 会崩)。
metadata:
  type: project
---

2026-08-12 落地 architecture-review §五 建议 B(摘要触发改 token 感知,直击 §2.1 短板 1)。

## 改了啥
- **`SummarizationConfig` dataclass**(factory.py):3 字段 `enabled` / `trigger_tokens=32_000` / `keep_messages=20`。
- **`build_default_middlewares` 加 `summarization` 参数**:SummarizationMiddleware 构造从 `trigger=("messages",50)` 改 `trigger=("tokens", sc.trigger_tokens)`。`enabled=False` 跳过(扩展口)。
- 2 单测:`test_factory_summarization_trigger_is_token_aware`(断言 trigger 含 `("tokens",32000)`、不含旧 `("messages",50)`)+ `..._disabled_skips`。全 runtime 26 绿。

## 核心调研结论(逐字核源码,防回退踩坑)
1. **langchain 1.3.14 `SummarizationMiddleware.trigger` 原生支持 token 感知** —— 不是 API 限制,Hyperion 当初选了 `("messages",50)`。源码 `.venv/.../langchain/agents/middleware/summarization.py:230`。三种:`("tokens",N)` / `("fraction",F)` / `("messages",N)`,可 list(OR)/ dict(AND)。
2. **`("fraction",F)` 硬排除** —— 实测三模型(deepseek-v4-pro/flash、gpt-4.1)全 `profile=None`(ChatOpenAI 走第三方兼容端点)。源码 `__init__` 末尾:用 fraction 但 `_get_profile_limits() is None` → `raise ValueError`,**整个 lead agent 构造崩**。只能 `("tokens",N)`。
3. **deer-flow 生产默认就是 token 触发** —— `deer-flow/config.example.yaml:1563` `trigger:[{type:tokens,value:32000}]`(fraction/messages 注释掉);deer-flow 继承 langchain SummarizationMiddleware 没改触发逻辑(只增强摘要生成+多模型 fallback+手动 compact)。→ Hyperion 直用父类+换 token 触发 = 完全对齐,零自写子类。

## 故意不做(YAGNI,防回退)
- **不进 config.yaml** —— token_budget/tool_output 的 yaml 当前都没 wire 进 build_default_middlewares(config.py:240 自承认),进 yaml = 造死配置。对齐 turn_budget 先例(代码内传参)。将来可配:RuntimeConfig 已 `extra="allow"`,接一下即可。
- **不改 keep 为 token** —— 只 trigger 需要治(「何时压」);keep 用 messages 计数 langchain 默认 safe。
- **不自写 SummarizationMiddleware 子类** —— deer-flow 自写是为多模型 fallback+手动 compact(lead agent 交互特性);Hyperion lead agent 只在 deep_research 子 agent 跑,无交互式 /compact,父类够用(踩坑#2)。
- **不改 _research.py** —— 用 factory 默认即可。

相关:[[toolset-after-audit-2026-08-10]] 工具集审计;[[runtime-middleware-policy]] 中间件 pull-by-need 策略。
