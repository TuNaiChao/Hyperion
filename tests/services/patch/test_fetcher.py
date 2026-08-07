"""P-A 1a · GitHubFetcher 离线测试(httpx MockTransport,不碰网络)。

覆盖:
  - 公开 PR:fetch → diff + meta(title/body/merge_commit_sha)+ changed_files。
  - diff media 头(Accept: application/vnd.github.v3.diff)→ 返 diff 体。
  - 404(PR 不存在)→ 抛(httpx.HTTPStatusError),调方降级。
  - 非 GitHub URL → ValueError。
  - token 注入:GITHUB_TOKEN env 在 → Authorization 头带上。
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from hyperion.services.patch.fetcher import _GH_PR_RE, GitHubFetcher, PatchArtifact


def _diff() -> str:
    return ("diff --git a/f.c b/f.c\nindex 1..2 100644\n--- a/f.c\n+++ b/f.c\n"
            "@@ -1 +1,2 @@\n int old;\n+int new;\n")


def _handler(diff_text: str = _diff(), meta_status: int = 200):
    """构造一个按 Accept 头 + URL 分流的 MockTransport handler。

    meta_status != 200 时所有请求都返该状态(测 404/错误降级)。
    """
    meta = {"title": "Fix X", "body": "body text", "merge_commit_sha": "deadbeef"}
    files = [{"filename": "f.c"}]

    def handle(request: httpx.Request) -> httpx.Response:
        if meta_status != 200:
            return httpx.Response(meta_status, json={"message": "err"})
        url = str(request.url)
        if request.headers.get("accept", "").endswith(".diff"):
            return httpx.Response(200, text=diff_text)
        if "/files" in url:  # 带 ?per_page=100 查询串,用包含匹配
            return httpx.Response(200, json=files)
        return httpx.Response(200, json=meta)

    return handle


def _run(coro):
    """同步跑一个 async fetch(测试里好断言)。"""
    return asyncio.run(coro)


# ── URL 解析 ─────────────────────────────────────────────────────────────────


def test_url_regex():
    m = _GH_PR_RE.search("https://github.com/torvalds/linux/pull/42")
    assert m and m.group("owner") == "torvalds" and m.group("repo") == "linux" and m.group("num") == "42"


# ── 正常抓取 ─────────────────────────────────────────────────────────────────


def test_fetch_public_pr_ok():
    f = GitHubFetcher(transport=httpx.MockTransport(_handler()))
    art = _run(f.fetch("https://github.com/o/r/pull/1"))
    assert isinstance(art, PatchArtifact)
    assert art.source_kind == "github"
    assert art.title == "Fix X" and art.body == "body text"
    assert art.merge_commit_sha == "deadbeef"
    assert "diff --git" in art.diff and "int new" in art.diff
    assert art.changed_files == ["f.c"]


def test_fetch_token_injects_auth_header(monkeypatch):
    """GITHUB_TOKEN 在 → 每个请求带 Authorization: token ...。"""
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    seen = {}

    def handle(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        if request.headers.get("accept", "").endswith(".diff"):
            return httpx.Response(200, text=_diff())
        if "/files" in str(request.url):
            return httpx.Response(200, json=[{"filename": "f.c"}])
        return httpx.Response(200, json={"title": "t", "body": "", "merge_commit_sha": None})

    f = GitHubFetcher(transport=httpx.MockTransport(handle))
    _run(f.fetch("https://github.com/o/r/pull/1"))
    assert seen["auth"] == "token ghp_test"


# ── 错误路径 ─────────────────────────────────────────────────────────────────


def test_fetch_404_raises():
    f = GitHubFetcher(retries=1, transport=httpx.MockTransport(_handler(meta_status=404)))
    with pytest.raises(httpx.HTTPStatusError):
        _run(f.fetch("https://github.com/o/r/pull/999"))


def test_fetch_non_github_url_raises():
    f = GitHubFetcher(transport=httpx.MockTransport(_handler()))
    with pytest.raises(ValueError):
        _run(f.fetch("https://gitlab.com/o/r/-/merge_requests/1"))


def test_gerrit_fetcher_is_stub():
    """GerritFetcher 留接口未实现(1d),fetch 抛 NotImplementedError。"""
    from hyperion.services.patch.fetcher import GerritFetcher
    with pytest.raises(NotImplementedError):
        _run(GerritFetcher().fetch("https://gerrit-review.googlesource.com/c/proj/+/123"))
