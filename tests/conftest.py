"""pytest 共享 fixture。

autouse 地给所有测试预设一个假 OPENAI_API_KEY,绕过 langchain_openai 构造期的凭据校验
(我们不在测试里真调 API)。config 缓存首次加载时会读到这个值,后续用例复用缓存即一致。
"""

import pytest


@pytest.fixture(autouse=True)
def _fake_api_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
