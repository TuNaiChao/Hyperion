"""F1 · repo registry 端到端:建索引记下 repo_path → MCP 工具吃「裸名字」→ CLI 反查。

模拟用户真实链路(零网络、零真 embed —— 假 embedder 出确定性向量):
  ① fixture git 仓(带 bug 符号的 C 文件)→ build_index(假 embedder)→ manifest 记 repo_path;
  ② MCP 工具(when_introduced / validate_patch)repo_path 直接传**索引名**,应反查出仓库路径干活;
  ③ CLI `repo register` 登记 baseline → `repo resolve` 注册表优先命中 → `repo ls` 可见;
  ④ `repo rm` 只删记录不动盘上文件。

隔离:注册表/索引目录全指到 tmp(绝不碰真 data/)。
"""

from __future__ import annotations

import hashlib
import json
import subprocess

import numpy as np

from rootrecall.cli import main as cli_main
from rootrecall.services.code_index.chunker import CodeChunk
from rootrecall.services.code_index.index import build_index
from rootrecall.tools.mcp_memory import build_server

_IDX_NAME = "e2e-bluez-v20"


class _FakeEmbedder:
    """假 embedder(与 test_store_index_incremental 同款):确定性哈希向量,零网络。"""

    @property
    def fingerprint(self) -> str:
        return "fake-embedder-v1"

    def embed_chunks(self, chunks: list[CodeChunk]) -> np.ndarray:
        return np.stack([self._vec(c.id) for c in chunks])

    def embed_query(self, query: str) -> np.ndarray:
        return self._vec(query)

    @staticmethod
    def _vec(key: str) -> np.ndarray:
        h = hashlib.sha256(key.encode()).digest()
        v = np.frombuffer(h[:8], dtype=np.uint8).astype(np.float32)
        return v / (np.linalg.norm(v) + 1e-9)


def _git(path, *argv, check=True) -> str:
    r = subprocess.run(["git", "-C", str(path), *argv], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise AssertionError(f"git {argv} 失败: {r.stderr}")
    return r.stdout


def _make_repo(path) -> None:
    """两 commit 的 C 仓:第二 commit 引入 bug 符号(when_introduced 的 pickaxe 锚点)。"""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@t.test")
    _git(path, "config", "user.name", "tester")
    (path / "core.c").write_text(
        "int util(void) { return 1; }\nint main(void) { return util(); }\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "init")
    (path / "buggy.c").write_text(
        "int e2e_buggy_leak(void) { return 42; }\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "introduce buggy symbol")


def _call_tool(mcp, name: str, args: dict) -> str:
    import asyncio
    blocks, _ = asyncio.run(mcp.call_tool(name, args))
    return blocks[0].text


def test_f1_registry_e2e(tmp_path, monkeypatch):
    # ── ① 建索引:manifest 应记录 repo_path ──────────────────────────────────
    repo = tmp_path / "bluez"
    _make_repo(repo)
    idx_root = tmp_path / "data" / "code_index"
    stats = build_index(repo, _IDX_NAME, _FakeEmbedder(), idx_root)
    assert stats["mode"] == "full" and stats["indexed"] > 0
    manifest = json.loads((idx_root / _IDX_NAME / "index_manifest.json").read_text(encoding="utf-8"))
    assert manifest["repo_path"] == str(repo.resolve()), "F1 核心:索引清单必须记下源码仓绝对路径"

    # ── ② MCP 工具吃裸名字:resolve 链走 index-manifest 命中 ─────────────────
    import rootrecall.services.repos.registry as reg_mod

    monkeypatch.setenv("ROOTRECALL_REPOS_FILE", str(tmp_path / "repos.yaml"))  # 空注册表
    monkeypatch.setattr(reg_mod, "_default_index_dir", lambda: idx_root)
    mcp = build_server()

    out = _call_tool(mcp, "when_introduced", {"repo_path": _IDX_NAME, "symbol": "e2e_buggy_leak"})
    assert "没法算" not in out and "e2e_buggy_leak" not in ("",)
    assert "introduce buggy symbol" in out, f"裸名字应反查出仓库并跑通 pickaxe:{out[:300]}"

    # validate_patch 同款:对 HEAD 态做 forward apply(改一行 → 还原 → 修补丁)
    (repo / "buggy.c").write_text("int e2e_buggy_leak(void) { return 43; }\n", encoding="utf-8")
    diff = _git(repo, "diff")
    _git(repo, "checkout", "--", "buggy.c")
    out = _call_tool(mcp, "validate_patch", {"patch": diff, "repo_path": _IDX_NAME})
    assert "applies=True" in out, f"裸名字应解析出工作树并验 apply:{out[:300]}"

    # ── ③ CLI:登记 baseline → resolve 注册表优先 → ls 可见 ──────────────────
    rc = cli_main(["repo", "register", _IDX_NAME, "--path", str(repo), "--url",
                   "https://intr/bluez.git", "--role", "baseline", "--branch", "v20-stable"])
    assert rc == 0
    assert (tmp_path / "repos.yaml").exists()

    # 注册表优先于 manifest(人是显式声明的真相):换个路径也能被解析到。
    moved = tmp_path / "moved" / "bluez"
    moved.mkdir(parents=True)
    subprocess.run(["cp", "-r", f"{repo}/.", str(moved)], check=True)
    cli_main(["repo", "register", _IDX_NAME, "--path", str(moved), "--role", "baseline"])
    out_path, source = reg_mod.resolve_repo_path(_IDX_NAME)
    assert out_path == moved.resolve() and source.startswith("registry[baseline]")

    rc = cli_main(["repo", "ls"])
    assert rc == 0

    # ── ④ rm 只删记录,不动盘上文件 ──────────────────────────────────────────
    assert cli_main(["repo", "rm", _IDX_NAME]) == 0
    assert reg_mod.RepoRegistry().get(_IDX_NAME) is None
    assert moved.is_dir(), "rm 只删记录,仓库文件必须还在"
    # 删记录后 resolve 回落到 index-manifest(还在,索引没删)
    out_path, source = reg_mod.resolve_repo_path(_IDX_NAME)
    assert out_path == repo.resolve() and source == "index-manifest"
