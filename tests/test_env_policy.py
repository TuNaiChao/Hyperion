"""env_policy:验证 secret-looking 环境变量被刮除、普通变量保留。"""

from hyperion.platform.sandbox.env_policy import build_sandbox_env, is_blocked_env_name


def test_secret_patterns_blocked():
    """各类密钥命名都应被识别。"""
    assert is_blocked_env_name("OPENAI_API_KEY")
    assert is_blocked_env_name("ANTHROPIC_API_KEY")
    assert is_blocked_env_name("DEEPSEEK_API_KEY")
    assert is_blocked_env_name("TAVILY_API_KEY")
    assert is_blocked_env_name("DATABASE_URL")
    assert is_blocked_env_name("MY_PASSWORD")
    assert is_blocked_env_name("SOME_TOKEN")


def test_benign_vars_kept():
    """基础设施变量不能误伤。"""
    assert not is_blocked_env_name("PATH")
    assert not is_blocked_env_name("HOME")
    assert not is_blocked_env_name("LANG")


def test_build_scrubs_and_keeps(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-LEAK")
    monkeypatch.setenv("DATABASE_URL", "postgres://x")
    monkeypatch.setenv("MY_HARMLESS", "keep")
    env = build_sandbox_env()
    assert "OPENAI_API_KEY" not in env
    assert "DATABASE_URL" not in env
    assert env["MY_HARMLESS"] == "keep"


def test_injected_overrides_scrubbed(monkeypatch):
    """显式注入的值优先于被刮除的宿主值。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-LEAK")
    env = build_sandbox_env(injected={"OPENAI_API_KEY": "sk-allowed"})
    assert env["OPENAI_API_KEY"] == "sk-allowed"
