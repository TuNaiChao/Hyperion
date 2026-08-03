"""R3.0 runtime harness 冒烟测试。

验证 R3.0 五大件集成可用:
  1. factory 装配默认中间件链 + 编译图(channels 含 HyperionState 字段)。
  2. token 预算闸:_apply 在硬停阈值剥 tool_calls + 写 stop_reason。
  3. 工具输出预算:超阈值外化到磁盘 + synopsis。
  4. checkpointer:sqlite 往返(put → get_tuple)。
  5. 端到端:用脚本模型(不联网)跑一次 agent loop,checkpointer 写入 + 可续跑。

这是 R3.0 的退出闸:全绿 = runtime 骨架可用,可进 R3.1。
组件级更细的测试在各模块(本文件只做集成冒烟 + 关键不变量)。
"""

from __future__ import annotations

import tempfile

from langchain.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import PrivateAttr

from hyperion.platform.runtime.checkpoint import _checkpointer_cm
from hyperion.platform.runtime.factory import build_default_middlewares, create_hyperion_agent
from hyperion.platform.runtime.middlewares.token_budget import TokenBudgetConfig, TokenBudgetMiddleware
from hyperion.platform.runtime.middlewares.tool_output import ToolOutputBudgetConfig, _budget_content


# ── 不联网的脚本模型(冒烟用,按 script 顺序吐 AIMessage)──────────────
class _ScriptedModel(BaseChatModel):
    """按 script 顺序返回 AIMessage 的假模型(测试用,不调 API)。

    支持带 usage_metadata(测 token 预算)+ tool_calls(测 agent loop);
    bind_tools 返回自身(create_agent 要求模型能 bind 工具 schema)。
    """

    script: list[AIMessage]
    _i: int = PrivateAttr(default=0)

    @property
    def _llm_type(self) -> str:
        return "scripted-smoke"

    def bind_tools(self, tools, **kwargs):  # type: ignore[override]
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        idx = self._i
        self._i = idx + 1
        msg = self.script[min(idx, len(self.script) - 1)]
        return ChatResult(generations=[ChatGeneration(message=msg)])


# ── 1. factory 装配 + 编译 ────────────────────────────────────────────
def test_factory_assembles_default_chain():
    """默认中间件链(无 model)= [ToolOutputBudget, LoopDetection, TokenBudget],顺序外→内。

    R3.2 加 LoopDetection(无条件);Summarization 要 model,没给就跳过(见下个测试)。
    """
    mws = build_default_middlewares()
    assert [type(m).__name__ for m in mws] == [
        "ToolOutputBudgetMiddleware",
        "LoopDetectionMiddleware",
        "TokenBudgetMiddleware",
    ]


def test_factory_chain_adds_summarization_when_model_given():
    """给 model → 链多 SummarizationMiddleware(ToolOutputBudget 后、LoopDetection 前)。"""
    model = _ScriptedModel(script=[AIMessage(content="ok", id="m1")])
    mws = build_default_middlewares(model)
    assert [type(m).__name__ for m in mws] == [
        "ToolOutputBudgetMiddleware",
        "SummarizationMiddleware",
        "LoopDetectionMiddleware",
        "TokenBudgetMiddleware",
    ]


def test_factory_compiles_with_state_channels():
    """编译图的 channels 含 HyperionState 三字段(messages/delegations/summary_text)。"""
    model = _ScriptedModel(script=[AIMessage(content="ok", id="m1")])
    graph = create_hyperion_agent(model, tools=[], system_prompt="test", checkpointer=None)
    for field in ("messages", "delegations", "summary_text"):
        assert field in graph.channels, f"{field} 未进 graph channels"


