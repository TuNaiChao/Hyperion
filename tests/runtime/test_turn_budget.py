"""TurnBudgetMiddleware 单测 —— 不跑真模型,直接喂 after_model 假 state 验逻辑。

测什么(面向小白)
  TurnBudgetMiddleware 是"轮数闸":第 N-1 轮警告、第 N 轮剥 tool_calls 强制收尾。这里不跑整个
  agent(那要真 LLM),而是手动调 after_model / _drain_pending_warnings / consume_stop_reason,
  验关键行为:① warn 在 N-1 轮排队;② 第 N 轮 strip tool_calls + 标 turn_capped;
  ③ 模型自己收尾(无 tool_calls)时不干预;④ after_agent 清运行态但不清 stop_reason;
  ⑤ 计数按 thread_id 累积,与 runtime 对象身份无关(回归:早版按 id(runtime) 每轮清零撞墙)。
"""

from types import SimpleNamespace

from langchain_core.messages import AIMessage

from rootrecall.platform.runtime.middlewares.turn_budget import (
    TurnBudgetConfig,
    TurnBudgetMiddleware,
)


def _ai_with_tools(content: str = "explore") -> AIMessage:
    """造一条带 tool_calls 的 AIMessage(模拟模型"还想调工具不收尾")。"""
    return AIMessage(
        content=content,
        tool_calls=[{"name": "grep_symbol", "args": {"name": "x"}, "id": "tc1"}],
    )


def _state(msg: AIMessage) -> dict:
    return {"messages": [msg]}


def _runtime(thread_id: str = "t1") -> SimpleNamespace:
    """假 runtime:带 thread_id 的 context(中间件靠它分桶 + 回写 stop_reason)。

    thread_id 是生产真值(LangGraph 把 RunnableConfig.configurable.thread_id 注入 runtime.context),
    整 run 稳定;**别**用 run_id(生产子 agent 没设)。
    """
    return SimpleNamespace(context={"thread_id": thread_id})


def test_warn_at_n_minus_1_then_strip_at_n():
    """max_turns=3 → warn@2(排队)、strip@3(剥 tool_calls + 标 turn_capped)。"""
    mw = TurnBudgetMiddleware(TurnBudgetConfig(max_turns=3))  # warn_turns=None → max_turns-1=2
    rt = _runtime()
    mw.before_agent({"messages": []}, rt)

    # 第 1 轮:不到 warn、不到 strip → 返 None,无待发警告
    assert mw.after_model(_state(_ai_with_tools()), rt) is None
    assert mw._drain_pending_warnings(rt) == []

    # 第 2 轮:到 warn_turns → 排队警告(不改 state)
    assert mw.after_model(_state(_ai_with_tools()), rt) is None
    warn = mw._drain_pending_warnings(rt)
    assert len(warn) == 1 and "TURN BUDGET WARNING" in warn[0]

    # 第 3 轮:到 max_turns 且仍调工具 → strip
    update = mw.after_model(_state(_ai_with_tools()), rt)
    assert update is not None
    stopped = update["messages"][0]
    assert stopped.tool_calls == []
    assert mw.consume_stop_reason("t1") == "turn_capped"
    # stop_reason 也写到 runtime.context,让不持中间件引用的调用方能读(#4176 同款)
    assert rt.context.get("stop_reason") == "turn_capped"


def test_no_strip_when_model_self_finishes():
    """已达 max_turns 但这轮没 tool_calls(模型自己吐最终文本)→ 不干预,返 None,无 stop_reason。"""
    mw = TurnBudgetMiddleware(TurnBudgetConfig(max_turns=2))
    rt = _runtime()
    mw.before_agent({"messages": []}, rt)

    mw.after_model(_state(_ai_with_tools()), rt)  # turn 1:调工具
    # turn 2:模型收尾(无 tool_calls)
    assert mw.after_model(_state(AIMessage(content='{"summary":"done"}', tool_calls=[])), rt) is None
    assert mw.consume_stop_reason("t1") is None


def test_disabled_is_noop():
    """enabled=False:计数 / warn / strip 全不触发。"""
    mw = TurnBudgetMiddleware(TurnBudgetConfig(enabled=False, max_turns=1))
    rt = _runtime()
    mw.before_agent({"messages": []}, rt)
    assert mw.after_model(_state(_ai_with_tools()), rt) is None
    assert mw.consume_stop_reason("t1") is None


def test_consume_stop_reason_pops():
    """consume_stop_reason 弹出后第二次返 None(不残留,不串 thread)。"""
    mw = TurnBudgetMiddleware(TurnBudgetConfig(max_turns=1))
    rt = _runtime()
    mw.before_agent({"messages": []}, rt)
    mw.after_model(_state(_ai_with_tools()), rt)
    assert mw.consume_stop_reason("t1") == "turn_capped"
    assert mw.consume_stop_reason("t1") is None


def test_after_agent_clears_run_state_not_stop_reason():
    """after_agent 清轮数/警告运行态,但不清 stop_reason(调用方跑完才读)。"""
    mw = TurnBudgetMiddleware(TurnBudgetConfig(max_turns=1))
    rt = _runtime()
    mw.before_agent({"messages": []}, rt)
    mw.after_model(_state(_ai_with_tools()), rt)  # 触发 strip + 记 stop_reason
    mw.after_agent({"messages": []}, rt)  # run 结束:清运行态
    # stop_reason 仍在(没被清)
    assert mw.consume_stop_reason("t1") == "turn_capped"


def test_counting_keys_on_thread_id_not_object_identity():
    """回归:LangGraph 每个 superstep 可能注入【新的】Runtime 对象,id(runtime) 会变;但同一 run
    的 thread_id 不变。计数必须按 thread_id 累积,否则每轮清零 → 永远到不了 max_turns → 全撞
    recursion_limit(这正是 P1 e2e 8/8 撞墙的根因:早版按 run_id/id(runtime) 分桶)。

    本测每次 after_model 都造新 runtime 对象(仅 thread_id 相同),max_turns=2 → 第 2 次必须 strip。
    若回退成 id(runtime) 分桶,本测必挂(每次 id 不同 → 计数恒为 1)。
    """
    mw = TurnBudgetMiddleware(TurnBudgetConfig(max_turns=2))
    mw.before_agent({"messages": []}, _runtime())  # 又一个新对象
    # turn 1:新 runtime 对象
    assert mw.after_model(_state(_ai_with_tools()), _runtime()) is None
    # turn 2:又一个新对象 —— 按 thread_id 累积到 2 → strip;按 id(runtime) 会算成 1 → 不 strip(挂)
    update = mw.after_model(_state(_ai_with_tools()), _runtime())
    assert update is not None
    assert update["messages"][0].tool_calls == []
    assert mw.consume_stop_reason("t1") == "turn_capped"
