"""记忆核心服务(R1,★P3 差异化)。

MemoryService 契约 + 可换后端(v1 native = 组合 code_index 语义 + code-review-graph 结构)。
详细设计:docs/设计/memory-design.md。
"""

from rootrecall.services.memory.manager import (
    MemoryService,
    discover_backends,
    get_memory_service,
    reset_memory_service,
    resolve_backend_class,
)
from rootrecall.services.memory.schema import (
    TIER_WEIGHT,
    Evidence,
    KnowledgeItem,
    RecallHit,
    Scope,
    SourceTier,
    make_id,
)

__all__ = [
    # 契约 + 工厂
    "MemoryService",
    "get_memory_service",
    "reset_memory_service",
    "resolve_backend_class",
    "discover_backends",
    # schema
    "KnowledgeItem",
    "RecallHit",
    "Scope",
    "Evidence",
    "SourceTier",
    "TIER_WEIGHT",
    "make_id",
]
