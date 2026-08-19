"""F3 · bare 镜像 + worktree 生命周期端到端(全 tmp,零网络 —— 本地 file:// remote)。

模拟用户完整旅程:
  ① 建本地"上游"仓(打 tag 5.50.61)→ 注册 baseline → `repo checkout` 开 bug 检出;
  ② 检出 = worktree(共享对象库:.git 是文件不是目录;内容 = ref 状态),已登记 ephemeral;
  ③ 给检出造假索引 → 把记录 created_at 改老 → `repo gc --dry-run` 列级联计划;
  ④ `repo gc` 真删:worktree + 向量索引 + 结构图 + 记录全清,**镜像和 baseline 完好**;
  ⑤ `rootrecall gc` 不碰 baseline/unmanaged;点名强删忽略年龄。
另:workspace manager 的 worktree 改造(git 仓 → worktree + 脏态覆写;非 git → copytree 回落)。
"""

from __future__ import annotations

import contextlib
import io
import json
import subprocess

import pytest

import rootrecall.services.repos.mirror as mirror_mod
import rootrecall.services.repos.registry as reg_mod
from rootrecall.cli import main as cli_main
from rootrecall.services.repos.mirror import add_worktree, ensure_mirror, remove_worktree
from rootrecall.services.workspace.manager import create_workspace


