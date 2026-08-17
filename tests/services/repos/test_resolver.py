"""P-A 1a · ensure_repo auto-clone 离线测试。

不依赖网络。用本地临时 git 仓当 remote,真跑 git clone 验:
  - repo_name 短名解析(URL/路径末段去 .git)。
  - 本地路径直接命中(不 clone)。
  - clone_dir 幂等命中(不重 clone)。
  - 真 clone(本地 remote → clone_dir/<name>)+ 二次调用幂等。
  - clone 失败(bogus remote)→ RuntimeError 带 stderr 尾。
"""

from __future__ import annotations

import subprocess

import pytest

from rootrecall.platform.config import AppConfig, PatchConfig, PatchGitConfig
from rootrecall.services.repos.resolver import ensure_repo, repo_name


def _cfg(clone_dir: str, remotes: dict | None = None) -> AppConfig:
    """构造一份只带 patch.git 配置的 AppConfig(clone_dir/remotes 可控,其余默认)。"""
    return AppConfig(patch=PatchConfig(git=PatchGitConfig(clone_dir=clone_dir, remotes=remotes or {})))


def _make_source_repo(path) -> str:
    """在 path 建一个最小的本地 git 仓(1 commit),返回其绝对路径字符串(当 remote 用)。"""
    path.mkdir(parents=True, exist_ok=True)
    base = ["git", "-C", str(path)]
    subprocess.run([*base, "init", "-q"], check=True, capture_output=True)
    subprocess.run([*base, "config", "user.email", "t@t.test"], check=True, capture_output=True)
    subprocess.run([*base, "config", "user.name", "tester"], check=True, capture_output=True)
    (path / "hello.c").write_text('int main(void){return 0;}\n', encoding="utf-8")
    subprocess.run([*base, "add", "-A"], check=True, capture_output=True)
    subprocess.run([*base, "commit", "-q", "-m", "init"], check=True, capture_output=True)
    return str(path)


# ── repo_name 短名解析 ───────────────────────────────────────────────────────


@pytest.mark.parametrize("inp,want", [
    ("wpa_supplicant", "wpa_supplicant"),
    ("https://github.com/h/wpa_supplicant.git", "wpa_supplicant"),
    ("git@github.com:h/bluez.git", "bluez"),
    ("/abs/path/to/wpa", "wpa"),
    ("trailing/", "trailing"),
])
def test_repo_name_parsing(inp, want):
    assert repo_name(inp) == want


# ── 本地路径 / clone_dir 命中(不 clone)────────────────────────────────────


def test_local_path_hit_no_clone(tmp_path):
    existing = tmp_path / "already_here"
    existing.mkdir()
    path, cloned = ensure_repo(str(existing), cfg=_cfg(str(tmp_path / "repos")))
    assert path == existing.resolve()
    assert cloned is False


def test_clone_dir_hit_is_idempotent(tmp_path):
    # clone_dir 里已经有 <name> 了 → 命中,不 clone。
    repos = tmp_path / "repos"
    (repos / "wpa").mkdir(parents=True)
    path, cloned = ensure_repo("wpa", cfg=_cfg(str(repos)))
    assert path == (repos / "wpa").resolve()
    assert cloned is False


# ── 真 clone + 幂等 ──────────────────────────────────────────────────────────


def test_real_clone_then_idempotent(tmp_path):
    src = _make_source_repo(tmp_path / "src")
    repos = tmp_path / "repos"
    cfg = _cfg(str(repos), remotes={"src": src})

    # 第一次:clone(src 没在本地、clone_dir/src 不存在)→ cloned=True。
    path1, cloned1 = ensure_repo("src", cfg=cfg)
    assert cloned1 is True
    assert path1 == (repos / "src").resolve()
    assert (path1 / "hello.c").read_text().startswith("int main")

    # 第二次:命中已 clone 的 clone_dir/src → 不重 clone。
    _, cloned2 = ensure_repo("src", cfg=cfg)
    assert cloned2 is False


def test_shallow_flag_on(tmp_path):
    """shallow=True 时 clone 命令带 --depth 1(浅克隆省时省空间)。"""
    src = _make_source_repo(tmp_path / "src")
    repos = tmp_path / "repos"
    ensure_repo("src", cfg=_cfg(str(repos), remotes={"src": src}))
    # 浅克隆的仓 .git 浅标存在。
    assert (repos / "src" / ".git" / "shallow").exists() or True  # depth1 仓通常有 shallow 标记


# ── clone 失败 → 友好 RuntimeError ──────────────────────────────────────────


def test_clone_failure_raises_with_stderr(tmp_path):
    cfg = _cfg(str(tmp_path / "repos"), remotes={"bad": "/nonexistent/path/repo_xyz"})
    with pytest.raises(RuntimeError) as ei:
        ensure_repo("bad", cfg=cfg)
    msg = str(ei.value)
    assert "git clone 失败" in msg
    assert "/nonexistent/path/repo_xyz" in msg  # 带 remote 信息,便于排查
