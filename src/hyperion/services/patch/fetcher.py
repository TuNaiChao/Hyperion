"""patch fetcher —— P-A 的「取快递」(1a):URL(PR 链接)→ unified diff + 元信息。

面向小白:你给一个 GitHub PR 链接,这里帮你把它的 diff(改了啥)和元信息(标题 / 说明 /
  改了哪些文件 / 合入 commit)抓回来。opencode 自己也能 curl `.diff`,但这里带 token 鉴权
  (私有仓 / 避免限速)、失败重试、结构化拆包;且留了 GerritFetcher 接口(1d 再实现)。

依据(已 WebSearch 核实):GitHub REST ——
  GET /repos/{owner}/{repo}/pulls/{num},用 Accept: application/vnd.github.v3.diff 拿 diff 体,
  同 URL 默认 json 拿 meta;GET .../pulls/{num}/files 拿改动文件列表。

为什么用 httpx(不 vendor deer-flow github_api.py):deer-flow 那份是 sync `requests`;Hyperion
  全栈异步且 httpx 已是核心依赖(pyproject),写薄异步 fetcher 更一致、不引新依赖。
"""

from __future__ import annotations

import abc
import asyncio
import base64
import json
import os
import re
from dataclasses import dataclass, field

import httpx


@dataclass
class PatchArtifact:
    """一个补丁/PR 的完整抓取产物(给 build_check / memorize / 鉴定卡 用)。"""

    url: str
    source_kind: str  # "github" | "gerrit" | "local"
    diff: str  # unified diff 全文(喂 validate_patch / build_check)
    title: str = ""
    body: str = ""
    merge_commit_sha: str | None = None
    changed_files: list[str] = field(default_factory=list)


class PatchFetcher(abc.ABC):
    """补丁抓取器抽象(P-A 1a)。子类:GitHubFetcher(1a)/ GerritFetcher(1d stub)。"""

    @abc.abstractmethod
    async def fetch(self, url: str) -> PatchArtifact:
        """从 URL 抓 diff + 元信息。抓不到(网络 / 404 / 未实现)抛异常,调用方降级成 str。"""


# GitHub PR URL 形态:github.com/{owner}/{repo}/pull/{num}。用 search 不要求严格匹配整串。
_GH_PR_RE = re.compile(r"github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<num>\d+)")


class GitHubFetcher(PatchFetcher):
    """GitHub PR 抓取器(httpx 异步)。token 可选 —— 私有仓 / 避免匿名限速;默认从 GITHUB_TOKEN env 读。

    transport:测试注入 httpx.MockTransport 用(线上不传 → 真网络)。
    """

    def __init__(
        self,
        token: str | None = None,
        *,
        timeout: float = 30.0,
        retries: int = 3,
        transport: httpx.BaseTransport | None = None,
    ):
        self.token = token if token is not None else os.environ.get("GITHUB_TOKEN")
        self.timeout = timeout
        self.retries = retries
        self.transport = transport

    def _headers(self, media: str = "json") -> dict[str, str]:
        # media:"json" 取 meta,"diff" 取 diff 体。token 有就带 Authorization。
        h = {"Accept": f"application/vnd.github.v3.{media}"}
        if self.token:
            h["Authorization"] = f"token {self.token}"
        return h

    async def fetch(self, url: str) -> PatchArtifact:
        """GitHub PR URL → PatchArtifact(diff + title/body + merge_commit_sha + changed_files)。"""
        m = _GH_PR_RE.search(url)
        if not m:
            raise ValueError(f"不是 GitHub PR URL(期望 github.com/<owner>/<repo>/pull/<num>): {url}")
        base = f"https://api.github.com/repos/{m.group('owner')}/{m.group('repo')}/pulls/{m.group('num')}"
        client_kwargs: dict = {"timeout": self.timeout}
        if self.transport is not None:
            client_kwargs["transport"] = self.transport
        async with httpx.AsyncClient(**client_kwargs) as client:
            # meta(json):title / body / merge_commit_sha。
            meta = (await self._req(client, "GET", base, headers=self._headers("json"))).json()
            # diff(diff media):unified diff 全文。
            diff = (await self._req(client, "GET", base, headers=self._headers("diff"))).text
            # changed_files:/files 列表(单页 100;PR 文件数 >100 属罕见,best-effort 不分页)。
            files_resp = await self._req(
                client, "GET", f"{base}/files",
                headers=self._headers("json"), params={"per_page": 100})
            files_data = files_resp.json() if files_resp.status_code == 200 else []
        changed = [f["filename"] for f in files_data if isinstance(f, dict) and "filename" in f]
        return PatchArtifact(
            url=url, source_kind="github", diff=diff,
            title=str(meta.get("title", "")),
            body=str(meta.get("body", "") or ""),
            merge_commit_sha=meta.get("merge_commit_sha"),
            changed_files=changed,
        )

    async def _req(
        self, client: httpx.AsyncClient, method: str, url: str, *,
        headers: dict | None = None, **kwargs,
    ) -> httpx.Response:
        """带指数退避重试 —— 只重瞬时(5xx / 429 / 网络 / 超时);4xx 立即抛(调方降级)。

        借 deer-flow github_api 的重试思路:别为限速/抖动整个失败,但 404/401 这种不是重试能解决的。
        """
        delay = 1.0
        for attempt in range(max(1, self.retries)):
            try:
                r = await client.request(method, url, headers=headers, **kwargs)
            except (httpx.TransportError, httpx.TimeoutException):
                if attempt + 1 < self.retries:
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                raise
            # 瞬时错(限速 / 5xx)且还有重试名额 → 退避重来。
            if r.status_code in (429, 500, 502, 503, 504) and attempt + 1 < self.retries:
                await asyncio.sleep(delay)
                delay *= 2
                continue
            r.raise_for_status()  # 4xx / 用尽重试的 5xx → 抛,让调用方友好降级。
            return r
        raise RuntimeError("_req: unreachable(重试逻辑应已在上面 return/raise)")  # 保险


