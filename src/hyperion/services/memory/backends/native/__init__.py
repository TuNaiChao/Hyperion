"""native 后端:v1 默认,组合 code_index(语义)+ code-review-graph(结构,可选)。

组件:store(SQLite)/ recall(多路)/ memorize(写)/ consolidate(巩固)/ structural(CRG 适配)。
MemoryService 子类 NativeMemoryService 在 service.py;这里注册 BACKEND_CLASS 供 manager 发现。
"""

from hyperion.services.memory.backends.native.service import NativeMemoryService

BACKEND_CLASS = NativeMemoryService

__all__ = ["NativeMemoryService", "BACKEND_CLASS"]
