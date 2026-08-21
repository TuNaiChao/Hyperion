"""P0 · 自然语言→自动开仓端到端:注册表只有基线时,find_repo → repo checkout --index 全链就绪。

剧本(对齐真实旅程:用户在 bug 目录问「分析 demo 5.50.61 根因」):
  ① 本地"上游"仓 2 commit(main=基线态 3 文件;tag 5.50.61=改 1 增 1),注册 baseline;
     基线态检出建全量索引(播种源,embed 计数对账);
  ② MCP find_repo(demo, 5.50.61)→ 未命中,回复带基线清单 + 含 --index、带安装根的开仓命令;
  ③ 按命令跑 CLI repo checkout demo-5.50.61 --ref 5.50.61 --bug B-9 --index:
     worktree 就位(tag 态内容)+ 播种基线索引增量建(embed 恰好只重嵌「改动+新增」文件的
     chunk,F5 同款对账)+ manifest 记 repo_path(F1 链路);
  ④ find_repo 复查 → 命中 ephemeral 候选,名字可直接当 repo_path 用。
隔离:注册表/镜像/worktree/安装根全锚 tmp + 计数假 embedder(F3/F5 同款手法)。
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import subprocess

import numpy as np
import pytest

import rootrecall.services.code_index.embed as embed_mod
import rootrecall.services.repos.mirror as mirror_mod
import rootrecall.services.repos.registry as reg_mod
from rootrecall.cli import main as cli_main
from rootrecall.services.code_index.chunker import chunk_repo
from rootrecall.tools.mcp_memory import build_server

_calls = {"n": 0}


class _CountingEmbedder:
    """假 embedder:确定性向量 + 全局计数(e2e 断言播种增量只重嵌差异文件)。"""

    @property
    def fingerprint(self) -> str:
        return "fake-embedder-v1"

    def embed_chunks(self, chunks):
        _calls["n"] += len(chunks)
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


def _call_tool(mcp, name: str, args: dict) -> str:
    blocks, _ = asyncio.run(mcp.call_tool(name, args))
    return blocks[0].text


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """上游仓(基线态 + tag 5.50.61)+ 全根锚 tmp + 基线全量索引就绪。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ROOTRECALL_REPOS_FILE", str(tmp_path / "repos.yaml"))
    monkeypatch.setattr(mirror_mod, "mirrors_root", lambda: tmp_path / "mirrors")
    monkeypatch.setattr(mirror_mod, "worktrees_root", lambda: tmp_path / "worktrees")
    monkeypatch.setattr(reg_mod, "_install_root", lambda: tmp_path)
    monkeypatch.setattr(embed_mod, "create_embedder", lambda cfg: _CountingEmbedder())
    _calls["n"] = 0

    up = tmp_path / "upstream"
    up.mkdir()
    _git(up, "init", "-q", "-b", "main")
    _git(up, "config", "user.email", "t@t.test")
    _git(up, "config", "user.name", "tester")
    (up / "a.c").write_text("int util_a(void) { return 1; }\n", encoding="utf-8")
    (up / "b.c").write_text("int util_b(void) { return 2; }\n", encoding="utf-8")
    (up / "d.c").write_text("int util_d(void) { return 4; }\n", encoding="utf-8")
    _git(up, "add", "-A")
    _git(up, "commit", "-qm", "init")                                        # c1(3 文件)
    (up / "a.c").write_text("int util_a(void) { return 100; }\n", encoding="utf-8")  # 小版本改 1
    (up / "c.c").write_text("int util_c(void) { return 3; }\n", encoding="utf-8")    # 小版本增 1
    _git(up, "add", "-A")
    _git(up, "commit", "-qm", "5.50.61")
    _git(up, "tag", "5.50.61")                                               # tag 定在 c2
    (up / "b.c").write_text("int util_b(void) { return 200; }\n", encoding="utf-8")  # main 领先一步
    _git(up, "add", "-A")
    _git(up, "commit", "-qm", "main ahead")                                  # c3 = main HEAD

    # 基线注册(url 走 file:// 本地仓,零网络)+ 基线态常驻检出 + 全量索引(播种源)
    assert cli_main(["repo", "register", "demo", "--url", str(up),
                     "--role", "baseline", "--branch", "main"]) == 0
    from rootrecall.services.repos.mirror import add_worktree, ensure_mirror
    mirror, _ = ensure_mirror("demo", str(up))
    baseline = tmp_path / "baseline"
    add_worktree(mirror, "main", baseline)
    reg_mod.RepoRegistry().register("demo", path=str(baseline))  # 补常驻检出路径(upsert)
    with contextlib.redirect_stdout(io.StringIO()):
        assert cli_main(["index", str(baseline), "demo", "--no-graph"]) == 0
    full_embeds = _calls["n"]
    assert full_embeds == len(chunk_repo(baseline))  # 播种源 = 全量,对账
    return {"tmp": tmp_path, "full_embeds": full_embeds}


