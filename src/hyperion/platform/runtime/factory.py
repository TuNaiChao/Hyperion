# src/hyperion/platform/runtime/factory.py
"""create_hyperion_agent —— Hyperion lead agent 工厂(瘦身的中间件链 + create_agent)。

这是什么(面向小白):
  把「模型 + 工具 + 中间件链 + 状态 schema + checkpointer」拼成一个可跑的 LangGraph agent。
  对标 deer-flow create_deerflow_agent,但 R3 大幅瘦身(F6):
    - **不移植** _insert_extra / @Next/@Prev 锚点机制(那是给 30+ 中间件排序的;R3 仅 2 个,普通有序 list 够)。
    - **不自写** normalize_middleware_state_schema —— langchain create_agent 已自动合并
      各中间件自带的 state_schema(实测 extra_field 进了 graph.channels)。即:未来 SandboxMiddleware
      在自己类上声明 state_schema,create_agent 自动并入图状态,无需改 HyperionState 或本工厂。
  设计见 docs/设计/runtime-harness-design.md §4.1。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain.chat_models import BaseChatModel
from langgraph.graph.state import CompiledStateGraph

from hyperion.platform.runtime.middlewares.token_budget import TokenBudgetConfig, TokenBudgetMiddleware
from hyperion.platform.runtime.middlewares.tool_output import ToolOutputBudgetConfig, ToolOutputBudgetMiddleware
from hyperion.platform.runtime.state import HyperionState


def build_default_middlewares(
    *,
    token_budget: TokenBudgetConfig | None = None,
    tool_output: ToolOutputBudgetConfig | None = None,
) -> list[AgentMiddleware]:
    """R3.0 默认中间件链(顺序敏感)。

    顺序(面向小白):langgraph 的 after_model 是**反序**派发,注册顺序 = 外→内。
    这里 ToolOutputBudget 先注册(管单次工具输出截断,内层先动手),
    TokenBudget 后注册(管整 run token 总账,外层兜底)。

    ── 将来加中间件的槽位主脊(对标 deer-flow build_lead_runtime_middlewares)──
    链长上来时按这个顺序往本函数插(每条凭真实需求加,pull-by-need;见 runtime-middleware-policy 记忆):
        1. InputSanitization        (最外层;R4 多用户/不信任输入时)
        2. ToolOutputBudget         ← R3.0 已有(内层先截工具输出)
        3. ToolErrorHandling        (R3.2 伴生:工具异常→ToolMessage 不崩)
        4. Sandbox                  (R5 lead 沙箱化时;state 经 state_schema 自动并入)
        5. LLMErrorHandling         (R3.2 伴生:模型瞬时错误兜底)
        6. Authorization/Guardrail  (R4 多用户鉴权)
        7. DynamicContext           (R3.2:注入日期 + 记忆 recall 作 system-reminder)
        8. Skill*                   (若引入 skills:Activation → ToolPolicy)
        9. DurableContext           (R3.2:summary/delegations 投影成隐藏 HumanMessage)
       10. Summarization            (R3.2:压历史;落地后须伴生 DanglingToolCall)
       11. Memory(auto-inject)      (Hyperion 自建挂 MemoryService;**不抄** deer-flow MemoryMiddleware)
       12. LoopDetection            (R3.2:防搜索死循环)
       13. TokenBudget              ← R3.0 已有(外层兜底总账)
       14. (tail) Clarification     (若做交互式,必须最后)
    规则:链 >7 个时移植 deer-flow @Next/@Prev(factory.py:357-430)让中间件自声明位置;现在普通有序 list 够。
    """
    return [
        ToolOutputBudgetMiddleware(tool_output) if tool_output else ToolOutputBudgetMiddleware(),
        TokenBudgetMiddleware(token_budget) if token_budget else TokenBudgetMiddleware(),
    ]


def create_hyperion_agent(
    model: BaseChatModel,
    tools: Sequence[Any],
    *,
    system_prompt: str | None = None,
    middleware: Sequence[AgentMiddleware] | None = None,
    state_schema: type = HyperionState,
    checkpointer: Any | None = None,
    name: str | None = None,
) -> CompiledStateGraph:
    """组装 Hyperion lead agent(深度调研那种长 agent 用它跑)。

    参数:
      model         create_chat_model(...) 产出的 BaseChatModel(platform/models.py)。
      tools         工具列表(@tool 函数 / BaseTool;深度调研挂 nav 工具 + delegate)。
      system_prompt 系统提示(保持静态利于 prefix cache;动态信息走中间件注入)。
      middleware    中间件链;None → 用 build_default_middlewares() 的 R3.0 默认链。
      state_schema  状态 schema,默认 HyperionState。中间件自带的 state_schema 会被
                    create_agent 自动合并(已实测;无需自写 normalize)。
      checkpointer  可选 SqliteSaver(断点续跑);None 则不持久化(测试用 InMemorySaver)。
      name          agent 名(Langfuse trace / 日志用)。
    """
    if middleware is None:
        middleware = build_default_middlewares()
    return create_agent(
        model,
        tools,
        system_prompt=system_prompt,
        middleware=middleware,
        state_schema=state_schema,
        checkpointer=checkpointer,
        name=name,
    )
