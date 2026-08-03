# src/hyperion/platform/runtime/middlewares/loop_detection.py
"""LoopDetectionMiddleware —— 检测并打断重复 tool_call 循环(R3.2 最小版)。

这是什么(面向小白)
  agent 有时会陷进死循环:反复用同样参数调同一个工具(比如一直 read_file 同一文件),烧 token
  直到 token 预算把它截停。本中间件更早发现:每轮模型回完,把这轮的 tool_calls 集合算个 hash,
  在滑动窗口里数它重复了几次:
    - 重复 ≥ warn_threshold(默认 3)→ 下一轮塞条「你在重复,收尾」提示。
    - 重复 ≥ hard_limit(默认 5)→ 把这轮的 tool_calls 剥掉、finish_reason 改 stop,
      让 agent 自然产出最终答复(**不抛异常**)。

为什么 warn 在 wrap_model_call 注入,而不是 after_model(关键正确性,学 deer-flow):
  after_model 在模型吐 AIMessage(tool_calls)后立刻触发,这时工具还没跑、还没有对应的
  ToolMessage。在 after_model 里插 HumanMessage 会落到「assistant 的 tool_calls 和它的 tool 响应
  之间」,OpenAI/严格后端会因 tool_call_ids 没有紧跟响应而拒下一轮请求。所以 warn 先排队,等到
  下一次 wrap_model_call(那时上一轮的 ToolMessage 都已就位)再追加到消息列表末尾 —— 配对不破,
  也不篡改已有 AIMessage。

R3.2 最小版范围(对齐 deer-flow 的单层 hash;pull-by-need 推后的见末尾)
  - 只做「相同 tool_call 集合」的重复检测(deer-flow Layer 1)。
  - 按 thread_id 分桶(不按 run_id;consume_stop_reason 暂不实现 —— 研究子 agent 不需要)。
  - 频次层(deer-flow Layer 2:同一工具类型被调很多次但参数不同)、run_id 作用域、
    SubagentExecutor 联动的 stop_reason —— 都 pull-by-need(踩到再补,记 backlog)。
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from hyperion.platform.runtime._bounded_dict import BoundedDict

logger = logging.getLogger(__name__)

_DEFAULT_WARN_THRESHOLD = 3   # 重复 3 次开始警告
_DEFAULT_HARD_LIMIT = 5       # 重复 5 次硬停
_DEFAULT_WINDOW_SIZE = 20     # 滑动窗口:记最近 N 轮的 hash
_DEFAULT_MAX_THREADS = 100    # 最多跟踪多少个 thread(LRU 淘汰)

_WARNING_MSG = "[LOOP DETECTED] 你在用相同参数重复调用同样的工具。停止调用工具,基于已有结果产出最终答复;若无法完成,总结你目前的进展。"
_HARD_STOP_MSG = "[FORCED STOP] 重复的工具调用超过安全上限。基于已收集的结果产出最终答复。"


# ── 纯函数:把一组 tool_calls 归一化成稳定 hash(学 deer-flow,简化)─────────────

# 「显著字段」:这些字段决定「这是不是同一次调用」,其它字段(如临时 id)忽略以免误判不同。
_SALIENT_FIELDS = ("path", "url", "query", "command", "pattern", "glob", "cmd")


def _normalize_args(raw: object) -> tuple[dict, str | None]:
    """把 tool_call 的 args 规整成 dict + 一个兜底 key(有些 provider 给 JSON 字符串而非 dict)。"""
    if isinstance(raw, dict):
        return raw, None
    if isinstance(raw, str):
        try:
            p = json.loads(raw)
        except (TypeError, ValueError):
            return {}, raw
        return (p, None) if isinstance(p, dict) else ({}, json.dumps(p, sort_keys=True, default=str))
    if raw is None:
        return {}, None
    return {}, json.dumps(raw, sort_keys=True, default=str)


def _stable_key(name: str, args: dict, fallback: str | None) -> str:
    """从「显著字段」派生稳定 key,让同样含义的调用归到同一 hash(忽略无关噪音)。"""
    salient = {f: args[f] for f in _SALIENT_FIELDS if args.get(f) is not None}
    if salient:
        return json.dumps(salient, sort_keys=True, default=str)
    return fallback if fallback is not None else json.dumps(args, sort_keys=True, default=str)


def _hash_tool_calls(tool_calls: list[dict]) -> str:
    """对一组 tool_calls 算**顺序无关**的稳定 hash(同一组调用,任意顺序 → 同一 hash)。"""
    parts = sorted(
        f"{tc.get('name', '')}:{_stable_key(tc.get('name', ''), *_normalize_args(tc.get('args', {})))}"
        for tc in tool_calls
    )
    blob = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.md5(blob.encode()).hexdigest()[:12]


# ── 中间件 ──────────────────────────────────────────────────────────────────


class LoopDetectionMiddleware(AgentMiddleware[AgentState]):
    """检测重复 tool_call 循环:warn(排队下一轮塞提示)/ hard(剥 tool_calls 强制收尾)。

    R3.2 单层 hash 最小版。线程安全(长驻 lead agent 实例跨多 run 共享)。
    """

    def __init__(
        self,
        *,
        warn_threshold: int = _DEFAULT_WARN_THRESHOLD,
        hard_limit: int = _DEFAULT_HARD_LIMIT,
        window_size: int = _DEFAULT_WINDOW_SIZE,
        max_tracked_threads: int = _DEFAULT_MAX_THREADS,
    ) -> None:
        super().__init__()
        self.warn_threshold = warn_threshold
        self.hard_limit = hard_limit
        self.window_size = window_size
        self.max_tracked_threads = max_tracked_threads
        self._lock = threading.Lock()
        # 每 thread 的近期 hash 滑动窗口(BoundedDict 按 thread LRU 淘汰,防泄漏)
        self._history: BoundedDict[str, list[str]] = BoundedDict(max_tracked_threads)
        # 每 thread「已警告过」的 hash 集(同一 hash 只警告一次,避免每轮重复塞)
        self._warned: BoundedDict[str, set[str]] = BoundedDict(max_tracked_threads)
        # 待注入的警告:thread_id → [msg,...],在 wrap_model_call 排空(见模块 docstring 的正确性说明)
        self._pending: BoundedDict[str, list[str]] = BoundedDict(max_tracked_threads)

    @staticmethod
    def _thread_id(runtime: Runtime) -> str:
        tid = runtime.context.get("thread_id") if runtime.context else None
        return str(tid) if tid else "default"

    def reset(self) -> None:
        with self._lock:
            for d in (self._history, self._warned, self._pending):
                d.clear()

    def _track(self, tool_calls: list[dict], tid: str) -> tuple[str | None, bool]:
        """记一笔 hash + 判是否该警告/硬停。返 (warning_msg_or_none, should_hard_stop)。"""
        h = _hash_tool_calls(tool_calls)
        with self._lock:
            history = self._history.setdefault(tid, [])
            history.append(h)
            if len(history) > self.window_size:
                history[:] = history[-self.window_size:]  # 只留最近 window_size 笔
            count = history.count(h)
            if count >= self.hard_limit:
                logger.error("Loop hard limit: thread=%s hash=%s count=%d", tid, h, count)
                return _HARD_STOP_MSG, True
            if count >= self.warn_threshold:
                warned = self._warned.setdefault(tid, set())
                if h not in warned:
                    warned.add(h)
                    logger.warning("Loop detected: thread=%s hash=%s count=%d", tid, h, count)
                    return _WARNING_MSG, False
            return None, False

    def _apply(self, state: AgentState, runtime: Runtime) -> dict | None:
        """after_model 逻辑:取末条 AIMessage 的 tool_calls → 记 → 判警告/硬停。"""
        messages = state.get("messages", [])
        if not messages:
            return None
        last = messages[-1]
        if getattr(last, "type", None) != "ai":  # 只看模型回的 AIMessage
            return None
        tool_calls = getattr(last, "tool_calls", None) or []
        if not tool_calls:
            return None
        tid = self._thread_id(runtime)
        warning, hard = self._track(tool_calls, tid)

        if hard:
            # 剥 tool_calls + 清 additional_kwargs 里的原始 provider 元数据 + 追加硬停文本
            content = last.content if isinstance(last.content, str) else str(last.content)
            content = f"{content}\n\n{_HARD_STOP_MSG}" if content else _HARD_STOP_MSG
            extra = dict(getattr(last, "additional_kwargs", {}) or {})
            for k in ("tool_calls", "function_call"):
                extra.pop(k, None)
            meta = deepcopy(getattr(last, "response_metadata", {}) or {})
            if meta.get("finish_reason") == "tool_calls":
                meta["finish_reason"] = "stop"
            stripped = last.model_copy(update={
                "tool_calls": [],
                "content": content,
                "additional_kwargs": extra,
                "response_metadata": meta,
            })
            return {"messages": [stripped]}

        if warning:
            # 不能在 after_model 注入(破 tool_call 配对)→ 排队,等 wrap_model_call 再发
            self._pending.setdefault(tid, []).append(warning)
        return None

    # ── hooks ──

    @override
    def before_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        # 新 run 开始:清本 thread 残留的旧待发警告,不串到下一 run
        with self._lock:
            self._pending.pop(self._thread_id(runtime), None)
        return None

    @override
    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self.before_agent(state, runtime)

    @override
    def after_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self._apply(state, runtime)

    @override
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self._apply(state, runtime)

    @override
    def after_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        # 跑完:清待发警告(没排空的也不串到下一 run)
        with self._lock:
            self._pending.pop(self._thread_id(runtime), None)
        return None

    @override
    async def aafter_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self.after_agent(state, runtime)

    def _augment(self, request: ModelRequest) -> ModelRequest:
        """排空待发警告:追加到消息列表末尾(此时上一轮 ToolMessage 已就位,配对不破)。"""
        with self._lock:
            warnings = self._pending.pop(self._thread_id(request.runtime), [])
        if not warnings:
            return request
        msg = HumanMessage(content="\n\n".join(dict.fromkeys(warnings)), name="loop_warning")
        return request.override(messages=[*request.messages, msg])

    @override
    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelCallResult:
        return handler(self._augment(request))

    @override
    async def awrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]) -> ModelCallResult:
        return await handler(self._augment(request))