def test_p0_autoprovision_e2e(env):
    tmp, full_embeds = env["tmp"], env["full_embeds"]

    # ── ② find_repo 未命中:基线清单 + 可原样跑的开仓命令(带安装根)────────
    mcp = build_server()
    out = _call_tool(mcp, "find_repo", {"project": "demo", "version": "5.50.61"})
    assert "No repo matched" in out and "- demo" in out and "branch=main" in out
    assert "baseline checkout demo-5.50.61" in out and "--ref 5.50.61" in out
    assert "--index" in out and str(tmp) in out  # 命令带 --project <安装根>,bash 可直接跑

    # ── ③ 按命令自动开仓:worktree + 播种增量索引一步就绪 ──────────────────
    with contextlib.redirect_stdout(io.StringIO()) as bo:
        assert cli_main(["repo", "checkout", "demo-5.50.61", "--from", "demo",
                         "--ref", "5.50.61", "--bug", "B-9", "--index"]) == 0
    out3 = bo.getvalue()
    assert "检出就绪" in out3 and "已播种向量索引" in out3 and "增量" in out3

    wt = tmp / "worktrees" / "demo-5.50.61"
    # worktree 且 tag 态:c2 的 a.c(return 100)在,main 领先的 b.c=200 不在
    assert (wt / "c.c").exists() and (wt / ".git").is_file()
    assert "return 100" in (wt / "a.c").read_text(encoding="utf-8")
    assert "return 200" not in (wt / "b.c").read_text(encoding="utf-8")
    mf = tmp / "data" / "code_index" / "demo-5.50.61" / "index_manifest.json"
    assert mf.exists(), "自动开仓应顺手建好索引"
    assert json.loads(mf.read_text(encoding="utf-8"))["repo_path"] == str(wt)  # F1 链路接上

    # 播种增量对账(F5 同款):基线(main=c3)↔ tag 差异只有 b.c → 恰好只重嵌 b.c 的 chunk
    expected = len([c for c in chunk_repo(wt) if c.file == "b.c"])
    assert _calls["n"] - full_embeds == expected
    assert 0 < expected < full_embeds, "测试前提:tag↔基线差异应显著小于基线全量"

    # ── ④ find_repo 复查:命中 ephemeral,名字即 repo_path ─────────────────
    out4 = _call_tool(mcp, "find_repo", {"project": "demo", "version": "5.50.61"})
    assert "demo-5.50.61" in out4 and "[ephemeral]" in out4 and "bug=B-9" in out4


def test_p0_checkout_index_without_baseline_index_falls_back_to_full(env):
    """基线索引不存在(播种源缺失)→ --index 诚实走全量,不炸、不静默跳过。"""
    import shutil

    tmp = env["tmp"]
    shutil.rmtree(tmp / "data" / "code_index" / "demo")  # 撤掉播种源
    with contextlib.redirect_stdout(io.StringIO()) as bo:
        assert cli_main(["repo", "checkout", "demo-full-5.50.61", "--from", "demo",
                         "--ref", "5.50.61", "--index"]) == 0
    out = bo.getvalue()
    assert "检出就绪" in out and "seed=无 → 全量" in out and "全量" in out
    assert (tmp / "data" / "code_index" / "demo-full-5.50.61" / "index_manifest.json").exists()
