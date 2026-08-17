# src/rootrecall/platform/runtime/_bounded_dict.py
"""BoundedDict —— 达到 maxsize 后淘汰最老条目的 OrderedDict(插入序,LRU 式)。

用途:guard 中间件(TokenBudget / LoopDetection)按 thread_id / run_id 存「每 run 状态」,
长驻 lead agent 跨很多 run 不能无限涨 → 上限淘汰防 abandoned run 内存泄漏。

从 token_budget.py 的内联版抽出共享(R3.2 LoopDetection 也要用)。⚠️ 原内联版的
``__setitem__`` 误写在 ``__init__`` 函数体里(成了局部函数、淘汰从未生效);此处修正为
真正的类方法 —— BoundedDict 现在真的会淘汰了。
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, TypeVar

_K = TypeVar("_K")
_V = TypeVar("_V")


class BoundedDict(OrderedDict[_K, _V]):
    """达到 maxsize 后淘汰最老条目(按插入序,LRU 式)。

    不是真 LRU(读不触发 move_to_end):guard 场景「写多读少 + 只在 run 结束读一次」,
    插入序淘汰够用且更可预测。要真 LRU 重写 ``__getitem__`` 加 ``move_to_end`` 即可(暂不需要)。
    """

    def __init__(self, maxsize: int = 1000, *args: Any, **kwds: Any) -> None:
        self.maxsize = maxsize
        super().__init__(*args, **kwds)

    def __setitem__(self, key: _K, value: _V) -> None:  # noqa: D401 - 方法,非内联局部函数
        if key not in self:
            if len(self) >= self.maxsize:
                self.popitem(last=False)  # 淘汰最老(插入序)
        super().__setitem__(key, value)
