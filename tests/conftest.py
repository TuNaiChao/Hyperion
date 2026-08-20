"""pytest 共享 fixture。

autouse 地给所有测试预设一个假 OPENAI_API_KEY,绕过 langchain_openai 构造期的凭据校验
(我们不在测试里真调 API)。config 缓存首次加载时会读到这个值,后续用例复用缓存即一致。

另 autouse 把仓库注册表(ROOTRECALL_REPOS_FILE)锚到 tmp —— ensure_repo clone 后会自动
登记注册表,不隔离会写进真实 data/repos.yaml 并污染后续测试/本机状态。

同时中和 ROOTRECALL_HOME:load_dotenv 会把真机 .env 灌进 pytest 进程环境,迁过家的机器上
这个值会让 data_root()/reanchor 指向真实 ~/.local/share/rootrecall —— 测试数据写进真实家
(踩坑#24 新变体:生产 .env 反向漏进测试)。显式 delenv 保证默认未设;要测迁家行为的用例
(如 test_p1_rootrecall_home_e2e)在自己的 fixture 里 setenv 覆盖,晚于 autouse 生效。
"""

import pytest


@pytest.fixture(autouse=True)
def _fake_api_key(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ROOTRECALL_REPOS_FILE", str(tmp_path / "repos.yaml"))
    # 设**空串**而非 delenv:delenv 后 load_dotenv 会在 config 首载时把真机 .env 的值
    # 重新灌进来(删掉的键不算"已存在");空串键已存在 → dotenv 不覆盖,而 data_root/
    # reanchor 都把空串当未设(回落安装根)。两头的语义刚好接上。
    monkeypatch.setenv("ROOTRECALL_HOME", "")
