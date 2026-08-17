"""R3 收尾 ②[a] node_report_memorize 填齐 BugLesson 字段 · 离线逻辑测试。

不真跑 opencode / 报告渲染:mock get_memory_service(捕获 memorize 的 KI)+
mock render_report(本测试只关心 lesson 字段,不关心报告)。验 ②[a] 四字段填齐 +
commit_sha 在 git 仓/非 git 仓的兜底。
"""
from __future__ import annotations

import asyncio
import subprocess

from rootrecall.services.memory.schema import Scope
from rootrecall.tools.delegate import DelegateResult
from rootrecall.workflows.bug_rca import nodes


# ── 假 MemoryService:捕获 memorize 收到的 KI,不真写 ──
class _FakeSvc:
    def __init__(self):
        self.captured: list = []

    async def memorize(self, items, scope):
        self.captured.extend(items)
        return len(items)


def _state(repo_root, *, problem_summary=None, blast_radius=None,
           evidence=None, patch="DIFF", trigger="现象X 扫描挂起"):
    """造一份够 node_report_memorize 跑的 state。"""
    return {
        "repo_root": str(repo_root),
        "trigger": trigger,
        "scope": Scope(codebase="wpa"),
        "workspace": str(repo_root),
        "localization_json": {
            "root_cause": "根因R",
            "problem_summary": problem_summary,
            "blast_radius_files": blast_radius or [],
            "evidence": evidence if evidence is not None else [
                {"file": "a.c", "line": 10}, {"file": "b.c", "line": 20},
            ],
        },
        "delegate_result": DelegateResult(final_text="", data={}),  # 非 None 才过 node_report_memorize 守卫
        "patch": patch,
        "verified": True,
    }


def _patch(monkeypatch, tmp_path, svc):
    """render_report mock 成空串(不关心报告);get_memory_service 返假 svc;cwd 转 tmp(data/ 写 tmp)。"""
    monkeypatch.setattr("rootrecall.workflows.bug_rca.report.render_report", lambda state: "# report")
    monkeypatch.setattr(nodes, "get_memory_service", lambda: svc)
    monkeypatch.chdir(tmp_path)


def _git_repo(path) -> None:
    """在 path 建个真 git 仓 + 一个 commit(让 rev-parse HEAD 有 sha)。"""
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True)
    (path / "f.c").write_text("int main(void){return 0;}\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "init"], check=True)


# ═════════════════════════ ②[a] 四字段填齐 ═════════════════════════

def test_report_memorize_fills_four_fields(monkeypatch, tmp_path):
    """symptom/fix_patch/blast_radius_files/commit_sha 四字段从 localization/patch/repo 填齐。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_repo(repo)

    svc = _FakeSvc()
    _patch(monkeypatch, tmp_path, svc)
    asyncio.run(nodes.node_report_memorize(
        _state(repo, problem_summary="扫描挂起", blast_radius=["scan.c", "p2p.c"], patch="MYDIFF"),
    ))

    assert len(svc.captured) == 1
    ki = svc.captured[0]
    assert ki.symptom == "扫描挂起"
    assert ki.fix_patch == "MYDIFF"
    assert ki.blast_radius_files == ["scan.c", "p2p.c"]
    assert ki.commit_sha and len(ki.commit_sha) == 40  # 真 git 40 位 sha
    assert ki.root_cause == "根因R"


def test_report_memorize_field_fallbacks(monkeypatch, tmp_path):
    """delegate 没给 problem_summary/blast_radius → symptom 退 trigger、blast_radius 退 evidence 文件去重。"""
    repo = tmp_path / "repo"  # 非 git 仓(不 init)→ commit_sha None
    repo.mkdir()

    svc = _FakeSvc()
    _patch(monkeypatch, tmp_path, svc)
    asyncio.run(nodes.node_report_memorize(
        _state(repo, problem_summary=None, blast_radius=None,
               evidence=[{"file": "a.c", "line": 1}, {"file": "a.c", "line": 9}, {"file": "b.c"}],
               trigger="现象现象现象"),
    ))

    ki = svc.captured[0]
    assert ki.symptom == "现象现象现象"            # problem_summary 空 → 退 trigger[:200]
    assert ki.blast_radius_files == ["a.c", "b.c"]  # 空退 evidence 文件去重(a.c 不重复)
    assert ki.commit_sha is None                    # 非 git 仓 → None(防御)


def test_resolve_commit_sha_non_git(tmp_path):
    """_resolve_commit_sha 对非 git 目录返 None(不抛)。"""
    d = tmp_path / "notgit"
    d.mkdir()
    assert nodes._resolve_commit_sha(str(d)) is None


def test_resolve_commit_sha_git_repo(tmp_path):
    """_resolve_commit_sha 对真 git 仓返 40 位 sha。"""
    repo = tmp_path / "gitrepo"
    repo.mkdir()
    _git_repo(repo)
    sha = nodes._resolve_commit_sha(str(repo))
    assert sha and len(sha) == 40


def test_report_memorize_archives_to_workspace(monkeypatch, tmp_path):
    """报告/补丁同时写:全局 data/bug_rca/(latest)+ 本次 workspace/report/、/patch/(每次归档不丢)。"""
    repo = tmp_path / "wpa"  # 文件名按 repo_root.name 算 → 用 wpa 对齐断言
    repo.mkdir()
    ws = tmp_path / "ws_wpa__run1"  # 模拟本次 workspace(manager.py 建的,带 report/ patch/ 子目录)
    (ws / "report").mkdir(parents=True)
    (ws / "patch").mkdir(parents=True)

    svc = _FakeSvc()
    _patch(monkeypatch, tmp_path, svc)
    asyncio.run(nodes.node_report_memorize(
        {**_state(repo, patch="MYDIFF", blast_radius=["a.c"]), "workspace": str(ws)},
    ))

    # 全局 latest
    assert (tmp_path / "data/bug_rca/wpa-rca.md").exists()
    assert (tmp_path / "data/bug_rca/wpa.patch").read_text() == "MYDIFF"
    # 本次 workspace 归档(每 bug 一份,不被下次同仓跑覆盖)
    assert (ws / "report" / "wpa-rca.md").exists()
    assert (ws / "patch" / "wpa.patch").read_text() == "MYDIFF"
