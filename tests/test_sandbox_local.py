"""LocalSandbox 功能:命令执行 / env 刮除 / 超时 kill / 读写 / 浏览搜索。"""

import pytest

from hyperion.platform.sandbox.local import LocalSandbox


@pytest.fixture
def sb(tmp_path):
    """每个用例一个独立工作区的 LocalSandbox。"""
    return LocalSandbox(workspace=tmp_path)


def test_echo_and_exit_code(sb):
    out = sb.execute_command("echo hello")
    assert "hello" in out
    assert "exit code: 0" in out


def test_env_scrub_via_bash(sb, monkeypatch):
    """子进程里拿不到宿主的密钥。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-LEAK")
    out = sb.execute_command("printenv OPENAI_API_KEY || echo NONE")
    assert "sk-LEAK" not in out
    assert "NONE" in out


def test_timeout_kills_process_group(sb):
    """超时必须杀进程组,不能让 sleep 挂满。"""
    out = sb.execute_command("sleep 10", timeout=1)
    assert "超时" in out


def test_read_write_and_line_range(sb, tmp_path):
    p = tmp_path / "a.txt"
    sb.write_file(str(p), "line1\nline2\nline3\n")
    assert "line1" in sb.read_file(str(p))
    rng = sb.read_file(str(p), start_line=2, end_line=3)
    assert "line2" in rng and "line3" in rng and "line1" not in rng


def test_ls(sb, tmp_path):
    (tmp_path / "x.txt").write_text("x", encoding="utf-8")
    assert any("x.txt" in line for line in sb.list_dir(str(tmp_path)))


def test_glob_and_grep(sb, tmp_path):
    (tmp_path / "b.c").write_text("int main(){}\n", encoding="utf-8")
    assert any("b.c" in x for x in sb.glob(str(tmp_path), "*.c"))
    assert any("main" in x for x in sb.grep(str(tmp_path), "main"))
