# 指南 · 跑通批量 PR 聚合报告

> `hyperion patch-report` —— 给一组 GitHub / Gerrit PR,自动抓 diff → 逐 PR 分析(apply 验证 + CRG 影响面 + 安全分层)→ 跨 PR 聚合 → cited 报告 + 入记忆。
> 对标 P-A「补丁 / PR 分析」。适合批量审 PR、做季度安全复盘、跨 PR 找共性根因。

## 它做什么

一条 LangGraph 流水线:

```
ingest → fetch_prs → analyze → aggregate → report → memorize
```

1. **ingest**:登记 PR 列表 + scope。
2. **fetch_prs**:`PatchFetcher` 并发抓每个 PR 的 diff + meta(`--concurrency` 控并发)。
3. **analyze** ★:每个 PR —— `validate_patch`(Tier 0,验 apply)+ `CodeGraph.analyze_changes`(六因子 `risk_score`)+ 安全分层(`none`/`relevant`/`high`,keyword 预筛 + 子集 LLM)+ cited 摘要。
4. **aggregate**:跨 PR 确定性分桶(模块 / 安全 / 功能)+ 一次 LLM cited 综合。
5. **report**:cited 报告 + Verifier 零幻觉回查。
6. **memorize**:`codebase_fact` 入记忆。

> [!IMPORTANT]
> 和 research 不同,patch-report **要求你先手动建索引**:`hyperion index <repo> <name>`。它直接复用已有 CRG 图做 `risk_score`;没建图 → analyze 步 risk_score 降级。

## 命令

```bash
hyperion patch-report --prs <url...> --repo <仓库路径> --codebase <名字> \
                     [--owner <owner>] [--deep] [--concurrency N]
```

| 参数 | 必填 | 说明 |
|---|---|---|
| `--prs` | ✅ | 一个或多个 PR URL(GitHub / Gerrit;空格分隔) |
| `--repo` | ✅ | 代码仓根(CRG 图 + validate_patch 用;**需先 `hyperion index`**) |
| `--codebase` | ✅ | 仓库名(CRG db + 记忆 `scope.codebase`) |
| `--owner` | ❌ | 记忆 `scope.owner`(默认 `default`) |
| `--deep` | ❌ | 高风险 / security 子集走 ReAct 深审(默认 light) |
| `--concurrency` | ❌ | 并发抓取 / 分析(默认 3,GitHub 限速友好) |

## 示例

```bash
# 0. 先建索引(patch-report 不自动建,research 才自动建)
uv run hyperion index ~/src/linux linux

# 1. 跑批量分析
uv run hyperion patch-report \
  --prs https://github.com/torvalds/linux/pull/123 \
       https://github.com/torvalds/linux/pull/456 \
  --repo ~/src/linux --codebase linux --deep
```

跑完:

```
报告:data/.../linux-patch-report.md
PRs:2 · high_security=N · CodebaseFact 入记忆:M 条
```

## 前置

- 配好 `.env`(模型 key)。
- **先 `hyperion index <repo> <name>`**(CRG 图给 risk_score 用)。
- `GITHUB_TOKEN`:强烈建议配(匿名限速严重,几十 PR 容易被掐);`.env` 里加 `GITHUB_TOKEN=ghp_...`。
- CRG 可选 extra(`uv sync --extra graph`);不装则 `analyze_changes` 不可用(risk_score 降级)。

## 产物

| 文件 | 路径 |
|---|---|
| 聚合报告 | `data/.../<codebase>-patch-report.md`(cited + 跨 PR 分桶) |
| 记忆 | 记忆库(`kind=codebase_fact`,带 PR 溯源) |

## 边界与限制

- **验证封顶 = apply**:不编译 / 不测试 / 不复现;`risk_score` 是 CRG 图启发式,**非**语义正确性。详见 [../overview.md](../overview.md) §验证封顶。
- GitHub 匿名限速 → 配 `GITHUB_TOKEN`;`concurrency` 默认 3。
- Gerrit PR 同接口(自动走 `GerritFetcher`,剥 XSSI + base64)。

## 排查

| 现象 | 解法 |
|---|---|
| risk_score 全 0 / 缺失 | 没先建索引;先 `hyperion index <repo> <name>` |
| fetch_prs 大量 403 / 限速 | 没配 `GITHUB_TOKEN`;配上,或调小 `--concurrency` |
| analyze 超时 | PR 太多 + `--deep`;去掉 `--deep` 先 light,或分批跑 |
| 报告引用对不上符号 | Verifier 拦了幻觉项,正常;查 `[cited: …]` |

## See Also

- [../workflows/patch-report.md](../workflows/patch-report.md) — 流水线节点 + API
- [../cli-reference.md](../cli-reference.md) §`hyperion patch-report`
- [../services/patch-fetcher.md](../services/patch-fetcher.md) — GitHub / Gerrit 抓取
- [../services/workspace.md](../services/workspace.md) — validate_patch(Tier 0)
- [../guides/run-research.md](run-research.md) — research 自动建索引(对比本指南需手动)
