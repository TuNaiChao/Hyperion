# src/hyperion/platform/runtime/middlewares/turn_budget.py
"""TurnBudgetMiddleware —— 每 run ReAct 轮数闸(计轮 + 软警告 + 硬停)。

这是什么(面向小白)
  agent(尤其是 reasoning 模型)有时一直"再看一眼代码"停不下来:每轮调个不同工具,
  良性探索但永远不写最终答复。TokenBudget 管的是 token 总账,LoopDetection 管的是"重复
  调同一工具"——这种"每轮换不同工具的良性探索"两个都不触发,会一路撞到 LangGraph 的
  recursion_limit 硬墙(GraphRecursionError 一抛,ainvoke 把已收集的证据全丢)。

  本中间件补上"轮数"这一轴(对标 TokenBudget 的 warn+hard 两段模式,只是计量从 token
  换成轮):
    - 第 warn_turns 轮(默认 max_turns - 1)→ 排队一条「立刻收尾,别再调工具」提示,
      下一轮注入(保 AIMessage→ToolMessage 配对,原理同 TokenBudget)。
    - 第 max_turns 轮 → 若模型仍吐 tool_calls,剥掉 + finish_reason 改 stop,run 自然结束。
  常规情形:warn 在 max_turns-1 轮提醒 → 模型在 max_turns 轮自己吐最终 JSON → 干净自收尾,
  无异常、无丢证据、无裸模型重述(比 astream+catch+裸模型 和 deer-flow 的 catch+抢残文都优)。

  recursion_limit 仍设 (max_turns + 2) * 2 留 margin,GraphRecursionError catch 降为
  永不触发的兜底(见 _research.py)。

关键正确性(学 TokenBudget / LoopDetection)
  - **按 thread_id 分桶**(同 LoopDetection,不按 run_id)。runtime.context["thread_id"] 来自
    RunnableConfig["configurable"]["thread_id"],整个 run 稳定不变;而 Runtime 对象本身每个
    superstep 可能新建,id(runtime) 不稳。**踩坑:早版按 run_id 分桶 + id(runtime) 兜底 ——
    research 子 agent 的 config 只设了 thread_id、没 run_id,于是永远走 id(runtime) 兜底,
    每轮 id 变 → 计数每轮清零 → 永远到不了 max_turns → 全撞 recursion_limit**(白加了闸)。
  - warn 不能在 after_model 直接注入 HumanMessage(会塞进 assistant tool_calls 和它的
    ToolMessage 之间,严格后端拒下一轮)→ 排队,等下一次 wrap_model_call(那时上一轮
    ToolMessage 已就位)再追加到消息列表末尾。
  - 硬停原因不被 after_agent 清(让调用方能 consume_stop_reason 区分"被轮数截"vs"干净完成")。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.runtime import Runtime

from hyperion.platform.runtime._bounded_dict import BoundedDict

logger = logging.getLogger(__name__)


# ── 配置 ──
@dataclass
class TurnBudgetConfig:
    """轮数预算配置。

    max_turns   单次 run 最多几轮 ReAct(模型调一次工具 + 工具回 = 一轮)。
    warn_turns  第几轮开始警告;None → max_turns - 1(留 1 轮给模型收尾)。
    """

    enabled: bool = True
    max_turns: int = 50           # 默认宽:lead agent / 通用场景够用;research 子 agent 显式给紧(如 12-20)
    warn_turns: int | None = None  # None → max_turns - 1


_TURN_WARN_MSG = (
    "[TURN BUDGET WARNING] You are about to run out of tool-call turns ({used} of {max} used). "
    "Stop calling tools now and produce your final answer based on the results collected so far."
)
_TURN_EXCEEDED_MSG = "[TURN BUDGET EXCEEDED] Tool-call turn limit ({max}) reached. Producing final answer with results collected so far."


class TurnBudgetMiddleware(AgentMiddleware[AgentState]):
    """每 run 轮数闸:计 after_model 次数(=ReAct 轮)+ 软警告 + 硬停剥 tool_calls。"""

    def __init__(self, config: TurnBudgetConfig | None = None) -> None:
        super().__init__()
        self._config = config if config is not None else TurnBudgetConfig()
        self._lock = threading.Lock()
        # 全部按 thread_id 分桶(thread_id 整 run 稳定;见模块 docstring 的踩坑说明)
        self._turn_count: BoundedDict[str, int] = BoundedDict(1000)
        self._warned: BoundedDict[str, bool] = BoundedDict(1000)
        self._pending_warnings: BoundedDict[str, list[str]] = BoundedDict(1000)
        # 硬停原因(不被 after_agent 清,让调用方能 consume_stop_reason)
        self._stop_reason: BoundedDict[str, str] = BoundedDict(1000)

    def reset(self) -> None:
        with self._lock:
            for d in (self._turn_count, self._warned, self._pending_warnings, self._stop_reason):
                d.clear()

    def consume_stop_reason(self, thread_id: str | None) -> str | None:
        """弹出本次 run 的硬停原因("turn_capped" 或 None)。调用方跑完读它区分"被轮数截"vs"干净完成"。"""
        if thread_id is None:
            return None
        with self._lock:
            return self._stop_reason.pop(thread_id, None)

    @staticmethod
    def _thread_id(runtime: Runtime) -> str:
        """从 runtime.context 取 thread_id 整 run 稳定的分桶键(同 LoopDetection)。

        thread_id 来自 RunnableConfig["configurable"]["thread_id"],整个 run 不变;无则 "default"
        (单测 / 嵌入式无 config 时)。**不要**用 id(runtime) —— Runtime 对象每 superstep 可能新建。
        """
        tid = runtime.context.get("thread_id") if runtime.context else None
        return str(tid) if tid else "default"

    def _warn_turn(self) -> int:
        """实际生效的警告轮数(None → max_turns - 1;且夹在 [1, max_turns-1])。"""
        wt = self._config.warn_turns if self._config.warn_turns is not None else self._config.max_turns - 1
        return max(1, min(wt, self._config.max_turns - 1))

    def _clear_run_state(self, tid: str) -> None:
        with self._lock:
            self._turn_count.pop(tid, None)
            self._warned.pop(tid, None)
            self._pending_warnings.pop(tid, None)

    @override
    def before_agent(self, state: AgentState, runtime: Runtime) -> None:
        if not self._config.enabled:
            return
        tid = self._thread_id(runtime)
        with self._lock:
            self._turn_count.setdefault(tid, 0)
            self._warned.setdefault(tid, False)

    @override
    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> None:
        self.before_agent(state, runtime)

    @override
    def after_agent(self, state: AgentState, runtime: Runtime) -> None:
        if not self._config.enabled:
            return
        self._clear_run_state(self._thread_id(runtime))  # 不清 _stop_reason(调用方还要读)

    @override
    async def aafter_agent(self, state: AgentState, runtime: Runtime) -> None:
        self.after_agent(state, runtime)

    @staticmethod
    def _append_text(content: Any, stop_msg: str) -> Any:
        """把停止提示追加到 AIMessage.content(兼容 str / list[内容块] / None)。"""
        if content is None:
            return stop_msg
        if isinstance(content, str):
            return f"{content}\n\n{stop_msg}" if content else f"\n\n{stop_msg}"
        # list[str | dict](None/str 已被上面两支排除)
        return [*content, {"type": "text", "text": f"\n\n{stop_msg}"}]

    def _build_hard_stop_update(self, msg: AIMessage, stop_msg: str) -> dict[str, Any]:
        """硬停的状态更新:剥 tool_calls + finish_reason→stop + 追加停止说明。"""
        updated_content = self._append_text(msg.content, stop_msg)
        kwargs = dict(msg.additional_kwargs) if msg.additional_kwargs else {}
        kwargs.pop("tool_calls", None)
        kwargs.pop("function_call", None)
        response_metadata = dict(getattr(msg, "response_metadata", {}) or {})
        if response_metadata.get("finish_reason") == "tool_calls":
            response_metadata["finish_reason"] = "stop"
        stopped_msg = msg.model_copy(
            update={
                "content": updated_content,
                "tool_calls": [],
                "additional_kwargs": kwargs,
                "response_metadata": response_metadata,
            }
        )
        return {"messages": [stopped_msg]}

    def _apply(self, state: AgentState, runtime: Runtime) -> dict | None:
        """核心:每轮 after_model 计数 +1,判阈,返硬停更新 / None(警告只入队,不改 state)。"""
        if not self._config.enabled:
            return None
        messages = state.get("messages", [])
        if not messages:
            return None
        last_msg = messages[-1]
        if not isinstance(last_msg, AIMessage):
            return None
        tid = self._thread_id(runtime)
        with self._lock:
            count = self._turn_count.get(tid, 0) + 1
            self._turn_count[tid] = count

            # 硬停:已达 max_turns 且这轮仍要调工具 → 剥 tool_calls,loop 自然终止(不抛异常)
            if count >= self._config.max_turns:
                if getattr(last_msg, "tool_calls", None):
                    logger.warning("Turn budget hard stop: thread %s at turn %d/%d", tid, count, self._config.max_turns)
                    self._stop_reason[tid] = "turn_capped"
                    ctx = getattr(runtime, "context", None)
                    if isinstance(ctx, dict):
                        ctx["stop_reason"] = "turn_capped"  # lead worker 不持中间件引用也能读(#4176 同款)
                    stop_text = _TURN_EXCEEDED_MSG.format(max=self._config.max_turns)
                    return self._build_hard_stop_update(last_msg, stop_text)
                # 已达上限但这轮没 tool_calls(模型自己收尾了)→ 正常结束,不干预
                return None

            # 软警告:到 warn_turns → 排队,下轮 wrap_model_call 注入
            if count >= self._warn_turn() and not self._warned.get(tid, False):
                self._warned[tid] = True
                warn_text = _TURN_WARN_MSG.format(used=count, max=self._config.max_turns)
                logger.info("Turn budget warning: thread %s at turn %d/%d", tid, count, self._config.max_turns)
                self._pending_warnings.setdefault(tid, []).append(warn_text)
            return None

    @override
    def after_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self._apply(state, runtime)

    @override
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self._apply(state, runtime)

    def _drain_pending_warnings(self, runtime: Runtime) -> list[str]:
        if not self._config.enabled:
            return []
        tid = self._thread_id(runtime)
        with self._lock:
            return self._pending_warnings.pop(tid, None) or []

    def _inject_warnings(self, request: ModelRequest, warnings: list[str]) -> ModelRequest:
        """把累积的警告拼成一条 HumanMessage(name=turn_budget_warning)追加到请求末尾。"""
        if not warnings:
            return request
        warn_msg = HumanMessage(content="\n\n".join(warnings), name="turn_budget_warning")
        messages = getattr(request, "messages", [])
        return request.override(messages=[*messages, warn_msg])

    @override
    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelCallResult:
        warnings = self._drain_pending_warnings(request.runtime)
        request = self._inject_warnings(request, warnings)
        return handler(request)

    @override
    async def awrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]) -> ModelCallResult:
        warnings = self._drain_pending_warnings(request.runtime)
        request = self._inject_warnings(request, warnings)
        return await handler(request)
