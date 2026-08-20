"""P1 · ROOTRECALL_HOME 端到端:数据整体迁出仓库克隆,全链落新家、不碰安装根。

剧本(期望①收尾:data/ 可迁 ~/.local/share/rootrecall 这类位置,git pull 升级不碰数据):
  ① 模拟安装根 tmp/install(注册表/镜像/worktree 默认本该落这里);
  ② 设 ROOTRECALL_HOME=tmp/home + conftest 的 REPOS_FILE 覆盖撤销(让注册表走 data 根);
  ③ 走真 CLI:register → checkout --index(计数假 embedder);
  ④ 断言 repos.yaml / mirrors / worktrees / code_index / structgraph 全在 tmp/home,
     tmp/install 下一片空白(没有 data/)。
零行为变化约束:env 未设时一切照旧 —— 单测 test_reanchor_data_path_zero_behavior_without_env 锁。
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import subprocess

import numpy as np
import pytest

import rootrecall.services.code_index.embed as embed_mod
import rootrecall.services.repos.registry as reg_mod
from rootrecall.cli import main as cli_main


class _FakeEmbedder:
    @property
    def fingerprint(self) -> str:
        return "fake-embedder-v1"

    def embed_chunks(self, chunks):
        return np.stack([self._vec(c.id) for c in chunks])

    def embed_query(self, query: str) -> np.ndarray:
        return self._vec(query)

    @staticmethod
    def _vec(key: str) -> np.ndarray:
        h = hashlib.sha256(key.encode()).digest()
        v = np.frombuffer(h[:8], dtype=np.uint8).astype(np.float32)
        return v / (np.linalg.norm(v) + 1e-9)


def _git(path, *argv) -> None:
    r = subprocess.run(["git", "-C", str(path), *argv], capture_output=True, text=True)
    assert r.returncode == 0, f"git {argv} 失败: {r.stderr}"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    install, home = tmp_path / "install", tmp_path / "home"
    install.mkdir()
    # conftest autouse 已把 REPOS_FILE 锚到 tmp_path;这里撤销让注册表走 data 根(本 e2e 的被测对象)
    monkeypatch.delenv("ROOTRECALL_REPOS_FILE", raising=False)
    monkeypatch.setenv("ROOTRECALL_HOME", str(home))
    monkeypatch.setattr(reg_mod, "_install_root", lambda: install)
    monkeypatch.setattr(embed_mod, "create_embedder", lambda cfg: _FakeEmbedder())
    monkeypatch.chdir(tmp_path)  # cmd_index 未设 env 时是 cwd 相对 —— 这里 env 已设,应不受 cwd 影响

    up = tmp_path / "up"
    up.mkdir()
    _git(up, "init", "-q", "-b", "main")
    _git(up, "config", "user.email", "t@t.test")
    _git(up, "config", "user.name", "tester")
    (up / "a.c").write_text("int util_a(void) { return 1; }\n", encoding="utf-8")
    _git(up, "add", "-A")
    _git(up, "commit", "-qm", "init")
    _git(up, "tag", "1.0.0")
    return {"install": install, "home": home, "up": up}


def test_p1_data_moves_to_home_e2e(env):
    install, home, up = env["install"], env["home"], env["up"]

    assert cli_main(["repo", "register", "demo", "--url", str(up),
                     "--role", "baseline", "--branch", "main"]) == 0
    assert (home / "repos.yaml").is_file(), "注册表应落 ROOTRECALL_HOME"

    with contextlib.redirect_stdout(io.StringIO()) as bo:
        assert cli_main(["repo", "checkout", "demo-1.0.0", "--from", "demo",
                         "--ref", "1.0.0", "--index"]) == 0
    assert "已播种" not in bo.getvalue() and "全量" in bo.getvalue()  # 基线没索引 → 诚实全量

    # 四类数据全在新家;安装根(模拟仓库克隆)下没有 data/ —— git pull 升级不碰数据
    assert (home / "mirrors" / "demo.git").is_dir()
    assert (home / "worktrees" / "demo-1.0.0" / "a.c").is_file()
    assert (home / "code_index" / "demo-1.0.0" / "index_manifest.json").is_file()
    assert (home / "structgraph" / "demo-1.0.0").exists()  # CRG 装了才有;没装也允许(CI 环境防御)
    assert not (install / "data").exists(), "安装根不应再长出 data/"
