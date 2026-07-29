"""memory 后端:drop-in 文件夹约定(借 deer-flow backends/__init__.py)。

加新后端 = 在本目录建 <name>/ 文件夹,里面 __init__.py 暴露:
    BACKEND_CLASS = YourMemoryServiceSubclass   # 必须是 MemoryService 子类
然后 config.yaml 的 memory.backend: <name>。也支持 'pkg.mod:Cls' 点路径。

约定:文件夹名 == 后端名 == config 的 backend 值(见 manager.discover_backends)。
v1 内置:native(组合 code_index + code-review-graph)。可选:mem0 / cognee(extra 装了才可用)。
"""
