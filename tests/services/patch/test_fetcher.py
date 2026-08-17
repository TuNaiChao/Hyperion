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

from rootrecall.services.patch.fetcher import (
    _GH_PR_RE,
    GitHubFetcher,
    PatchArtifact,
    RateLimitError,
)


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


def test_fetch_403_rate_limit_raises_friendly_hint():
    """匿名配额耗尽(403 + X-RateLimit-Remaining: 0)→ RateLimitError,消息提示 GITHUB_TOKEN。"""
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, json={"message": "rate limit exceeded"},
            headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1786091996"},
        )

    f = GitHubFetcher(retries=1, transport=httpx.MockTransport(handle))
    with pytest.raises(RateLimitError) as ei:
        _run(f.fetch("https://github.com/o/r/pull/1"))
    assert "GITHUB_TOKEN" in str(ei.value) and "5000" in str(ei.value)


def test_fetch_403_forbidden_not_mistaken_for_rate_limit():
    """403 但非限速(私有仓无权限,剩余配额正常)→ 普通 HTTPStatusError,不误判成限速。"""
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, json={"message": "Forbidden"},
            headers={"X-RateLimit-Remaining": "59"},  # 配额还多 → 不是限速
        )

    f = GitHubFetcher(retries=1, transport=httpx.MockTransport(handle))
    with pytest.raises(httpx.HTTPStatusError):
        _run(f.fetch("https://github.com/o/r/pull/1"))


def test_fetch_non_github_url_raises():
    f = GitHubFetcher(transport=httpx.MockTransport(_handler()))
    with pytest.raises(ValueError):
        _run(f.fetch("https://gitlab.com/o/r/-/merge_requests/1"))


def test_gerrit_fetcher_strips_xssi_and_decodes_patch():
    """GerritFetcher:剥 `)]}'` XSSI 前缀取 meta + base64 解码 patch + 从 diff 抽 changed_files。"""
    import base64
    import json as _json

    from rootrecall.services.patch.fetcher import GerritFetcher

    diff_text = "diff --git a/a.c b/a.c\n--- a/a.c\n+++ b/a.c\n@@ -1 +1,2 @@\n-x\n+y\n"
    # Gerrit JSON 响应带 )]}' 前缀;change 列表含 subject/id/revisions。
    meta = ")]}'\n" + _json.dumps([{"subject": "Fix X", "id": "proj~main~42", "revisions": {"abc123": {}}}])
    b64_patch = base64.b64encode(diff_text.encode()).decode()

    def handle(request: httpx.Request) -> httpx.Response:
        u = str(request.url)
        if "revisions/current/patch" in u:
            return httpx.Response(200, text=b64_patch)        # base64 diff,无前缀
        if "/changes" in u:
            return httpx.Response(200, text=meta)              # meta,带 )]}' 前缀
        return httpx.Response(404, json={"message": "nope"})

    art = _run(GerritFetcher(transport=httpx.MockTransport(handle))
               .fetch("https://gerrit-review.googlesource.com/c/proj/+/42"))
    assert art.source_kind == "gerrit"
    assert art.title == "Fix X"
    assert art.merge_commit_sha == "abc123"
    assert "diff --git" in art.diff and "+y" in art.diff
    assert art.changed_files == ["a.c"]


def test_gerrit_fetcher_non_gerrit_url():
    from rootrecall.services.patch.fetcher import GerritFetcher
    with pytest.raises(ValueError):
        _run(GerritFetcher(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="")))
             .fetch("https://github.com/o/r/pull/1"))


def test_gerrit_fetcher_change_not_found():
    """change 查询返空列表 → ValueError(404/无权限)。"""
    from rootrecall.services.patch.fetcher import GerritFetcher

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=")]}'\n[]")  # 空 change 列表

    with pytest.raises(ValueError):
        _run(GerritFetcher(transport=httpx.MockTransport(handle))
             .fetch("https://gerrit-review.googlesource.com/c/proj/+/999"))


def test_diff_changed_files_helper():
    from rootrecall.services.patch.fetcher import _diff_changed_files
    diff = ("diff --git a/a.c b/a.c\n+++ b/a.c\n@@\n+x\n"
            "diff --git a/b.c b/b.c\n+++ /dev/null\n@@\n-x\n"
            "diff --git a/a.c b/a.c\n+++ b/a.c\n@@\n+y\n")  # a.c 重复,去重
    assert _diff_changed_files(diff) == ["a.c"]  # /dev/null 跳过、a.c 去重


