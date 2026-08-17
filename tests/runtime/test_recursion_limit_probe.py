"""集成探针:锁 _research._recursion_limit_for 的 superstep 不变量(踩坑 #9 的永久护盾)。

为什么需要这个测(背景)
  _research_one_module 给子 agent 设的 recursion_limit 必须够大,让 TurnBudgetMiddleware 在
  max_turns 轮 strip 收尾、recursion 硬墙不抢先撞。早版公式按"每轮 2 superstep"算(踩坑 #9),
  漏算了"每个中间件 hook 是独立图节点"(每轮真实 N+2),5 中间件下 limit 太小 → TurnBudget 永远
  到不了 max_turns → 全撞 GraphRecursionError。手搓桩的单测不复刻真 agent 生命周期,抓不到这个
  (见 test_turn_budget.py —— 它直调 after_model,不走真 graph)。

  本测用真 create_agent + 全默认中间件链 + 桩模型(永吐 tool_call),几秒确定性证明:TurnBudget
  在 max_turns 轮干净 strip 收尾、不撞墙。谁加/减中间件没同步公式 → 本测挂(无 turn_capped 或抛异常)。
"""

from __future__ import annotations

from langchain.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from pydantic import PrivateAttr

from rootrecall.platform.runtime.factory import build_default_middlewares, create_rootrecall_agent
from rootrecall.platform.runtime.middlewares.turn_budget import TurnBudgetConfig, TurnBudgetMiddleware
from rootrecall.workflows.deep_research._research import _recursion_limit_for


# ── 桩:不联网,永远吐一个 echo tool_call ──────────────────────────────
@tool
def echo(n: int) -> str:
    """回显 n(测试占位工具,给桩模型的 tool_call 一个着落)。"""
    return f"echo:{n}"


class _AlwaysToolModel(BaseChatModel):
    """永远吐一个 echo tool_call 的假模型(集成测用,不联网)。

    每轮 args 故意变化(n 递增)→ LoopDetection 不会把它当"重复同工具同参数"的循环(隔离 TurnBudget,
    避免两个中间件打架);真实 research 子 agent 也是每轮换不同工具/参数的良性探索。
    """

    _i: int = PrivateAttr(default=0)

    @property
    def _llm_type(self) -> str:
        return "always-tool-probe"

    def bind_tools(self, tools, **kwargs):  # type: ignore[override]
        return self  # 桩无视 schema,反正是硬编码 tool_call

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        i = self._i
        self._i = i + 1
        msg = AIMessage(
            content=f"turn {i}",
            tool_calls=[{"name": "echo", "args": {"n": i}, "id": f"tc{i}"}],
            id=f"m{i}",
        )
        return ChatResult(generations=[ChatGeneration(message=msg)])


# ── 1. 集成:TurnBudget 在 max_turns 轮 strip,不撞 recursion 硬墙 ─────────
def test_recursion_limit_keeps_turn_budget_from_being_preempted():
    """真 create_agent + 全默认中间件链 + 桩模型 → TurnBudget 在 max_turns 轮 strip,**不**撞 recursion。

    锁的是 ``_recursion_limit_for(max_turns, len(middleware))``:若公式算小了(谁加中间件没同步),
    recursion_limit 会在 TurnBudget 的 max_turns 之前先撞 → 本测要么 ``.invoke`` 抛 GraphRecursionError、
    要么 ``consume_stop_reason`` 拿不到 turn_capped → 挂。这是 _research_one_module 的 superstep 不变量护盾。
    """
    max_turns = 3
    model = _AlwaysToolModel()
    # 复刻 _research_one_module 的真实组装:默认链 + 紧 TurnBudget(全 5 个中间件)
    middleware = build_default_middlewares(model, turn_budget=TurnBudgetConfig(max_turns=max_turns))
    assert any(isinstance(m, TurnBudgetMiddleware) for m in middleware), "默认链该含 TurnBudget"
    recursion_limit = _recursion_limit_for(max_turns, len(middleware))

    agent = create_rootrecall_agent(model, [echo], system_prompt="probe", middleware=middleware, name="probe-tb")
    cfg = {"configurable": {"thread_id": "probe-tb"}, "recursion_limit": recursion_limit}

    # 跑到 TurnBudget strip 收尾(若 recursion_limit 太小 → .invoke 抛 GraphRecursionError → 测挂)
    result = agent.invoke({"messages": [HumanMessage(content="go")]}, config=cfg)

    # TurnBudget 在 max_turns 轮 strip 了:末条 AI 无 tool_calls + 内容带 TurnBudget 停止标记。
    # (桩模型永吐 tool_call 不会自收尾;LoopDetection 因 args 每轮变不触发硬停 → 唯一终结者 = TurnBudget。
    # 注:configurable.thread_id 不会自动进 runtime.context(本 langgraph 版),TurnBudget 走 "default"
    # 兜底分桶——生产 e2e 同样如此,靠每模块各起一个中间件实例隔离;故这里不断 stop_reason 的 key,
    # 改断结果,更稳。)
    last_ai = next(m for m in reversed(result["messages"]) if isinstance(m, AIMessage))
    assert last_ai.tool_calls == [], "TurnBudget 没在 max_turns 轮剥 tool_calls(被递归墙抢先?)"
    assert "TURN BUDGET EXCEEDED" in str(last_ai.content), "末条非 TurnBudget 停止标记"


# ── 2. 纯公式单测:中间件/max_turns 变多 → limit 单调增;生产值锚定 ────────
def test_recursion_limit_formula_grows_with_complexity():
    """公式随中间件数 / max_turns 单调增(加中间件不能让 limit 变小);锚定研究子 agent 生产值。"""
    # 研究子 agent 生产值:max_turns=20、5 中间件(ToolOutputBudget+Summarization+LoopDetection+TurnBudget+TokenBudget)
    assert _recursion_limit_for(20, 5) == 20 * (5 + 2) + 2 * 5 + 20  # = 170
    # 小例(同上集成测)
    assert _recursion_limit_for(3, 5) == 3 * 7 + 10 + 20  # = 51
    # 中间件变多 → limit 单调增(加中间件不能让 limit 变小,否则 TurnBudget 更易被抢撞)
    assert _recursion_limit_for(10, 6) > _recursion_limit_for(10, 5)
    # max_turns 变大 → limit 单调增
    assert _recursion_limit_for(20, 5) > _recursion_limit_for(10, 5)
    # 永远 > max_turns × (N+2)(否则就是在 TurnBudget 的 max_turns 之前撞墙的硬证)
    for n_mw in (3, 5, 8):
        assert _recursion_limit_for(20, n_mw) > 20 * (n_mw + 2)
