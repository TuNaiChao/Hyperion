"""demo agent 构造测试(用假 key 绕过凭据校验,不真调 API)。"""

import warnings

from hyperion.platform.agent import build_demo_agent, build_middlewares


def test_build_demo_agent_constructs_without_deprecation(monkeypatch):
    """构造出 CompiledStateGraph,且无 create_react_agent 弃用警告。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")  # 绕过构造期凭据校验
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        agent = build_demo_agent()
    assert type(agent).__name__ == "CompiledStateGraph"
    bad = [
        str(w.message)
        for w in caught
        if "create_react_agent" in str(w.message)
        or ("create_agent" in str(w.message) and "moved" in str(w.message))
    ]
    assert bad == []


def test_middlewares_empty_at_p0():
    """P0 阶段中间件链为空(生产级护栏见 backlog)。"""
    assert build_middlewares() == []
