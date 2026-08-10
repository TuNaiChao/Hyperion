# 工作流 · 批量 PR 聚合报告(patch_report)

> `workflows/patch_report/` —— **P-A 补丁 / PR 分析**。一组 PR → fetch → 逐 PR 分析 → 跨 PR 聚合 → cited 报告 + 入记忆。
> CLI:`hyperion patch-report --prs <urls...> --repo <path> --codebase <name>`。

## 概览

P-A 是 bug-RCA 的反向:bug-RCA 是"从 bug 找补丁",P-A 是"给一组补丁 / PR,验它们对不对、该不该合、跨 PR 有什么共性"。一条 LangGraph 六节点流水线:抓各 PR diff → 逐 PR(`validate_patch` + CRG 影响面 risk_score + cited 摘要)→ 跨 PR 分桶聚合(模块 / 安全 / 功能)→ cited 报告(Verifier 零幻觉回查)→ 入记忆。

## 源码

| 文件 | 职责 |
|---|---|
| `patch_report/state.py` | `PatchReportState` + `PRFinding` TypedDict |
| `patch_report/graph.py` | `build_graph()`(六节点)+ `async run(prs, *, repo, codebase, owner="default", deep=False, concurrency=3) -> dict` |
| `patch_report/nodes.py` | 六节点函数 |
| `patch_report/_analyze.py` | ★`_analyze_one_pr` + `_security_tier` + `_cited_summarize` |
| `patch_report/_aggregate.py` | `aggregate(findings, codebase="") -> dict` —— 确定性分桶 + 一次 LLM cited 综合 |
| `patch_report/report.py` | `render_patch_report(state)` + `verify_and_append(report_md, state)` |

## API

```python
def build_graph() -> CompiledStateGraph
async def run(prs: list[str], *, repo: str, codebase: str,
              owner: str = "default", deep: bool = False, concurrency: int = 3) -> dict
    # 返回 {report_path, aggregate: {stats: {total_prs, high_security_count, ...}}, facts_memorized, ...}
```

## 流程(六节点)

```
ingest ──▶ fetch_prs ──▶ analyze ──▶ aggregate ──▶ report ──▶ memorize
```

1. **ingest**:登记 PR 列表 + scope。
2. **fetch_prs**:`PatchFetcher` 抓各 PR 的 diff + meta(并发 `concurrency`)。
3. **analyze** ★:`_analyze_one_pr(art, *, repo_root, codebase, deep=False)`:
   - `validate_patch`(Tier 0,验 apply);
   - `CodeGraph.analyze_changes`(六因子 `risk_score`)+ `community_ids_for`;
   - `_security_tier(changed_funcs, risk_score)` → `none` / `relevant` / `high`(keyword 预筛 + 子集 LLM);
   - `_cited_summarize`(cited-reporter,引用真实符号)。
4. **aggregate**:`aggregate` —— 确定性分桶统计(模块 / 安全 / 功能)+ 一次 LLM cited 综合。
5. **report**:`render_patch_report` + `verify_and_append`(零幻觉回查)。
6. **memorize**:`codebase_fact` 入记忆。

## 配置 / 前置

- 需先 `hyperion index <path> <name>`(CRG 图给 risk_score 用)。
- `GITHUB_TOKEN` 提速提额(匿名限速严重)。
- `--deep`:高风险 / security 子集走 ReAct 深审(默认 light)。

## 边界与限制

- **验证封顶 = apply**:不编译 / 不测试 / 不复现;`risk_score` 是 CRG 图启发式,非语义正确性。详见 [overview.md](../overview.md) §验证封顶。
- GitHub 匿名限速 → 建议配 `GITHUB_TOKEN`;`concurrency` 默认 3(限速友好)。
- CRG 是可选 extra;不装则 `analyze_changes` 不可用(risk_score 降级)。
- Gerrit PR 同接口(走 `GerritFetcher`)。

## 示例

```bash
uv run hyperion patch-report \
  --prs https://github.com/torvalds/linux/pull/123 https://github.com/torvalds/linux/pull/456 \
  --repo ~/src/linux --codebase linux --deep
# 报告:data/.../<repo>-patch-report.md
# PRs:2 · high_security=N · CodebaseFact 入记忆:M 条
```

## See Also

- [../cli-reference.md](../cli-reference.md) §`hyperion patch-report`
- [../guides/run-patch-report.md](../guides/run-patch-report.md) — 跑通步骤
- [../services/patch-fetcher.md](../services/patch-fetcher.md) / [../services/workspace.md](../services/workspace.md)(validate_patch)
- [../tools/mcp-tools.md](../tools/mcp-tools.md) — `fetch_patch` / `validate_patch` / `ensure_repo`
- 上级 [../../设计/harness-v2/03-patch-analysis.md](../../设计/harness-v2/03-patch-analysis.md)
