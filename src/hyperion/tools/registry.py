"""工具注册表:从 config.yaml 声明式加载工具(反射)。

遍历 AppConfig.tools,用 resolve_variable 按 'module:variable' 动态导入每个工具
(BaseTool 实例),可选按 group 过滤、按 name 去重。加新工具通常零代码——只改 config.yaml。

对应 deer-flow 的 get_available_tools(deer-flow/backend/.../tools/tools.py)。
"""

from __future__ import annotations

from langchain_core.tools import BaseTool

from hyperion.platform.config import AppConfig, get_app_config
from hyperion.platform.reflection import resolve_variable


def get_available_tools(
    groups: set[str] | None = None,
    *,
    config: AppConfig | None = None,
) -> list[BaseTool]:
    """按 config.yaml 声明加载工具。

    Args:
        groups: 只保留这些 group 的工具(如 {"sandbox", "file:read"});None 表示全要。
        config: 已解析配置(测试时可传入);默认 get_app_config()。
    """
    cfg = config or get_app_config()
    tools: list[BaseTool] = []
    seen: set[str] = set()
    for tc in cfg.tools:
        if groups is not None and tc.group not in groups:
            continue  # 不在请求的 group 里,跳过
        if tc.name in seen:
            continue  # 同名去重(以配置名为准)
        loaded = resolve_variable(tc.use)  # 反射:hyperion.tools.sandbox:bash_tool -> 对象
        if not isinstance(loaded, BaseTool):
            raise TypeError(
                f"{tc.use} 加载出来不是 BaseTool(实际 {type(loaded).__name__}),请检查 use 路径。"
            )
        tools.append(loaded)
        seen.add(tc.name)
    return tools