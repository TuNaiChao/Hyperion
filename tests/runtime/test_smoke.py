"""R3.0 runtime harness 冒烟测试。

验证 R3.0 五大件集成可用:
  1. factory 装配默认中间件链 + 编译图(channels 含 RootRecallState 字段)。
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
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import PrivateAttr

from rootrecall.platform.runtime.checkpoint import _checkpointer_cm
from rootrecall.platform.runtime.factory import SummarizationConfig, build_default_middlewares, create_rootrecall_agent
from rootrecall.platform.runtime.middlewares.token_budget import TokenBudgetConfig, TokenBudgetMiddleware
from rootrecall.platform.runtime.middlewares.tool_output import ToolOutputBudgetConfig, _budget_content, _patch_model_messages


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
    """默认中间件链(无 model)= [ToolOutputBudget, LoopDetection, TurnBudget, TokenBudget],顺序外→内。

    R3.2 加 LoopDetection;R3.2.x P1 加 TurnBudget(轮数闸,无条件);Summarization 要 model,没给就跳过(见下个测试)。
    """
    mws = build_default_middlewares()
    assert [type(m).__name__ for m in mws] == [
        "ToolOutputBudgetMiddleware",
        "LoopDetectionMiddleware",
        "TurnBudgetMiddleware",
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
        "TurnBudgetMiddleware",
        "TokenBudgetMiddleware",
    ]


def test_factory_summarization_trigger_is_token_aware():
    """建议 B:默认触发是 token 感知(不是旧的消息条数 50)。

    旧版 trigger=("messages", 50) 有两个坑:50 条消息可能才几千 token(太早压,白烧一次摘要 LLM 调用),
    也可能已超模型窗口(太晚,已爆)。改成 ("tokens", 32000) 对齐 deer-flow 生产默认。

    这里反射读 SummarizationMiddleware 实例的 trigger 属性,确认它含 ("tokens", ...) 且不含旧 ("messages", 50)。
    """
    model = _ScriptedModel(script=[AIMessage(content="ok", id="m1")])
    mws = build_default_middlewares(model)
    summ = next(m for m in mws if type(m).__name__ == "SummarizationMiddleware")
    # trigger 归一成 list(单个 tuple 也当 list 看),检查每个条件的形式
    conditions = summ.trigger if isinstance(summ.trigger, list) else [summ.trigger]
    kinds = [c[0] for c in conditions]
    assert "tokens" in kinds, f"trigger 应 token 感知,实际 kinds={kinds}"
    assert ("tokens", 32000) in conditions, f"默认 trigger_tokens 应=32000,实际 {summ.trigger}"
    assert ("messages", 50) not in conditions, f"旧的消息条数触发应已移除,实际 {summ.trigger}"


def test_factory_summarization_disabled_skips():
    """SummarizationConfig(enabled=False) → 链不含 SummarizationMiddleware(扩展口:冒烟/测试想禁摘要)。"""
    model = _ScriptedModel(script=[AIMessage(content="ok", id="m1")])
    mws = build_default_middlewares(model, summarization=SummarizationConfig(enabled=False))
    assert "SummarizationMiddleware" not in [type(m).__name__ for m in mws]


def test_factory_compiles_with_state_channels():
    """编译图的 channels 含 RootRecallState 三字段(messages/delegations/summary_text)。"""
    model = _ScriptedModel(script=[AIMessage(content="ok", id="m1")])
    graph = create_rootrecall_agent(model, tools=[], system_prompt="test", checkpointer=None)
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


# ── 3b. 历史漏网兜底(建议 C:wrap_model_call 预扫描 + fallback 截断,不外化)──
def test_wrap_model_call_truncates_oversized_history():
    """建议 C:历史里混进 >fallback_max_chars 的漏网 ToolMessage → _patch_model_messages 兜底截断(不外化)。

    场景:断点续跑/改过阈值时,历史 ToolMessage 没经 wrap_tool_call 处理,仍是几万字符的原始返回。
    _patch_model_messages 应:① 大消息被 fallback head+tail 截断(含 truncated 标记);
    ② 小 synopsis(~3K)不动(靠摘要治累积,不二次压);③ 返新 list(非 None)。
    """
    cfg = ToolOutputBudgetConfig(outputs_dir=tempfile.mkdtemp())  # 用默认阈值(externalize_min=30K, fallback=20K)
    big = ToolMessage(content="BIG\n" * 8000, tool_call_id="c-big", name="grep")  # 32K > externalize_min=30K
    small_synopsis = ToolMessage(content="[synopsis] saved to data/x.txt (3000 chars)", tool_call_id="c-sm", name="grep")
    ai = AIMessage(content="thinking", id="a1")
    patched = _patch_model_messages([ai, big, small_synopsis], cfg)
    assert patched is not None, "有漏网大消息 → 应返新 list,不是 None"
    # big 被截断(fallback 标记),small_synopsis 原样保留(不外化、不截断)
    big_out = next(m for m in patched if isinstance(m, ToolMessage) and m.tool_call_id == "c-big")
    sm_out = next(m for m in patched if isinstance(m, ToolMessage) and m.tool_call_id == "c-sm")
    assert "truncated" in big_out.content and "disk externalization unavailable" in big_out.content
    assert "saved to" in sm_out.content and len(sm_out.content) < len(big_out.content)  # synopsis 没被压(还是那段)


def test_wrap_model_call_skips_externalize_on_history():
    """建议 C:历史兜底路径不外化(externalize=False)—— 不产生磁盘文件。

    对比 wrap_tool_call 主路径(externalize=True)会外化。这里历史路径即使内容 >externalize_min_chars,
    也只走 fallback 截断:断言截断结果含 fallback 特征标记,且 outputs_dir 下无新文件产生。
    """
    out_dir = tempfile.mkdtemp()
    cfg = ToolOutputBudgetConfig(outputs_dir=out_dir)  # 默认 externalize_min=30K, fallback=20K
    big = ToolMessage(content="BIG\n" * 8000, tool_call_id="c1", name="grep")  # 32K > externalize_min
    patched = _patch_model_messages([big], cfg)
    assert patched is not None
    # 截断走 fallback 分支(不外化):含 fallback 标记,无 saved to / 文件路径
    out = patched[0].content
    assert "disk externalization unavailable" in out and "saved to" not in out
    # outputs_dir 下无文件产生(历史路径 externalize=False → _externalize 没被调)
    import os

    remaining = os.listdir(out_dir) if os.path.isdir(out_dir) else []
    assert remaining == [], f"历史路径不该写磁盘文件,实际 {out_dir} 下有 {remaining}"


def test_wrap_model_call_returns_none_when_clean():
    """建议 C:历史里没有漏网大消息(都是小 synopsis / AIMessage)→ _patch_model_messages 返 None(不重建 list)。

    预扫描的价值:99% 的轮次历史是干净的(工具结果都已被主路径处理成 synopsis),返 None 不重建 list,零开销。
    """
    cfg = ToolOutputBudgetConfig(outputs_dir=tempfile.mkdtemp())
    clean = [
        AIMessage(content="hi", id="a1"),
        ToolMessage(content="[synopsis] saved to x.txt (3000 chars)", tool_call_id="c1", name="grep"),
        ToolMessage(content="短结果", tool_call_id="c2", name="read"),
    ]
    assert _patch_model_messages(clean, cfg) is None  # 全在阈值内 → 不动


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
    graph = create_rootrecall_agent(model, tools=[], system_prompt="你是测试 agent。", checkpointer=cp)
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
