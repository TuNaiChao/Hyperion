"""native 后端 · 巩固 / 持续学习(R1 backends/native/consolidate.py)。

后台/手动触发的巩固 pass(对应 memory-design.md §6,借 mnemopi + Letta):
  - 升级 mental_model:被召回≥N 次(access_count)的教训 → 升级为"稳定规则"(Letta 3+ 规则)。
  - 精确去重:防御性 —— 写时已按 content_key 合并,这里不重复(同 id 不会并存)。

记 backlog(刻意 v1 不做):
  - 语义近邻去重(同根因不同措辞 → 合并):需 embedding 聚类,R1 后。
  - Weibull/分层衰减物理降级:recall 已做 exp 衰减评分;物理降级不做(研究:eviction 仅合规)。
  - 自动失效(补丁合入上游 → invalidate):需接 git/CI,R3+。
"""

from __future__ import annotations

import logging

from hyperion.services.memory.backends.native.store import MemoryStore
from hyperion.services.memory.schema import Scope

logger = logging.getLogger(__name__)


def consolidate(scope: Scope, *, store: MemoryStore, promote_access_count: int = 3) -> dict[str, int]:
    """巩固 pass:扫 active 项,达标的升级 mental_model。返回统计 {scanned, promoted}。"""
    stats = {"scanned": 0, "promoted": 0}
    for it in store.list_items(scope):
        stats["scanned"] += 1
        if it.kind != "mental_model" and it.access_count >= promote_access_count:
            store.set_kind(it.id, "mental_model")
            stats["promoted"] += 1
    logger.info("memory.consolidate(%s): 扫 %d,升级 %d", scope.codebase, stats["scanned"], stats["promoted"])
    return stats
