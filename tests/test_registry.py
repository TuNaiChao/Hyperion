"""工具注册表:从 config.yaml 加载、按组过滤。"""

from hyperion.tools.registry import get_available_tools


def test_loads_all_five_tools():
    names = [t.name for t in get_available_tools()]
    assert names == ["bash", "read_file", "write_file", "str_replace", "ls"]


def test_group_filter():
    read_tools = sorted(t.name for t in get_available_tools(groups={"file:read"}))
    assert read_tools == ["ls", "read_file"]
    assert [t.name for t in get_available_tools(groups={"sandbox"})] == ["bash"]


def test_unknown_group_yields_empty():
    assert get_available_tools(groups={"nonexistent"}) == []
