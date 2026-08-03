# src/hyperion/platform/runtime/middlewares/token_budget.py
"""TokenBudgetMiddleware —— 每 run token 预算闸(累加 usage + 软警告 + 硬停)。

这是什么(面向小白):
  agent 跑长了(深度调研几千次调用),token 会爆。本中间件每轮模型返回后,把这次的 token 用量
  累加进"本次运行的预算桶",算占比:
    - 占比 ≥ 软警告阈值(默认 0.7)→ 下一轮给模型塞一条「快收尾」提示(HumanMessage)。
    - 占比 ≥ 硬停阈值(默认 1.0)→ 把模型这轮的 tool_calls 剥掉 + finish_reason 改 stop,
      让 agent loop 自然终止、产出当前最佳答复(**不抛异常**)。
  对标 deer-flow TokenBudgetMiddleware,逻辑保持一致(已验证)。设计见 runtime-harness-design.md §4.2。

  关键:① before_agent 把旧消息标"已见",不计入本次 run 预算(只数新增);② BoundedDict(上限 1000)
  防 abandoned run 泄漏;③ 硬停原因记进 _stop_reason + runtime.context,供 executor 读(consume_stop_reason)。
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


# ── 配置 ——
@dataclass
class TokenBudgetConfig:
    """token 预算配置(对标 deer-flow TokenBudgetConfig)。三档上限取用得率最高的那档判阈。"""

    enabled: bool = True
    max_tokens: int = 1_000_000  # 总 token 上限(input+output)
    max_input_tokens: int | None = None  # 可选:单限 input
    max_output_tokens: int | None = None  # 可选:单限 output
    warn_threshold: float = 0.7  # 软警告占比
    hard_stop_threshold: float = 1.0  # 硬停占比


@dataclass
class TokenUsage:
    """单次 run 的累计用量。"""

    input: int = 0
    output: int = 0
    total: int = 0


_BUDGET_WARNING_MSG = (
    "[TOKEN BUDGET WARNING] You have used {used:,} of your {budget:,} {reason} token budget ({percent:.0f}%). Wrap up your current work and produce a final answer. Avoid starting new tool calls unless absolutely necessary."
)
_BUDGET_EXCEEDED_MSG = "[TOKEN BUDGET EXCEEDED] The {reason} token usage ({used:,}) has exceeded the safety limit ({budget:,}). Producing final answer with results collected so far."


class TokenBudgetMiddleware(AgentMiddleware[AgentState]):
    """每 run token 预算闸:累加 usage + 软警告 + 硬停剥 tool_calls。"""

    def __init__(self, config: TokenBudgetConfig | None = None) -> None:
        super().__init__()
        self._config = config if config is not None else TokenBudgetConfig()
        self._lock = threading.Lock()
        # 全部按 run_id 分桶 + 上限 1000(防 abandoned run 泄漏)
        self._warned: BoundedDict[str, bool] = BoundedDict(1000)
        self._pending_warnings: BoundedDict[str, list[str]] = BoundedDict(1000)
        self._seen_messages: BoundedDict[str, dict[str, tuple[int, int]]] = BoundedDict(1000)
        self._cumulative_usage: BoundedDict[str, TokenUsage] = BoundedDict(1000)
        # 硬停原因:不被 after_agent/_clear_run_state 清(让 executor 跑完能读);bounded 防漏
        self._stop_reason: BoundedDict[str, str] = BoundedDict(1000)

    def reset(self) -> None:
        with self._lock:
            for d in (self._warned, self._pending_warnings, self._seen_messages, self._cumulative_usage, self._stop_reason):
                d.clear()

    def consume_stop_reason(self, run_id: str | None) -> str | None:
        """弹出本次 run 的硬停原因("token_capped" 或 None)。executor 跑完调它区分"被预算截"vs"干净完成"。"""
        if run_id is None:
            return None
        with self._lock:
            return self._stop_reason.pop(run_id, None)

    @staticmethod
    def _get_run_id(runtime: Runtime) -> str:
        ctx = getattr(runtime, "context", None)
        if isinstance(ctx, dict) and "run_id" in ctx:
            return ctx["run_id"]
        return str(id(runtime))  # 兜底:用 runtime 对象 id,防 embedded client 多 run 串

    def _clear_run_state(self, run_id: str) -> None:
        with self._lock:
            self._warned.pop(run_id, None)
            self._pending_warnings.pop(run_id, None)
            self._seen_messages.pop(run_id, None)
            self._cumulative_usage.pop(run_id, None)

    @override
    def before_agent(self, state: AgentState, runtime: Runtime) -> None:
        """run 开始:把旧消息标"已见",不计入本次预算(只数本轮新增 token)。"""
        if not self._config.enabled:
            return
        messages = state.get("messages", [])
        if not messages:
            return
        run_id = self._get_run_id(runtime)
        with self._lock:
            seen = self._seen_messages.setdefault(run_id, {})
            self._cumulative_usage.setdefault(run_id, TokenUsage())
            for msg in messages:
                if isinstance(msg, AIMessage) and msg.id and hasattr(msg, "usage_metadata"):
                    usage = msg.usage_metadata or {}
                    seen[msg.id] = (usage.get("input_tokens", 0), usage.get("output_tokens", 0))

    @override
    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> None:
        self.before_agent(state, runtime)

    @override
    def after_agent(self, state: AgentState, runtime: Runtime) -> None:
        if not self._config.enabled:
            return
        self._clear_run_state(self._get_run_id(runtime))  # 不清 _stop_reason(executor 还要读)

    @override
    async def aafter_agent(self, state: AgentState, runtime: Runtime) -> None:
        self.after_agent(state, runtime)

    @staticmethod
    def _append_text(content: str | list[str | dict[str, Any]] | None, stop_msg: str) -> str | list[str | dict[str, Any]]:
        """把停止提示追加到 AIMessage.content(兼容 str / list[内容块] / None)。"""
        if content is None:
            return stop_msg
        if isinstance(content, str):
            return f"{content}\n\n{stop_msg}" if content else f"\n\n{stop_msg}"
        # 到这里 content 必为 list[str | dict[str, Any]](None/str 已被上面两支排除)
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
        """核心:累加本轮新增 token,判阈,返回状态更新(硬停)或 None(警告只入队,不改 state)。"""
        if not self._config.enabled:
            return None
        messages = state.get("messages", [])
        if not messages:
            return None
        last_msg = messages[-1]
        if not isinstance(last_msg, AIMessage):
            return None
        run_id = self._get_run_id(runtime)
        with self._lock:
            seen = self._seen_messages.setdefault(run_id, {})
            usage_accum = self._cumulative_usage.setdefault(run_id, TokenUsage())
            for msg in messages:
                if isinstance(msg, AIMessage) and msg.id and hasattr(msg, "usage_metadata"):
                    usage = msg.usage_metadata or {}
                    input_tokens = usage.get("input_tokens", 0)
                    output_tokens = usage.get("output_tokens", 0)
                    prev_input, prev_output = seen.get(msg.id, (0, 0))
                    # 只累加"新增" diff(处理 subagent token 追溯补记的情况)
                    diff_input = max(0, input_tokens - prev_input)
                    diff_output = max(0, output_tokens - prev_output)
                    if diff_input > 0 or diff_output > 0:
                        usage_accum.input += diff_input
                        usage_accum.output += diff_output
                        usage_accum.total += diff_input + diff_output
                        seen[msg.id] = (input_tokens, output_tokens)
            if usage_accum.total <= 0:
                return None
            # 三档上限,取用得率最高的那档
            fractions = [("total", usage_accum.total, self._config.max_tokens)]
            if self._config.max_input_tokens:
                fractions.append(("input", usage_accum.input, self._config.max_input_tokens))
            if self._config.max_output_tokens:
                fractions.append(("output", usage_accum.output, self._config.max_output_tokens))
            highest_fraction = 0.0
            trigger_reason = ""
            trigger_used = 0
            trigger_budget = 0
            for reason, used, limit in fractions:
                frac = used / limit
                if frac > highest_fraction:
                    highest_fraction = frac
                    trigger_reason, trigger_used, trigger_budget = reason, used, limit
            # 硬停:剥 tool_calls,loop 自然终止(不抛异常)
            if highest_fraction >= self._config.hard_stop_threshold:
                logger.warning("Token budget hard stop: run %s %s limit exceeded", run_id, trigger_reason)
                self._stop_reason[run_id] = "token_capped"
                ctx = getattr(runtime, "context", None)
                if isinstance(ctx, dict):
                    ctx["stop_reason"] = "token_capped"  # lead worker 不持中间件引用也能读(#4176 同款)
                stop_text = _BUDGET_EXCEEDED_MSG.format(reason=trigger_reason, used=trigger_used, budget=trigger_budget)
                return self._build_hard_stop_update(last_msg, stop_text)
            # 软警告:只入队,由 wrap_model_call 下轮注入(不改 state,保 AIMessage→ToolMessage 配对)
            if highest_fraction >= self._config.warn_threshold and not self._warned.get(run_id, False):
                self._warned[run_id] = True
                percent = highest_fraction * 100
                warn_text = _BUDGET_WARNING_MSG.format(reason=trigger_reason, used=trigger_used, budget=trigger_budget, percent=percent)
                logger.info("Token budget warning: run %s %s at %.1f%%", run_id, trigger_reason, percent)
                self._pending_warnings.setdefault(run_id, []).append(warn_text)
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
        run_id = self._get_run_id(runtime)
        with self._lock:
            return self._pending_warnings.pop(run_id, None) or []

    def _inject_warnings(self, request: ModelRequest, warnings: list[str]) -> ModelRequest:
        """把累积的警告拼成一条 HumanMessage(name=budget_warning)追加到请求末尾。"""
        if not warnings:
            return request
        warning_msg = HumanMessage(content="\n\n".join(warnings), name="budget_warning")
        messages = getattr(request, "messages", [])
        return request.override(messages=[*messages, warning_msg])

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
