"""pytest 共享 fixture。

autouse 地给所有测试预设一个假 OPENAI_API_KEY,绕过 langchain_openai 构造期的凭据校验
(我们不在测试里真调 API)。config 缓存首次加载时会读到这个值,后续用例复用缓存即一致。

另 autouse 把仓库注册表(ROOTRECALL_REPOS_FILE)锚到 tmp —— ensure_repo clone 后会自动
登记注册表,不隔离会写进真实 data/repos.yaml 并污染后续测试/本机状态。
"""

import pytest


@pytest.fixture(autouse=True)
def _fake_api_key(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ROOTRECALL_REPOS_FILE", str(tmp_path / "repos.yaml"))
