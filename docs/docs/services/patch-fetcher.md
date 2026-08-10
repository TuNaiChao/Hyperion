# 服务 · 补丁抓取(patch fetcher)

> `services/patch/fetcher.py` —— 把一个 URL(PR / Gerrit change)抓成 unified diff + meta。给 `fetch_patch` MCP 工具、patch_report workflow 用。

## 概览

`PatchFetcher` 抽象类定义「URL → `PatchArtifact`」的契约;v1 有两个实现:`GitHubFetcher`(GitHub PR)和 `GerritFetcher`(Gerrit change,剥 XSSI 前缀 + base64 解 diff)。都走 httpx 异步,带指数退避重试(只重瞬时错),`RateLimitError` 区分配额耗尽。

## 源码

| 文件 | 职责 |
|---|---|
| `services/patch/fetcher.py` | `PatchFetcher` ABC + `GitHubFetcher` / `GerritFetcher` + `PatchArtifact` + `from_config` |

## API

```python
@dataclass
class PatchArtifact:
    url: str
    source_kind: str          # "github" | "gerrit"
    diff: str                 # unified diff 文本
    title: str
    body: str
    merge_commit_sha: str | None
    changed_files: list[str]

class PatchFetcher(abc.ABC):
    async def fetch(self, url: str) -> PatchArtifact: ...

class GitHubFetcher(token=None, *, timeout=30.0, retries=3, transport=None):
    # GitHub PR;指数退避重试(瞬时错);RateLimitError 区分 403 + X-RateLimit-Remaining: 0

class GerritFetcher(*, timeout=30.0, retries=3, transport=None):
    # Gerrit change;剥 XSSI 前缀 + base64 解 diff

def from_config(cfg=None) -> PatchFetcher   # v1 返 GitHubFetcher
```

## 流程

1. 解析 URL → 判定 source_kind(GitHub / Gerrit)。
2. 异步拉取(GitHub 走 PR diff API;Gerrit 走 change detail + 解 XSSI/base64)。
3. 瞬时网络错(connection reset)指数退避重试,最多 `retries` 次。
4. 配额耗尽(GitHub 403 + `X-RateLimit-Remaining: 0`)→ 抛 `RateLimitError`,不重试。

## 配置

无独立配置段;`token` 从环境变量(GitHub 建议 `GITHUB_TOKEN` 提速提额)。`from_config(cfg)` v1 固定返 `GitHubFetcher`。

## 边界与限制

- **GitHub 匿名限速严重**:建议配 `GITHUB_TOKEN`。
- v1 `from_config` 不按 URL 自动选 fetcher —— 调用方需自己选(GitHub / Gerrit)。patch_report workflow 内部按 URL 域名分发。
- 私有仓 / 需鉴权的 PR 需要 token。

## 示例

```python
import asyncio
from hyperion.services.patch.fetcher import GitHubFetcher

async def main():
    f = GitHubFetcher(token="<GITHUB_TOKEN>")
    art = await f.fetch("https://github.com/torvalds/linux/pull/123")
    print(art.changed_files, len(art.diff))

asyncio.run(main())
```

## See Also

- [../tools/mcp-tools.md](../tools/mcp-tools.md) — `fetch_patch`
- [../workflows/patch-report.md](../workflows/patch-report.md) — 批量抓 PR
- [repos.md](repos.md) — 抓到 diff 后可能还要 clone 仓
