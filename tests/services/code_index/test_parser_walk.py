"""iter_source_files 尊重 .gitignore 测试(2026-08-18 真事故回归)。

事故:工作区里 clone 进来的参考仓(deer-flow/oh-my-pi,gitignore 掉但目录名不隐藏)
被纯 rglob 全量扫进索引 —— 几千个无关 .py 爆嵌入账单 + 污染检索。修法:git 仓走
`git ls-files` ∪ `git ls-files --others --exclude-standard`,非 git 仓兜底 rglob。
"""

from __future__ import annotations

import subprocess

from rootrecall.services.code_index.parser import iter_source_files


def _git(cwd, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, timeout=30)


def test_gitignore_respected(tmp_path):
    """git 仓:已跟踪 + 未跟踪未忽略 进清单;.gitignore 掉的参考仓一个文件都不进。"""
    # 造仓:已跟踪 src/a.py;未跟踪 src/new.py;被忽略的 refrepo/(模拟 deer-flow)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "src" / "new.py").write_text("y = 2\n", encoding="utf-8")
    (tmp_path / "refrepo").mkdir()
    (tmp_path / "refrepo" / "big.py").write_text("z = 3\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("refrepo/\n", encoding="utf-8")

    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "add", "src/a.py", ".gitignore")
    _git(tmp_path, "commit", "-q", "-m", "init")

    rels = [rel for _, rel, _ in iter_source_files(tmp_path)]
    assert rels == ["src/a.py", "src/new.py"]  # refrepo/big.py 被 ignore,不进


def test_subdir_root_paths_relative_to_root(tmp_path):
    """root 取 git 仓的子目录(如 src/rootrecall):返回路径相对该子目录(与既有索引前缀一致)。"""
    sub = tmp_path / "pkg"
    (sub / "deep").mkdir(parents=True)
    (sub / "deep" / "m.py").write_text("a = 1\n", encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "init")

    rels = [rel for _, rel, _ in iter_source_files(sub)]
    assert rels == ["deep/m.py"]  # 不带 pkg/ 前缀,相对 sub


def test_non_git_repo_falls_back_to_walk(tmp_path):
    """非 git 仓:兜底 rglob 仍能扫到文件(宁可慢不可漏);隐藏目录照旧跳过。"""
    (tmp_path / "m.py").write_text("a = 1\n", encoding="utf-8")
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "n.py").write_text("b = 2\n", encoding="utf-8")
    (tmp_path / "not_py.txt").write_text("x", encoding="utf-8")

    rels = [rel for _, rel, _ in iter_source_files(tmp_path)]
    assert rels == ["m.py"]
