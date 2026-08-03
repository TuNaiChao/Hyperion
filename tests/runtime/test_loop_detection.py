"""LoopDetectionMiddleware 测试(R3.2)。

覆盖:
- _hash_tool_calls:顺序无关 + args 规一化(dict vs JSON 字符串)+ 不同调用不同 hash。
- _track:warn 首次触发、不重复警告、hard 硬停;不同调用不误触。
- _apply:非 AIMessage / 无 tool_calls 返 None;hard 剥 tool_calls + finish→stop。
- BoundedDict 真的淘汰(修了原内联版 __setitem__ 写在 __init__ 里的 bug)。
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from hyperion.platform.runtime._bounded_dict import BoundedDict
from hyperion.platform.runtime.middlewares.loop_detection import (
    LoopDetectionMiddleware,
    _hash_tool_calls,
)


class _RT:
    """假 runtime(给 middleware 读 thread_id 用)。"""

    def __init__(self, tid: str = "t1") -> None:
        self.context = {"thread_id": tid}


# ── 纯函数:_hash_tool_calls ───────────────────────────────────────────────


def test_hash_order_independent():
    """同一组 tool_calls,顺序不同 → 同一 hash。"""
    a = [{"name": "grep", "args": {"pattern": "x"}}]
    b = [{"name": "read", "args": {"path": "p"}}]
    assert _hash_tool_calls([a[0], b[0]]) == _hash_tool_calls([b[0], a[0]])


def test_hash_differs_by_salient_arg():
    """显著字段(pattern)不同 → 不同 hash。"""
    assert _hash_tool_calls([{"name": "grep", "args": {"pattern": "x"}}]) != _hash_tool_calls(
        [{"name": "grep", "args": {"pattern": "y"}}]
    )


def test_hash_normalizes_json_string_args():
    """有些 provider 把 args 给成 JSON 字符串而非 dict → 归一到同一 hash。"""
    dict_tc = [{"name": "grep", "args": {"pattern": "x"}}]
    str_tc = [{"name": "grep", "args": '{"pattern": "x"}'}]
    assert _hash_tool_calls(dict_tc) == _hash_tool_calls(str_tc)


# ── _track:warn / hard 决策 ───────────────────────────────────────────────


def test_track_warn_once_then_hard():
    """同调用重复:warn_threshold 首次警告(只一次),hard_limit 硬停。"""
    mw = LoopDetectionMiddleware(warn_threshold=3, hard_limit=5, window_size=20)
    tc = [{"name": "grep", "args": {"pattern": "x"}}]

    assert mw._track(tc, "t1") == (None, False)  # 第 1 次
    assert mw._track(tc, "t1") == (None, False)  # 第 2 次
    warn, hard = mw._track(tc, "t1")  # 第 3 次:首次警告
    assert warn is not None and not hard
    assert mw._track(tc, "t1") == (None, False)  # 第 4 次:已警告过,不重复
    warn, hard = mw._track(tc, "t1")  # 第 5 次:硬停
    assert hard and warn is not None


def test_track_different_calls_no_warn():
    """不同调用的序列不该被误判为循环。"""
    mw = LoopDetectionMiddleware(warn_threshold=3)
    for p in ("a", "b", "c", "d"):
        assert mw._track([{"name": "grep", "args": {"pattern": p}}], "t1") == (None, False)


def test_track_thread_isolation():
    """同调用在不同 thread 不应互相累计(thread_id 分桶)。"""
    mw = LoopDetectionMiddleware(warn_threshold=2)
    tc = [{"name": "grep", "args": {"pattern": "x"}}]
    mw._track(tc, "tA")
    mw._track(tc, "tB")
    # 各 thread 只 1 次,都不该警告
    assert mw._track(tc, "tA") != (None, False)  # tA 第 2 次 → 警告
    assert mw._track(tc, "tB") != (None, False)  # tB 第 2 次 → 警告


# ── _apply:消息提取 + hard 剥 tool_calls ──────────────────────────────────


def test_apply_ignores_non_ai_and_no_tool_calls():
    mw = LoopDetectionMiddleware()
    rt = _RT()
    assert mw._apply({"messages": []}, rt) is None
    assert mw._apply({"messages": [HumanMessage(content="hi")]}, rt) is None
    assert mw._apply({"messages": [AIMessage(content="hi", id="m1")]}, rt) is None  # 无 tool_calls


def test_apply_hard_strips_tool_calls():
    """hard 路径:剥 tool_calls + finish→stop + 追加硬停文本。"""
    mw = LoopDetectionMiddleware(warn_threshold=1, hard_limit=2)
    rt = _RT()
    msg = AIMessage(
        content="hi",
        id="m1",
        tool_calls=[{"name": "grep", "args": {"pattern": "x"}, "id": "tc1"}],
        response_metadata={"finish_reason": "tool_calls"},
    )
    assert mw._apply({"messages": [msg]}, rt) is None  # 第 1 次:warn 排队
    res = mw._apply({"messages": [msg]}, rt)  # 第 2 次:hard
    assert res is not None and "messages" in res
    stopped = res["messages"][0]
    assert stopped.tool_calls == []
    assert stopped.response_metadata.get("finish_reason") == "stop"
    assert "FORCED STOP" in stopped.content


# ── BoundedDict 淘汰(修了原 token_budget 内联版的 bug)────────────────────


def test_bounded_dict_evicts_oldest():
    """超 maxsize 淘汰最老(插入序)。原内联版 __setitem__ 误写在 __init__ 里从不生效,此处守住。"""
    d: BoundedDict[str, int] = BoundedDict(maxsize=3)
    d["a"], d["b"], d["c"] = 1, 2, 3
    assert len(d) == 3
    d["d"] = 4  # 满 3,淘汰 "a"
    assert "a" not in d
    assert list(d.keys()) == ["b", "c", "d"]