def test_diff_hunk_lines_helper():
    """diff_hunk_lines:按新文件归改动行区间(`@@ -a,b +c,d @@` 取新侧 c..c+d-1)。"""
    from rootrecall.services.patch.fetcher import diff_hunk_lines
    diff = ("diff --git a/f.c b/f.c\n--- a/f.c\n+++ b/f.c\n"
            "@@ -1,2 +1,3 @@\n int old;\n+int new;\n int keep;\n"
            "@@ -10,3 +11,2 @@\n-int gone;\n int stay;\n"
            # 第二个文件、删除文件(无新行)、长度省略(视为 1)
            "diff --git a/g.c b/g.c\n--- a/g.c\n+++ b/g.c\n@@ -5 +5 @@\n-x\n+y\n"
            "diff --git a/del.c b/del.c\n--- a/del.c\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n")
    out = diff_hunk_lines(diff)
    assert out["f.c"] == [(1, 3), (11, 12)]   # +1,3→[1,3];+11,2→[11,12]
    assert out["g.c"] == [(5, 5)]             # +5(长度省略=1)→[5,5]
    assert "del.c" not in out                 # /dev/null 不产区间


# ── Gerrit 鉴权(Gap A)─────────────────────────────────────────────────────────


def _gerrit_handler(record: dict):
    """MockTransport:返回 meta + base64 patch,把请求 URL / Authorization 记进 record。"""
    import base64
    import json as _json

    diff_text = "diff --git a/a.c b/a.c\n--- a/a.c\n+++ b/a.c\n@@ -1 +1,2 @@\n-x\n+y\n"
    meta = ")]}'\n" + _json.dumps([{"subject": "Fix X", "id": "proj~main~42",
                                    "revisions": {"abc123": {}}}])
    b64_patch = base64.b64encode(diff_text.encode()).decode()

    def handle(request: httpx.Request) -> httpx.Response:
        record["url"] = str(request.url)
        record["auth"] = request.headers.get("authorization")
        u = str(request.url)
        if "revisions/current/patch" in u:
            return httpx.Response(200, text=b64_patch)
        if "/changes" in u:
            return httpx.Response(200, text=meta)
        return httpx.Response(404, json={"message": "nope"})

    return handle


def test_gerrit_auth_uses_a_prefix_and_basic():
    """有凭据 → 端点走 /a/ 前缀 + 带 Authorization: Basic。"""
    from rootrecall.services.patch.fetcher import GerritFetcher

    rec = {}
    f = GerritFetcher(username="alice", http_password="secret-token",
                      transport=httpx.MockTransport(_gerrit_handler(rec)))
    art = _run(f.fetch("https://gerrit.example.com/c/proj/+/42"))
    assert art.source_kind == "gerrit" and "+y" in art.diff   # 抓取本身仍正常
    assert "/a/changes/" in rec["url"], f"应走 /a/ 前缀,实: {rec['url']}"
    assert rec["auth"] and rec["auth"].startswith("Basic "), "应带 Basic 鉴权头"


def test_gerrit_anonymous_no_a_prefix_no_auth(monkeypatch):
    """无凭据(env 也没设)→ 匿名:无 /a/ 前缀、无 Authorization 头(回归现有行为)。"""
    from rootrecall.services.patch.fetcher import GerritFetcher

    monkeypatch.delenv("GERRIT_USERNAME", raising=False)
    monkeypatch.delenv("GERRIT_HTTP_PASSWORD", raising=False)
    rec = {}
    f = GerritFetcher(transport=httpx.MockTransport(_gerrit_handler(rec)))
    _run(f.fetch("https://gerrit.example.com/c/proj/+/42"))
    assert "/a/" not in rec["url"], f"匿名不该走 /a/,实: {rec['url']}"
    assert rec["auth"] is None, "匿名不该带鉴权头"


def test_gerrit_reads_creds_from_env(monkeypatch):
    """GERRIT_USERNAME / GERRIT_HTTP_PASSWORD env 在 → 自动鉴权(对齐 GITHUB_TOKEN 惯例)。"""
    from rootrecall.services.patch.fetcher import GerritFetcher

    monkeypatch.setenv("GERRIT_USERNAME", "bob")
    monkeypatch.setenv("GERRIT_HTTP_PASSWORD", "env-token")
    rec = {}
    f = GerritFetcher(transport=httpx.MockTransport(_gerrit_handler(rec)))  # 不显式传凭据
    _run(f.fetch("https://gerrit.example.com/c/proj/+/42"))
    assert f.authed is True
    assert "/a/changes/" in rec["url"] and rec["auth"].startswith("Basic ")


# ── URL 分流(Gap B)────────────────────────────────────────────────────────────


def test_fetcher_for_url_dispatches():
    """fetcher_for_url:Gerrit change URL → GerritFetcher;GitHub PR URL → GitHubFetcher。"""
    from rootrecall.services.patch.fetcher import GerritFetcher, GitHubFetcher, fetcher_for_url

    assert isinstance(fetcher_for_url("https://gerrit.example.com/c/proj/+/42"), GerritFetcher)
    assert isinstance(fetcher_for_url("https://github.com/o/r/pull/1"), GitHubFetcher)
    # Gerrit project 路径可含斜杠
    assert isinstance(fetcher_for_url("https://gerrit.example.com/c/a/b/c/+/99"), GerritFetcher)
