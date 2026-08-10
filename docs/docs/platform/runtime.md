# 平台 · 运行时 harness

> `platform/runtime/` —— 把「模型 + 工具 + 中间件链 + state + checkpointer」拼成可跑的 LangGraph agent,管 token / 轮数 / loop / 工具输出预算。
> 对标 deer-flow 的 runtime(自建,coding 活仍委托 opencode)。驱动 Hyperion 自己的长 agent(如深度调研的 ReAct 子 agent)。

## 概览

Hyperion 的 lead / 子 agent 跑起来需要护栏:断点续跑(checkpointer)、token 预算闸(防烧爆)、ReAct 轮数闸(防死循环)、工具输出外化(防单次工具返回撑爆上下文)、重复调用检测(防 agent 卡在同一 tool_call)。这些做成**中间件**,按 pull-by-need 挂到 `create_hyperion_agent` 的链上,不抄 deer-flow 的 30+ 全家桶。

## 源码

| 文件 | 职责 |
|---|---|
| `runtime/factory.py` | `build_default_middlewares` + `create_hyperion_agent` |
| `runtime/state.py` | `HyperionState(AgentState)` TypedDict + `DelegationEntry` + `merge_delegations` reducer + `TERMINAL_STATUSES` |
| `runtime/checkpoint.py` | `get_checkpointer()` 单例 / `checkpointer_context()` / `reset_checkpointer()` |
| `runtime/middlewares/tool_output.py` | `ToolOutputBudgetMiddleware` + `ToolOutputBudgetConfig` |
| `runtime/middlewares/token_budget.py` | `TokenBudgetMiddleware` + `TokenBudgetConfig` / `TokenUsage` |
| `runtime/middlewares/loop_detection.py` | `LoopDetectionMiddleware` |
| `runtime/middlewares/turn_budget.py` | `TurnBudgetMiddleware` + `TurnBudgetConfig` |
| `runtime/context/tool_output_synopsis.py` | `build_tool_output_synopsis` / `render_tool_output_preview`(从 deer-flow 逐字移植,按 json/csv/yaml/… 分型摘要,纯函数不调 LLM) |
| `runtime/_bounded_dict.py` | `BoundedDict` —— 中间件用的上限 LRU dict(防 abandoned run 泄漏) |

## API

### 组装 agent

```python
def build_default_middlewares(
    model=None,
    *,
    token_budget=None,
    tool_output=None,
    turn_budget=None,
) -> list[AgentMiddleware]

def create_hyperion_agent(
    model,
    tools,
    *,
    system_prompt: str | None = None,
    middleware: list[AgentMiddleware] | None = None,
    state_schema=HyperionState,
    checkpointer=None,
    name: str | None = None,
) -> CompiledStateGraph
```

### checkpointer

```python
def get_checkpointer() -> Checkpointer          # 单例
def checkpointer_context() -> ContextManager    # CM
def reset_checkpointer() -> None
```

后端由 `config.runtime.checkpoint_backend` 决定:`memory`(仅测)/ `sqlite`(默认,落 `data/runtime/checkpoint.sqlite`)/ `postgres`。

## 默认中间件链顺序

`build_default_middlewares` 返回的链(顺序敏感):

```
ToolOutputBudget → Summarization(条件,trigger 50 / keep 20) → LoopDetection → TurnBudget → TokenBudget
```

| 中间件 | 触发 | 动作 |
|---|---|---|
| `ToolOutputBudgetMiddleware` | 单次工具输出 > `externalize_min_chars`(30000) | 外化到磁盘(`outputs_dir`)+ 用 `build_tool_output_synopsis` 摘要替换;磁盘失败降级 head+tail |
| `SummarizationMiddleware`(langchain 自带) | 消息数 > trigger(50) | 压成摘要,保留 keep(20)条;仅在 model 可用时条件加入 |
| `LoopDetectionMiddleware` | 同一 tool_call hash 重复 | warn@3 / hard stop@5;按 `thread_id` 分桶 |
| `TurnBudgetMiddleware` | 每 run ReAct 轮数 | warn@(max-1)/ hard strip tool_calls@max(默认 `max_turns=50`);`consume_stop_reason(thread_id)` |
| `TokenBudgetMiddleware` | 每 run token 用量 | soft warn@0.7 / hard stop@1.0 剥 tool_calls 自然停;`consume_stop_reason(run_id)` |

## 流程(一个 run)

1. `create_hyperion_agent` 合并传入 `middleware` 的 `state_schema`(中间件按需扩 state)+ 默认链。
2. 算 `recursion_limit`(见下)。
3. agent 进 ReAct 循环;每轮经过中间件链:工具输出超长先外化 → loop 检测 → 轮数预算 → token 预算。
4. 撞硬停时剥掉 `tool_calls`,让模型自然收尾;撞 recursion_limit 时有强制收尾补救(优雅降级,见下)。

## recursion_limit 动态计算(重要)

> [!WARNING]
> LangGraph 的 `recursion_limit` 单位是 **superstep**,不是「轮」。每个中间件是独立图节点,所以一轮 = `N + 2` superstep(N = 中间件数),不是 2。踩坑教训:固定值会撞墙。

`create_hyperion_agent` 按 `len(middleware)` 动态算:

```python
recursion_limit = _recursion_limit_for(max_turns, n_middleware)
# 形如 (max_turns + 2) * 2,随中间件数调整
```

deep_research 的子 agent 用紧 `TurnBudget(max_turns=20)` + 动态 recursion_limit,撞墙时 astream + 裸模型强制收尾(优雅降级,避免整段结果丢失)。

## keying 用 thread_id(不是 id(runtime))

中间件的状态分桶键用 `thread_id`(稳定),不要用 `id(runtime)`(每 superstep 变,会每轮清零失去记忆)。

## 配置

```yaml
runtime:
  enabled: true
  checkpoint_backend: sqlite       # memory | sqlite | postgres
  checkpoint_path: null            # null → data/runtime/checkpoint.sqlite
  token_budget:
    max_tokens: 1000000
    warn_threshold: 0.7
    hard_stop_threshold: 1.0
  tool_output:
    externalize_min_chars: 30000
    outputs_dir: data/runtime/tool-outputs
```

## 边界与限制

- **不抄 deer-flow MemoryMiddleware**:记忆走 Hyperion 自有的 `MemoryService`(见 [../services/memory.md](../services/memory.md)),不进中间件链。
- 中间件 pull-by-need:当前默认 4 个 + 条件 Summarization;链 > 7 再考虑移植 deer-flow 的 `@Next/@Prev` 跳转。
- postgres checkpointer 留待后续;v1 用 sqlite。

## 示例

```python
from hyperion.platform.models import create_chat_model
from hyperion.platform.runtime import (
    create_hyperion_agent, build_default_middlewares, get_checkpointer,
)
from langchain_core.tools import tool

@tool
def grep_symbol(q: str) -> str: ...

model = create_chat_model(role="default")
mw = build_default_middlewares(model, turn_budget={"max_turns": 20})
agent = create_hyperion_agent(
    model, [grep_symbol],
    system_prompt="...",
    middleware=mw,
    checkpointer=get_checkpointer(),
)
```

## See Also

- [configuration.md](../configuration.md) §runtime
- [../workflows/deep-research.md](../workflows/deep-research.md) — 用 `create_hyperion_agent` 起 ReAct 子 agent
- [../services/memory.md](../services/memory.md) — 记忆为何不进中间件
- [../../CLAUDE.md](../../../CLAUDE.md) §扩展性(runtime 中间件策略)