def _git(path, *argv, check=True) -> str:
    r = subprocess.run(["git", "-C", str(path), *argv], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise AssertionError(f"git {argv} 失败: {r.stderr}")
    return r.stdout


@pytest.fixture()
def upstream(tmp_path):
    """本地'上游'仓:2 commit,第二 commit 打 tag 5.50.61(当基线 remote 用)。"""
    up = tmp_path / "upstream"
    up.mkdir()
    _git(up, "init", "-q", "-b", "main")
    _git(up, "config", "user.email", "t@t.test")
    _git(up, "config", "user.name", "tester")
    (up / "core.c").write_text("int base(void){return 0;}\n", encoding="utf-8")
    _git(up, "add", "-A")
    _git(up, "commit", "-qm", "init")
    (up / "fix.c").write_text("int fix(void){return 1;}\n", encoding="utf-8")
    _git(up, "add", "-A")
    _git(up, "commit", "-qm", "add fix")
    _git(up, "tag", "5.50.61")
    return up


@pytest.fixture()
def isolation(tmp_path, monkeypatch):
    """把注册表/镜像/索引根全锚到 tmp(绝不碰真 data/)。"""
    monkeypatch.setenv("ROOTRECALL_REPOS_FILE", str(tmp_path / "repos.yaml"))
    monkeypatch.setattr(mirror_mod, "mirrors_root", lambda: tmp_path / "mirrors")
    monkeypatch.setattr(mirror_mod, "worktrees_root", lambda: tmp_path / "worktrees")
    monkeypatch.setattr(reg_mod, "_install_root", lambda: tmp_path)
    return tmp_path


def test_f3_checkout_and_gc_e2e(upstream, isolation):
    tmp = isolation
    mirrors, worktrees = tmp / "mirrors", tmp / "worktrees"

    # ── ① 注册 baseline + 开 bug 检出 ────────────────────────────────────────
    assert cli_main(["repo", "register", "e2e-v20", "--url", str(upstream),
                     "--role", "baseline", "--branch", "main"]) == 0
    assert cli_main(["repo", "checkout", "e2e-bug17", "--from", "e2e-v20",
                     "--ref", "5.50.61", "--bug", "BUG-17"]) == 0

    wt = worktrees / "e2e-bug17"
    assert (wt / "fix.c").exists() and (wt / ".git").is_file()  # worktree:.git 是文件
    assert (mirrors / "e2e-v20.git").is_dir()                   # bare 镜像就位
    rec = reg_mod.RepoRegistry().get("e2e-bug17")
    assert rec.role == "ephemeral" and rec.bug_id == "BUG-17" and rec.from_repo == "e2e-v20"
    assert reg_mod.RepoRegistry().get("e2e-v20").mirror == str(mirrors / "e2e-v20.git")

    # 幂等:同 ref 再 checkout 同名 → 复用已有 worktree,不炸。
    assert cli_main(["repo", "checkout", "e2e-bug17", "--from", "e2e-v20",
                     "--ref", "5.50.61", "--bug", "BUG-17"]) == 0

    # ── ② 给检出造假索引(模拟已建索引,gc 应级联清)─────────────────────────
    idx_dir = tmp / "data" / "code_index" / "e2e-bug17"
    idx_dir.mkdir(parents=True)
    (idx_dir / "index_manifest.json").write_text(json.dumps({"repo_path": str(wt)}), encoding="utf-8")
    sg_dir = tmp / "data" / "structgraph" / "e2e-bug17"
    sg_dir.mkdir(parents=True)
    (sg_dir / "graph.db").write_text("x", encoding="utf-8")

    # ── ③ 未到期:gc 保留 ────────────────────────────────────────────────────
    assert cli_main(["repo", "gc", "--max-age-days", "14"]) == 0
    assert wt.exists() and reg_mod.RepoRegistry().get("e2e-bug17") is not None

    # 改老 created_at → dry-run 列级联计划(不动手)
    reg_mod.RepoRegistry().register("e2e-bug17", created_at="2026-01-01")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert cli_main(["repo", "gc", "--dry-run"]) == 0
    assert "dry-run" in buf.getvalue() and "e2e-bug17" in buf.getvalue()
    assert wt.exists(), "dry-run 不得动手"

    # ── ④ 真删:级联 worktree + 两类索引 + 记录;镜像/baseline 完好 ──────────
    with contextlib.redirect_stdout(io.StringIO()):
        assert cli_main(["repo", "gc"]) == 0
    assert not wt.exists()
    assert not idx_dir.exists() and not sg_dir.exists()
    assert reg_mod.RepoRegistry().get("e2e-bug17") is None
    assert (mirrors / "e2e-v20.git").is_dir(), "bare 镜像是共享资产,gc 不删"
    assert reg_mod.RepoRegistry().get("e2e-v20") is not None, "baseline 记录不删"
    # 镜像仍能再开新检出(簿记干净,gc 后体系继续可用)
    wt2, new = add_worktree(mirrors / "e2e-v20.git", "5.50.61", worktrees / "e2e-bug18")
    assert new and (wt2 / "fix.c").exists()

    # ── ⑤ 点名强删(忽略年龄);baseline 点名也不删 ──────────────────────────
    wt2, new = add_worktree(mirrors / "e2e-v20.git", "5.50.61", worktrees / "e2e-bug18")
    reg_mod.RepoRegistry().register("e2e-bug18", path=str(wt2), role="ephemeral",
                                    mirror=str(mirrors / "e2e-v20.git"))  # 新记录必须显式 ephemeral
    with contextlib.redirect_stdout(io.StringIO()):
        rc = cli_main(["repo", "gc", "--name", "e2e-bug18"])
        rc_base = cli_main(["repo", "gc", "--name", "e2e-v20"])
    assert rc == 0 and not (worktrees / "e2e-bug18").exists()
    assert rc_base != 0 or reg_mod.RepoRegistry().get("e2e-v20") is not None


def test_f3_orphan_index_report_and_prune(upstream, isolation):
    """孤儿索引(没注册 + manifest 源路径失效):gc 只报告;--prune-orphans 才删。
    老索引(manifest 无 repo_path)单列 legacy —— 绝不能被 prune 误删。"""
    tmp = isolation
    orphan = tmp / "data" / "code_index" / "ghost"
    orphan.mkdir(parents=True)
    (orphan / "index_manifest.json").write_text(
        json.dumps({"repo_path": "/nonexistent/gone"}), encoding="utf-8")
    legacy = tmp / "data" / "code_index" / "old-style"
    legacy.mkdir()
    (legacy / "index_manifest.json").write_text(json.dumps({"repo_commit": "abc"}), encoding="utf-8")

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert cli_main(["repo", "gc"]) == 0
    assert "ghost" in buf.getvalue() and orphan.exists(), "默认只报告不动手"
    assert "old-style" in buf.getvalue() and "未登记" in buf.getvalue(), "老索引单列 legacy 提示"

    with contextlib.redirect_stdout(io.StringIO()):
        assert cli_main(["repo", "gc", "--prune-orphans"]) == 0
    assert not orphan.exists()
    assert legacy.exists(), "legacy(无 repo_path)不受 --prune-orphans 影响"


# ── mirror 原语单测 ───────────────────────────────────────────────────────────


def test_mirror_primitives(upstream, tmp_path):
    m, new = ensure_mirror("t", str(upstream), root=tmp_path)
    assert new and m.is_dir()
    assert ensure_mirror("t", str(upstream), root=tmp_path)[1] is False  # 幂等

    wt, new = add_worktree(m, "5.50.61", tmp_path / "wt1")
    assert new and (wt / "fix.c").exists()
    assert add_worktree(m, "5.50.61", tmp_path / "wt1")[1] is False      # 幂等

    assert remove_worktree(tmp_path / "wt1", mirror=m) is True
    assert not (tmp_path / "wt1").exists()
    assert not _git(m, "worktree", "list").count("wt1")                   # 簿记已清
    assert remove_worktree(tmp_path / "wt1", mirror=m) is False          # 再删 no-op


# ── workspace manager:worktree 改造 ──────────────────────────────────────────


def test_workspace_uses_worktree_with_dirty_state(upstream, tmp_path, monkeypatch):
    """git 源仓 → code/ 是 worktree(共享对象库);未提交改动必须带过来(语义对齐 copytree)。"""
    ws_root = tmp_path / "ws"
    monkeypatch.setattr("rootrecall.services.workspace.manager.WORKSPACE_ROOT", ws_root)
    (upstream / "dirty.c").write_text("int dirty(void){return 9;}\n", encoding="utf-8")  # 未跟踪
    (upstream / "core.c").write_text("int base(void){return 2;}\n", encoding="utf-8")    # 已改未提交

    ws = create_workspace(upstream, "trigger text", bug_id="B1")
    code = ws / "code"
    assert (code / ".git").is_file()                       # linked worktree 标志
    assert (code / "dirty.c").exists()                     # 未跟踪文件带过来了
    assert "return 2" in (code / "core.c").read_text()     # 未提交修改带过来了
    # base commit 已锁:diff 能观察到 agent 后续改动
    _git(code, "rev-parse", "HEAD")
    # 源仓未被污染:工作树脏态原样(return 2),HEAD 提交也没被 workspace 的 base commit 移动
    assert "return 2" in (upstream / "core.c").read_text()
    assert "return 0" in _git(upstream, "show", "HEAD:core.c")


def test_workspace_falls_back_to_copytree_for_non_git(tmp_path, monkeypatch):
    ws_root = tmp_path / "ws"
    monkeypatch.setattr("rootrecall.services.workspace.manager.WORKSPACE_ROOT", ws_root)
    src = tmp_path / "plain"
    src.mkdir()
    (src / "a.c").write_text("int a;\n", encoding="utf-8")

    ws = create_workspace(src, "t", bug_id="B2")
    code = ws / "code"
    assert (code / ".git").is_dir()                        # 回落路径:copytree + git init
    assert (code / "a.c").exists()
