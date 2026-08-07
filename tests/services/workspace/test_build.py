"""P-A 1a · build_check 离线测试(temp git 仓 + Makefile,真跑 make,不依赖编译器)。

覆盖:
  - 好补丁 + 能 build → builds=yes。
  - 补丁能 apply 但 build 失败(Makefile 跑 false)→ builds=no + 归因。
  - 补丁打不上(bad context)→ builds=no + "先 validate_patch" 提示。
  - 无构建系统 → unchecked;config.build_cmd 覆盖 → 跑指定命令。
  - 超时 → unchecked + 杀进程组。
  - 非目录 / 空补丁 → unchecked。
  - 辅助函数:_changed_files / _detect_build_cmd / _attribute。
"""

from __future__ import annotations

import subprocess

from hyperion.platform.config import AppConfig, PatchBuildConfig, PatchConfig
from hyperion.services.workspace.build import _attribute, _changed_files, _detect_build_cmd, build_check


def _cfg(*, timeout=600.0, commands=None) -> AppConfig:
    return AppConfig(patch=PatchConfig(build=PatchBuildConfig(timeout=timeout, commands=commands or {})))


def _make_repo(tmp_path, *, makefile=True, good=True):
    """建一个 temp git 仓(含 1 commit)。makefile=True 加 Makefile(good=True→make 成功,False→失败)。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    base = ["git", "-C", str(repo)]
    subprocess.run([*base, "init", "-q"], check=True, capture_output=True)
    subprocess.run([*base, "config", "user.email", "t@t.test"], check=True, capture_output=True)
    subprocess.run([*base, "config", "user.name", "t"], check=True, capture_output=True)
    (repo / "main.c").write_text("int main(void){return 0;}\n", encoding="utf-8")
    if makefile:
        # 不依赖编译器:good 用 echo(成功),bad 用 false(失败)。tab 缩进是 make 硬要求。
        body = "all:\n\t@echo build-ok\n" if good else "all:\n\t@false\n"
        (repo / "Makefile").write_text(body, encoding="utf-8")
    subprocess.run([*base, "add", "-A"], check=True, capture_output=True)
    subprocess.run([*base, "commit", "-q", "-m", "init"], check=True, capture_output=True)
    return repo


# 能干净 apply 的补丁:给 main.c 加一行(不带 index 行,git apply 接受)。
_PATCH_OK = (
    "diff --git a/main.c b/main.c\n"
    "--- a/main.c\n"
    "+++ b/main.c\n"
    "@@ -1 +1,2 @@\n"
    " int main(void){return 0;}\n"
    "+// added\n"
)
# 打不上的补丁:context 根本不匹配。
_PATCH_BAD = (
    "diff --git a/main.c b/main.c\n"
    "--- a/main.c\n"
    "+++ b/main.c\n"
    "@@ -1 +1,2 @@\n"
    " THIS LINE DOES NOT EXIST\n"
    "+// added\n"
)


# ── 主路径 ───────────────────────────────────────────────────────────────────


def test_good_patch_builds_yes(tmp_path):
    repo = _make_repo(tmp_path, good=True)
    r = build_check(_PATCH_OK, repo, cfg=_cfg())
    assert r["builds"] == "yes"
    assert r["command"] == "make"
    assert "编译过" in r["attribution"]


def test_patch_applies_but_build_fails(tmp_path):
    """补丁能 apply,但 Makefile 目标失败 → builds=no + 归因(main.c 在补丁改动里)。"""
    repo = _make_repo(tmp_path, good=False)
    r = build_check(_PATCH_OK, repo, cfg=_cfg())
    assert r["builds"] == "no"
    assert "main.c" in r["attribution"]  # 报错/改动文件归因里提到 main.c
    assert "build 过 ≠ 包对" in r["hint"]


def test_patch_does_not_apply(tmp_path):
    """补丁打不上 → builds=no + 提示先 validate_patch。"""
    repo = _make_repo(tmp_path, good=True)
    r = build_check(_PATCH_BAD, repo, cfg=_cfg())
    assert r["builds"] == "no"
    assert r["command"] is None
    assert "validate_patch" in r["hint"]


def test_no_build_system_unchecked(tmp_path):
    repo = _make_repo(tmp_path, makefile=False)
    r = build_check(_PATCH_OK, repo, cfg=_cfg())
    assert r["builds"] == "unchecked"
    assert "认不出构建系统" in r["hint"]


def test_build_cmd_override(tmp_path):
    """没 Makefile 但传 build_cmd → 跑指定命令(echo → 成功 → yes)。"""
    repo = _make_repo(tmp_path, makefile=False)
    r = build_check(_PATCH_OK, repo, build_cmd="echo ok", cfg=_cfg())
    assert r["builds"] == "yes"
    assert r["command"] == "echo ok"


def test_config_commands_override(tmp_path):
    """config.patch.build.commands[<repo名>] 指定 → 用它(repo 名 = 目录名 'repo')。"""
    repo = _make_repo(tmp_path, makefile=False)
    r = build_check(_PATCH_OK, repo, cfg=_cfg(commands={"repo": "echo from-config"}))
    assert r["builds"] == "yes"
    assert r["command"] == "echo from-config"


def test_timeout_killed(tmp_path):
    """build_cmd 跑 sleep,timeout 极小 → 超时 → unchecked + 杀进程组提示。"""
    repo = _make_repo(tmp_path, makefile=False)
    r = build_check(_PATCH_OK, repo, build_cmd="sleep 5", cfg=_cfg(timeout=0.5))
    assert r["builds"] == "unchecked"
    assert "杀整组" in r["errors"]


def test_bad_inputs_unchecked(tmp_path):
    assert build_check(_PATCH_OK, str(tmp_path / "nope"), cfg=_cfg())["builds"] == "unchecked"
    assert build_check("", tmp_path, cfg=_cfg())["builds"] == "unchecked"


def test_worktree_cleaned_up(tmp_path):
    """build_check 跑完,主仓的 worktree 列表里不该残留(git worktree list 无 hyperion_build 残骸)。"""
    repo = _make_repo(tmp_path, good=True)
    build_check(_PATCH_OK, repo, cfg=_cfg())
    wl = subprocess.run(["git", "-C", str(repo), "worktree", "list"], capture_output=True, text=True)
    assert "hyperion_build" not in wl.stdout  # 临时 worktree 已清


# ── 辅助函数 ─────────────────────────────────────────────────────────────────


def test_changed_files():
    patch = "diff --git a/a.c b/a.c\n+++ b/a.c\n@@\n+x\n" "diff --git a/b.c b/b.c\n+++ /dev/null\n@@\n-x\n"
    assert _changed_files(patch) == {"a.c"}


def test_detect_build_cmd(tmp_path):
    d = tmp_path
    assert _detect_build_cmd(d) is None
    (d / "Makefile").write_text("x:")
    assert _detect_build_cmd(d) == "make"
    (d / "Makefile").unlink()
    (d / "meson.build").write_text("x")
    assert "meson" in _detect_build_cmd(d)


def test_attribute_inside_vs_outside():
    changed = {"main.c"}
    inside = _attribute("main.c:10: error: bad", changed)
    assert "报错落在补丁改动文件内" in inside
    outside = _attribute("other.c:5: error: missing dep", changed)
    assert "未改动的文件" in outside and "环境/前置" in outside
    assert _attribute("xxx", set()) == "补丁改动文件未解析出,无法归因。"


# ── 归一化容错(e2e 实证:agent 传补丁丢末尾换行 / 带 CRLF)──────────────────────


def test_build_normalizes_stripped_trailing_newline(tmp_path):
    """agent rstrip 掉补丁末尾换行 → build_check 归一化补回,仍能 apply 进到 build(不报'补丁损坏')。"""
    repo = _make_repo(tmp_path, good=True)
    r = build_check(_PATCH_OK.rstrip(), repo, cfg=_cfg())
    assert r["builds"] == "yes"  # 归一化后 apply 过 + make 过
    assert r["command"] == "make"


def test_validate_normalizes_crlf_and_missing_newline(tmp_path):
    """CRLF + 丢末尾换行 → validate_patch 归一化后仍 apply(对应 e2e 的'第 71 行损坏')。"""
    from hyperion.services.workspace.validate import validate_patch
    repo = _make_repo(tmp_path, good=True)
    mangled = _PATCH_OK.replace("\n", "\r\n").rstrip()  # CRLF + 去尾换行
    r = validate_patch(mangled, forward_dir=repo)
    assert r["verified"] is True