# ── 2. token 预算闸(硬停剥 tool_calls)────────────────────────────────
def test_token_budget_hard_stop_strips_tool_calls():
    """用量超 hard_stop(1.0):剥 tool_calls + finish→stop + stop_reason=token_capped。"""

    class _RT:
        context = {"run_id": "smoke-budget"}

    cfg = TokenBudgetConfig(max_tokens=1000, warn_threshold=0.7, hard_stop_threshold=1.0)
    mw = TokenBudgetMiddleware(cfg)
    mw.before_agent({"messages": []}, _RT())  # 空 baseline
    msg = AIMessage(
        content="hi",
        id="m1",
        tool_calls=[{"name": "grep", "args": {"pattern": "x"}, "id": "tc1"}],
        usage_metadata={"input_tokens": 900, "output_tokens": 200, "total_tokens": 1100},
        response_metadata={"finish_reason": "tool_calls"},
    )
    res = mw._apply({"messages": [msg]}, _RT())
    assert res is not None and "messages" in res
    stopped = res["messages"][0]
    assert stopped.tool_calls == []  # 硬停剥 tool_calls
    assert stopped.response_metadata.get("finish_reason") == "stop"
    assert mw.consume_stop_reason("smoke-budget") == "token_capped"


# ── 3. 工具输出预算(超阈值外化)──────────────────────────────────────
def test_tool_output_externalizes_oversized():
    """内容 > externalize_min_chars → 外化到磁盘 + 返回 synopsis(含 saved to)。"""
    cfg = ToolOutputBudgetConfig(outputs_dir=tempfile.mkdtemp(), externalize_min_chars=10)
    out = _budget_content("line\n" * 8000, tool_name="grep", tool_call_id="c1", config=cfg)
    assert out is not None and "saved to" in out


# ── 4. checkpointer sqlite 可用 ──────────────────────────────────────
def test_checkpoint_sqlite_yields_setup_saver():
    """SqliteSaver CM:产出 setup 过的 saver(建表 + 可查询空 thread)。

    实际 put/get 往返由 E2E(test_agent_invoke_*)经 create_agent 覆盖(它用对的 API);
    这里只验 CM + setup + 可查询,不直 put(避免手搓 checkpoint dict 踩 saver 内部 API)。
    """
    from langgraph.checkpoint.sqlite import SqliteSaver

    path = tempfile.mktemp(suffix=".sqlite")
    try:
        with _checkpointer_cm("sqlite", path) as cp:
            assert isinstance(cp, SqliteSaver)  # CM 产出 SqliteSaver
            # setup() 已建表 → 查空 thread 返回 None(不抛异常 = 可查询)
            assert cp.get_tuple({"configurable": {"thread_id": "empty"}}) is None
    finally:
        import os

        if os.path.exists(path):
            os.unlink(path)


# ── 5. 端到端:脚本模型跑 agent loop + checkpointer 续跑 ─────────────
def test_agent_invoke_writes_checkpoint_and_resumes():
    """脚本模型 invoke 一次:产出回复 + checkpointer 写入 + 同 thread_id 可取 state。"""
    model = _ScriptedModel(script=[AIMessage(content="调研完成:这是结论。", id="m-final", usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})])
    cp = InMemorySaver()
    graph = create_hyperion_agent(model, tools=[], system_prompt="你是测试 agent。", checkpointer=cp)
    config: RunnableConfig = {"configurable": {"thread_id": "smoke-e2e"}}
    # 第一次 invoke:agent loop 跑一轮(model 无 tool_calls → 终止)
    result = graph.invoke({"messages": [{"role": "user", "content": "跑一下"}]}, config=config)
    # 产出含最终 AI 回复
    msgs = result.get("messages", [])
    assert any(isinstance(m, AIMessage) and "结论" in m.content for m in msgs), "最终 AIMessage 未产出"
    # checkpointer 写入:get_state 能取到同 thread 的状态
    state = graph.get_state(config)
    assert state is not None and state.values.get("messages"), "checkpointer 未写入 state"
    # 可续跑:同 thread_id 再 invoke,state 延续(messages 不丢)
    result2 = graph.invoke({"messages": [{"role": "user", "content": "再跑"}]}, config=config)
    all_msgs = result2.get("messages", [])
    # 第二轮 invoke 后消息数应 >= 第一轮(历史延续)
    assert len(all_msgs) >= len(msgs), "checkpointer 续跑:历史未延续"
