"""Reflection: dynamic loading of `module:Class` / `module:attr` strings.

This is the mechanism that makes the model factory provider-agnostic: a config
entry `use: langchain_openai:ChatOpenAI` is resolved here into the real class.
Mirrors deer-flow's `deerflow.reflection`.
"""

import importlib

_PROVIDER_INSTALL_HINTS = {
    "langchain_openai": "uv add langchain-openai",
    "langchain_anthropic": "uv add langchain-anthropic",
    "langchain_ollama": "uv add langchain-ollama",
    "langchain_google_genai": "uv add langchain-google-genai",
}


def resolve_class(dotted_path: str, base_class: type | None = None) -> type:
    """`'langchain_openai:ChatOpenAI'` → the class.

    Args:
        dotted_path: `'module.path:ClassName'`.
        base_class: if given, validate the resolved class is a subclass of it.
    """
    module_path, _, attr = dotted_path.partition(":")
    if not attr:
        raise ValueError(
            f"`use:` 路径必须是 'module:Class' 格式,得到 {dotted_path!r}"
        )
    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        top = module_path.split(".")[0]
        hint = _PROVIDER_INSTALL_HINTS.get(top, f"uv add {module_path}")
        raise ImportError(f"无法加载 {module_path}: {e}. 先安装: `{hint}`") from None
    cls = getattr(module, attr, None)
    if cls is None:
        raise AttributeError(f"{module_path} 中没有属性 {attr}")
    if base_class is not None and not (isinstance(cls, type) and issubclass(cls, base_class)):
        raise TypeError(f"{dotted_path} 不是 {base_class.__name__} 的子类")
    return cls


def resolve_variable(dotted_path: str):
    """`'module.path:func'` → the object (for tool loading)."""
    module_path, _, attr = dotted_path.partition(":")
    if not attr:
        raise ValueError(f"路径必须是 'module:attr' 格式,得到 {dotted_path!r}")
    module = importlib.import_module(module_path)
    return getattr(module, attr)
