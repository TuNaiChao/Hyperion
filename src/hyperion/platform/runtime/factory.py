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
from dataclasses import dataclass
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, SummarizationMiddleware
from langchain.chat_models import BaseChatModel
from langgraph.graph.state import CompiledStateGraph

from hyperion.platform.runtime.middlewares.loop_detection import LoopDetectionMiddleware
from hyperion.platform.runtime.middlewares.token_budget import TokenBudgetConfig, TokenBudgetMiddleware
from hyperion.platform.runtime.middlewares.tool_output import ToolOutputBudgetConfig, ToolOutputBudgetMiddleware
from hyperion.platform.runtime.middlewares.turn_budget import TurnBudgetConfig, TurnBudgetMiddleware
from hyperion.platform.runtime.state import HyperionState


@dataclass
class SummarizationConfig:
    """摘要触发的配置(token 感知,对标 deer-flow config.example.yaml:1563 trigger=tokens:32000)。

    面向小白:Lead agent(深度调研那种长 agent)聊到几十轮,历史消息会越来越长、把模型上下文窗口撑爆。
    SummarizationMiddleware 会在「该压的时候」把旧消息压成一段摘要,腾出空间。**这个 dataclass 只管「何时压、压完留多少」**。

    - trigger_tokens:历史累计 token 估算 ≥ 此值就压(不是按消息条数——50 条可能才几千 token 白压一次,
      也可能已爆窗口;按真实 token 量才准)。用 `("tokens", N)` 而非 `("fraction",F)`:实测 Hyperion 的
      ChatOpenAI 模型 profile=None,fraction 构造会直接 raise ValueError(见 summarization.py 末尾校验)。
    - keep_messages:压完保留最近 N 条(给模型连续性);langchain 默认 20,实测够用。

    对齐 deer-flow 生产默认(32000 token)+ Anthropic「留 5% 余量」精神。不进 config.yaml:token_budget/tool_output
    的 yaml 配当前都没 wire 进 build_default_middlewares,进 yaml = 造死配置(对齐 turn_budget 先例,代码内传参)。
    """

    enabled: bool = True  # False → 不挂 SummarizationMiddleware(冒烟/测试想禁摘要的扩展口)
    trigger_tokens: int = 32_000  # token 数 ≥ 此值 → 触发摘要(deer-flow 生产默认)
    keep_messages: int = 20  # 压缩后保留最近 N 条(langchain 默认 20)


def build_default_middlewares(
    model: BaseChatModel | None = None,
    *,
    token_budget: TokenBudgetConfig | None = None,
    tool_output: ToolOutputBudgetConfig | None = None,
    turn_budget: TurnBudgetConfig | None = None,
    summarization: SummarizationConfig | None = None,
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
       10. Summarization            ← R3.2 已有 + 建议 B token 感知触发(trigger=tokens:32000,对标 deer-flow;没给 model 则跳过)
       11. Memory(auto-inject)      (Hyperion 自建挂 MemoryService;**不抄** deer-flow MemoryMiddleware)
       12. LoopDetection            ← R3.2 已有(单层 hash 最小版;pull-by-need:频次层/stop_reason)
       13. TurnBudget               ← R3.2.x P1 已有(轮数闸:warn@N-1 + strip@N;管"每轮换工具的良性探索")
       14. TokenBudget              ← R3.0 已有(外层兜底总账)
       15. (tail) Clarification     (若做交互式,必须最后)
    规则:链 >7 个时移植 deer-flow @Next/@Prev(factory.py:357-430)让中间件自声明位置;现在普通有序 list 够。
    """
    chain: list[AgentMiddleware] = [
        ToolOutputBudgetMiddleware(tool_output) if tool_output else ToolOutputBudgetMiddleware(),
    ]
    if model is not None:
        # SummarizationMiddleware 要模型来压历史;没给 model 就跳过(冒烟 / 测试)。
        sc = summarization or SummarizationConfig()
        if sc.enabled:
            # token 感知触发(对标 deer-flow tokens:32000):按真实 token 量压,不按消息条数——
            # 50 条可能才几千 token 白压,也可能已爆窗口。见 SummarizationConfig docstring。
            chain.append(
                SummarizationMiddleware(
                    model,
                    trigger=("tokens", sc.trigger_tokens),
                    keep=("messages", sc.keep_messages),
                )
            )
    chain.append(LoopDetectionMiddleware())
    # TurnBudget(轮数闸):默认宽 max_turns=50;research 子 agent 显式传紧配置(见 _research.py)
    chain.append(TurnBudgetMiddleware(turn_budget) if turn_budget else TurnBudgetMiddleware())
    chain.append(TokenBudgetMiddleware(token_budget) if token_budget else TokenBudgetMiddleware())
    return chain


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
        middleware = build_default_middlewares(model)
    return create_agent(
        model,
        tools,
        system_prompt=system_prompt,
        middleware=middleware,
        state_schema=state_schema,
        checkpointer=checkpointer,
        name=name,
    )
