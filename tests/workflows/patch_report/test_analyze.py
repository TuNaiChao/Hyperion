"""patch_report · _analyze_one_pr 单测(P-A 1b,Checkpoint 3)。

不依赖网络/真 LLM/真 CRG 图:
  - model:monkeypatch create_chat_model → 桩 ainvoke 返固定 cited JSON。
  - CRG:CodeGraph.open 对不存在的 codebase 抛 FileNotFoundError → _analyze_one_pr 降级(无 risk/modules)。
  - validate_patch:真 temp git 仓 + 合法 diff → applies=True。
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace

from hyperion.services.patch.fetcher import PatchArtifact
from hyperion.workflows.patch_report._analyze import _analyze_one_pr


def _run(coro):
    return asyncio.run(coro)


def _make_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    base = ["git", "-C", str(repo)]
    subprocess.run([*base, "init", "-q"], check=True, capture_output=True)
    subprocess.run([*base, "config", "user.email", "t@t.test"], check=True, capture_output=True)
    subprocess.run([*base, "config", "user.name", "t"], check=True, capture_output=True)
    (repo / "f.c").write_text("int old;\n", encoding="utf-8")
    subprocess.run([*base, "add", "-A"], check=True, capture_output=True)
    subprocess.run([*base, "commit", "-q", "-m", "init"], check=True, capture_output=True)


# 能干净 apply 到 f.c 的补丁。
_DIFF = (
    "diff --git a/f.c b/f.c\n--- a/f.c\n+++ b/f.c\n"
    "@@ -1 +1,2 @@\n int old;\n+int new;\n"
)


class _StubModel:
    """桩 model:ainvoke 返固定 cited JSON(_cited_summarize 走 happy path)。"""

    def __init__(self, content: str):
        self._content = content

    async def ainvoke(self, msgs):  # noqa: ARG002
        return SimpleNamespace(content=self._content)


def test_analyze_one_pr_happy_path(monkeypatch, tmp_path):
    """mock model + CRG 降级 + 真 apply → PRFinding{applies=True, cited summary, theme}。"""
    repo = tmp_path / "repo"
    _make_repo(repo)
    art = PatchArtifact(
        url="https://github.com/o/r/pull/1", source_kind="github", diff=_DIFF,
        title="Add new var", changed_files=["f.c"])

    canned = ('{"summary":"adds new var at f.c:2","citations":'
              '[{"file":"f.c","line":2,"symbol":"new","claim":"added"}],"theme":"function"}')
    monkeypatch.setattr("hyperion.platform.models.create_chat_model",
                        lambda role: _StubModel(canned))

    finding = _run(_analyze_one_pr(art, repo_root=str(repo), codebase="test_nograph_xyz"))

    assert finding["url"] == art.url
    assert finding["applies"] is True                # diff 干净打到 f.c
    assert finding["theme"] == "function"
    assert finding["security_tier"] == "none"        # CRG 降级(无 changed_funcs)→ none
    assert finding["risk_score"] == 0.0              # CRG 降级 → 0
    assert finding["citations"]                       # cited-reporter 出了引用
    assert finding["citations"][0]["file"] == "f.c"
    assert "new" in finding["summary"].lower() or "add" in finding["summary"].lower()


def test_analyze_one_pr_llm_degrades_on_bad_json(monkeypatch, tmp_path):
    """LLM 返非 JSON → 降级(summary=evidence 首 300 字,空 citations),不抛。"""
    repo = tmp_path / "repo"
    _make_repo(repo)
    art = PatchArtifact(url="u", source_kind="github", diff=_DIFF, title="t", changed_files=["f.c"])
    monkeypatch.setattr("hyperion.platform.models.create_chat_model",
                        lambda role: _StubModel("这不是 JSON,模型抽风了"))

    finding = _run(_analyze_one_pr(art, repo_root=str(repo), codebase="test_nograph_xyz"))
    assert finding["citations"] == []                 # 降级:空引用
    assert finding["summary"]                         # 仍有降级 summary
    assert finding["theme"] == "function"             # 降级默认


def test_diff_to_abs_ranges():
    """diff → 绝对路径文件 + 行范围(CRG qn 是 abs-path::symbol,file 要拼 repo_root)。"""
    from hyperion.workflows.patch_report._analyze import _diff_to_abs_ranges

    files, ranges = _diff_to_abs_ranges(_DIFF, "/repo")
    assert files == ["/repo/f.c"]
    assert ranges["/repo/f.c"] == [(1, 3)]            # @@ -1 +1,2 @@ → new_start=1, span=2(context+added)


def test_security_tier_keyword_hit():
    """changed_funcs 名字命中 SECURITY_KEYWORDS(auth)+ risk 高 → high。"""
    from hyperion.workflows.patch_report._analyze import _security_tier

    funcs = [{"name": "auth_login", "qualified_name": "x::auth_login"}]
    assert _security_tier(funcs, 0.7) == "high"
    assert _security_tier(funcs, 0.2) == "relevant"   # 命中词但 risk 低 → relevant
    assert _security_tier([], 0.2) == "none"          # 无 funcs → none
