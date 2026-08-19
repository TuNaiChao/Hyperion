"""F4 · repo sync 端到端:fetch→ff→增量索引→上游三态分析报告(全 tmp,file:// remote,零网络)。

场景剧本(对应真实用法:上游仓 vs 发行版 fork 仓):
  upstream: A(init) ──随后── U1(新文件,干净) U2(动 core.c 第2行) U3(新文件)
  fork:    A ── F(动 core.c 第2行,与 U2 冲突) + cherry-pick U3(= 已修)
  baseline: A 的 clone(注册 baseline,url=upstream,branch=main)

  ① `repo sync baseline --no-index --analyze fork`:
     新 commit 3 条、ff 跟进;三态报告 = U1:recommend_merge / U2:conflict / U3:already_fixed;
     报告落 data/upstream_reports/baseline/*.md;记录回写 synced_sha/last_synced_at。
  ② 直接调 sync_repo(注入假 embedder):索引建立(full);上游再加 U4 → 再 sync → 增量(incremental)。
  ③ 二次 CLI sync(无新 commit):new_commits 空,幂等。
"""

from __future__ import annotations

import contextlib
import io
import subprocess

import pytest

import rootrecall.services.repos.mirror as mirror_mod
import rootrecall.services.repos.registry as reg_mod
from rootrecall.cli import main as cli_main
from rootrecall.services.repos.mirror import sync_repo


class _FakeEmbedder:
    """假 embedder(与 test_store_index_incremental 同款):确定性哈希向量,零网络。"""

    @property
    def fingerprint(self) -> str:
        return "fake-embedder-v1"

    def embed_chunks(self, chunks):
        import hashlib

        import numpy as np
        return np.stack([
            np.frombuffer(hashlib.sha256(c.id.encode()).digest()[:8], dtype=np.uint8).astype(np.float32)
            / 256.0 for c in chunks])

    def embed_query(self, query: str):
        return self.embed_chunks([type("C", (), {"id": query})()])[0]


def _git(path, *argv, check=True) -> str:
    r = subprocess.run(["git", "-C", str(path), *argv], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise AssertionError(f"git {argv} 失败: {r.stderr}")
    return r.stdout


def _commit(path, msg, files: dict) -> str:
    for name, text in files.items():
        (path / name).write_text(text, encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", msg)
    return _git(path, "rev-parse", "HEAD").strip()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """upstream / baseline clone / fork clone 三仓 + 注册表与数据根隔离。"""
    monkeypatch.setenv("ROOTRECALL_REPOS_FILE", str(tmp_path / "repos.yaml"))
    monkeypatch.setattr(reg_mod, "_install_root", lambda: tmp_path)
    monkeypatch.setattr(mirror_mod, "mirrors_root", lambda: tmp_path / "mirrors")

    up = tmp_path / "up"
    up.mkdir()
    _git(up, "init", "-q", "-b", "main")
    _git(up, "config", "user.email", "t@t.test")
    _git(up, "config", "user.name", "tester")
    _commit(up, "A init", {"core.c": "int base(void){return 0;}\n"})

    base = tmp_path / "baseline"
    subprocess.run(["git", "clone", "-q", str(up), str(base)], check=True)
    fork = tmp_path / "fork"
    subprocess.run(["git", "clone", "-q", str(up), str(fork)], check=True)
    for p in (base, fork):
        _git(p, "config", "user.email", "t@t.test")
        _git(p, "config", "user.name", "tester")

    assert cli_main(["repo", "register", "e2e-base", "--path", str(base),
                     "--url", str(up), "--role", "baseline", "--branch", "main"]) == 0
    assert cli_main(["repo", "register", "e2e-fork", "--path", str(fork)]) == 0
    return {"up": up, "base": base, "fork": fork, "tmp": tmp_path}


def test_f4_sync_analyze_e2e(env):
    up, base, fork, tmp = env["up"], env["base"], env["fork"], env["tmp"]

    # 上游出三个新 commit;fork 侧:F 与 U2 冲突,cherry-pick U3(制造 already_fixed)。
    _commit(up, "U1 add fix1", {"fix1.c": "int fix1(void){return 1;}\n"})
    _commit(up, "U2 change core line", {"core.c": "int base(void){return 2;}\n"})
    u3 = _commit(up, "U3 add fix3", {"fix3.c": "int fix3(void){return 3;}\n"})
    _commit(fork, "F fork change core line", {"core.c": "int base(void){return 9;}\n"})
    _git(fork, "remote", "add", "upstream-tmp", str(up))
    _git(fork, "fetch", "-q", "upstream-tmp")
    _git(fork, "cherry-pick", u3, check=False)  # 已在 upstream-tmp 里可见
    _git(fork, "remote", "remove", "upstream-tmp")

    # ── ① CLI sync:fetch + ff + 三态分析 ────────────────────────────────────
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli_main(["repo", "sync", "e2e-base", "--no-index", "--analyze", "e2e-fork"])
    assert rc == 0, buf.getvalue()
    out = buf.getvalue()
    assert "新 commit 3" in out and "ff 跟进" in out
    assert "already_fixed=1" in out and "recommend_merge=1" in out and "conflict=1" in out

    # ff 真的跟进了(baseline HEAD == upstream main)
    assert _git(base, "rev-parse", "HEAD").strip() == _git(up, "rev-parse", "main").strip()
    assert (base / "fix3.c").exists()
    # fork 没被动过(分析零 touch)
    assert "return 9" in (fork / "core.c").read_text(encoding="utf-8")

    # 报告落盘,含三态与 commit 行
    reports = list((tmp / "data" / "upstream_reports" / "e2e-base").glob("*-sync.md"))
    assert len(reports) == 1
    body = reports[0].read_text(encoding="utf-8")
    assert "already_fixed=1" in body and "U1 add fix1" in body and "U3 add fix3" in body
    # 记录回写
    rec = reg_mod.RepoRegistry().get("e2e-base")
    assert rec.synced_sha == _git(up, "rev-parse", "main").strip()
    assert rec.last_synced_at

    # ── ② 直接调 sync_repo(假 embedder):索引 full → 上游再加 U4 → incremental ──
    idx_root = tmp / "data" / "code_index"
    r = sync_repo("e2e-base", embedder=_FakeEmbedder(), registry=reg_mod.RepoRegistry(),
                  code_index_dir=idx_root)
    assert r["index"]["mode"] == "full"
    _commit(up, "U4 add fix4", {"fix4.c": "int fix4(void){return 4;}\n"})
    r = sync_repo("e2e-base", embedder=_FakeEmbedder(), registry=reg_mod.RepoRegistry(),
                  code_index_dir=idx_root)
    assert r["index"]["mode"] == "incremental"
    assert len(r["new_commits"]) == 1 and "U4" in r["new_commits"][0]

    # ── ③ 再跑 CLI sync:无新 commit,幂等 ───────────────────────────────────
    with contextlib.redirect_stdout(io.StringIO()) as b2:
        rc = cli_main(["repo", "sync", "e2e-base", "--no-index"])
    assert rc == 0 and "新 commit 0" in b2.getvalue()


def test_f4_sync_skip_non_baseline_and_missing(env):
    """非 baseline 不 sync;未注册报错不崩。"""
    with contextlib.redirect_stdout(io.StringIO()) as b:
        assert cli_main(["repo", "sync", "e2e-fork"]) == 0
    assert "只管 baseline" in b.getvalue()
    with contextlib.redirect_stdout(io.StringIO()):
        rc = cli_main(["repo", "sync", "no-such-repo", "--no-index"])
    assert rc == 1
