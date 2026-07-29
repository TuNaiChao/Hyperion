"""工具注册表:从 config.yaml 加载、按组过滤。"""

from hyperion.platform.config import get_app_config
from hyperion.tools.registry import get_available_tools


def test_loads_all_declared_tools():
    """加载出来的 == config.yaml 声明的(加工具/改组不会让本测试失效)。"""
    cfg = get_app_config()
    declared = [tc.name for tc in cfg.tools]
    loaded = [t.name for t in get_available_tools()]
    assert loaded == declared
    assert "bash" in loaded and "memory_recall" in loaded


def test_group_filter():
    """每个 group 加载出来的 == config 声明的该组成员。"""
    cfg = get_app_config()
    for g in ["sandbox", "file:read", "code", "memory"]:
        declared = sorted(tc.name for tc in cfg.tools if tc.group == g)
        loaded = sorted(t.name for t in get_available_tools(groups={g}))
        assert loaded == declared, f"group {g}: {loaded} != {declared}"


def test_unknown_group_yields_empty():
    assert get_available_tools(groups={"nonexistent"}) == []