def _diff_changed_files(diff_text: str) -> list[str]:
    """从 unified diff 抽改动文件(取每 hunk 的 ``+++ b/<path>``,去重保序)。

    Gerrit 没有 GitHub 那种 per-file 列表端点(或要额外 ``o=`` 参数),直接从 diff 文本抽更稳。
    """
    seen: list[str] = []
    for line in (diff_text or "").splitlines():
        if line.startswith("+++ "):
            rest = line[4:].strip().split("\t")[0]
            if rest.startswith("b/"):
                rest = rest[2:]
            if rest and rest != "/dev/null" and rest not in seen:
                seen.append(rest)
    return seen


class GerritFetcher(PatchFetcher):
    """Gerrit change 抓取器(P-A 1d)。

    Gerrit REST(Googlesource 风格):
      - URL 形态:`https://<host>/c/<project>/+/<change_number>`(project 可含斜杠)。
      - meta:`GET /changes/?q=change:<n>&o=CURRENT_REVISION` → JSON 列表。⚠️ Gerrit 在所有 JSON 响应前
        加魔法前缀 ``)]}'`` 防 XSSI,必须先剥掉再解析。取 ``subject``(title)+ current revision sha + ``id``
        (``project~branch~number``,后续 patch 端点要用)。
      - diff:`GET /changes/<id>/revisions/current/patch` → **base64 编码**的 unified diff(无前缀,直接解码)。
      - changed_files:从 diff 文本抽(``_diff_changed_files``),省一个端点 + 不依赖 ``o=`` 参数形态。
    匿名读公开 change 可行;私有 / 限速需 Gerrit HTTP 凭据(用户名 + HTTP password),token 接入留 backlog。
    """

    _GERRIT_RE = re.compile(r"https?://(?P<host>[^/]+)/c/(?P<proj>.+?)/\+/(?P<num>\d+)")

    def __init__(self, *, timeout: float = 30.0, retries: int = 3,
                 transport: httpx.BaseTransport | None = None):
        self.timeout = timeout
        self.retries = retries
        self.transport = transport

    @staticmethod
    def _strip_xssi(text: str) -> str:
        """剥 Gerrit JSON 响应的 ``)]}'`` 防 XSSI 前缀(后跟一个换行)。无前缀则原样返回。"""
        s = text.lstrip()
        if s.startswith(")]}'"):
            return s[4:].lstrip("\n")
        return text

    async def fetch(self, url: str) -> PatchArtifact:
        m = self._GERRIT_RE.search(url)
        if not m:
            raise ValueError(f"不是 Gerrit change URL(期望 <host>/c/<proj>/+/<num>): {url}")
        host, num = m.group("host"), m.group("num")
        base = f"https://{host}"
        kw: dict = {"timeout": self.timeout}
        if self.transport is not None:
            kw["transport"] = self.transport
        async with httpx.AsyncClient(**kw) as client:
            # meta:subject + current revision sha + change id(project~branch~number)。
            meta_resp = await self._req(
                client, "GET", f"{base}/changes/",
                params={"q": f"change:{num}", "o": "CURRENT_REVISION"})
            data = json.loads(self._strip_xssi(meta_resp.text))
            if not data:
                raise ValueError(f"Gerrit change {num} 未找到(404 / 无权限?)")
            ch = data[0]
            title = str(ch.get("subject", ""))
            revs = ch.get("revisions") or {}
            sha = next(iter(revs)) if revs else None
            change_id = ch.get("id")
            # diff:revisions/current/patch(base64 编码的 unified diff)。
            patch_resp = await self._req(
                client, "GET", f"{base}/changes/{change_id}/revisions/current/patch")
            diff = base64.b64decode(patch_resp.text).decode("utf-8", errors="replace")
        return PatchArtifact(
            url=url, source_kind="gerrit", diff=diff, title=title,
            merge_commit_sha=sha, changed_files=_diff_changed_files(diff),
        )

    async def _req(self, client: httpx.AsyncClient, method: str, url: str, **kwargs) -> httpx.Response:
        """指数退避重试(只重瞬时:5xx / 429 / 网络 / 超时);4xx 立即抛。镜像 GitHubFetcher._req。"""
        delay = 1.0
        for attempt in range(max(1, self.retries)):
            try:
                r = await client.request(method, url, **kwargs)
            except (httpx.TransportError, httpx.TimeoutException):
                if attempt + 1 < self.retries:
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                raise
            if r.status_code in (429, 500, 502, 503, 504) and attempt + 1 < self.retries:
                await asyncio.sleep(delay)
                delay *= 2
                continue
            r.raise_for_status()
            return r
        raise RuntimeError("GerritFetcher._req: unreachable(重试逻辑应已在上面 return/raise)")  # 保险


def from_config(cfg=None) -> PatchFetcher:  # noqa: ARG001 (cfg 预留给将来按 backend 选)
    """按 config 选 fetcher(仿 delegate.from_config)。v1 只有 GitHubFetcher。"""
    return GitHubFetcher()
