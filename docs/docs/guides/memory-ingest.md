# 指南 · 把报告 / 补丁摄取进记忆

> `hyperion memory ingest` 把外部文档(bug 报告 / 调研报告 / 补丁)读进长期记忆,供后续 `memory_recall` 召回复用。
> 对标 P3「持续学习」:把一次性分析沉淀成可检索、带溯源的知识。

## 何时用

- 拿到一份外部 bug 报告 / CVE 描述 / 调研文档 → 摄取成 `codebase_fact` / `bug_lesson`。
- 拿到一个补丁 / PR diff(不在 Hyperion workflow 里跑的)→ 摄取成 `bug_lesson`(根因 + 修法 + 影响面)。
- workflow(deep_research / bug_rca / patch_report)收尾会自动 memorize,**不需要**手动 ingest;本指南针对**外部**文档。

## 命令

```bash
hyperion memory ingest <path> [--kind auto|report|patch]
                      [--source-tier imported|stated|inferred]
                      [--commit-sha SHA] [--repo X]
```

按扩展名自动分流(`--kind auto`,默认):

| 输入 | 路径 | 处理 |
|---|---|---|
| `.md` / `.txt` / `.pdf` | 报告路 | `parse_issue` → `LongDocChunker` 切块 → LLM `extract_items` 抽知识项 → `memorize` |
| `.patch` / `.diff` | 补丁路 | `PatchIngestPipeline`:解析 hunk → `code_index.retrieve` 取上下文 → LLM 抽 `root_cause` → 组装 `bug_lesson` |

## 示例

```bash
# 摄取一份 bug 报告(报告路)
uv run hyperion memory ingest ~/reports/wpa-bug.md --repo wpa --source-tier stated

# 摄取一个补丁(补丁路;id 按 diff 内容算,同补丁重复摄取会合并)
uv run hyperion memory ingest ~/fix.patch --repo wpa --commit-sha abc123

# 指定走报告路 / 补丁路(覆盖 auto 判定)
uv run hyperion memory ingest ~/notes.txt --kind report --repo wpa
```

输出:

```
报告摄取:wpa-bug.md → 3 块 → 写入 5 条(scope=wpa)。
补丁摄取:fix.patch → 产 1 条 → 写入 1 条(scope=wpa)。
```

## 参数

| 参数 | 说明 |
|---|---|
| `path` | 文档路径(`.md`/`.txt`/`.pdf`/`.patch`/`.diff`) |
| `--kind` | `auto`(默认,按扩展名)\| `report` \| `patch` |
| `--source-tier` | 来源可信度:`imported`(默认)\| `stated`(人 / 报告明确陈述,最可信)\| `inferred`(LLM 推断) |
| `--commit-sha` | 溯源 commit(补丁路 / 报告路都建议给) |
| `--repo` | 代码库(默认 `config.code_index.repo`) |

## 去重与合并

- 补丁路:id 按 **diff 内容**算(非 LLM summary),同一补丁重复摄取走**合并(置信度累加)**,不重复入库。
- 报告路:抽取的 knowledge item 按 `make_id(scope, kind, summary)` 稳定 id 去重,重提走 Bayes 合并。
- 详见 [../services/memory.md](../services/memory.md) §ingest。

## 边界与限制

- 报告路需 LLM(经 `model_roles.memory_extractor` 路由)。
- 补丁路会 `code_index.retrieve` 取上下文 —— 需先建索引,否则上下文稀疏(仍能跑,根因抽取质量下降)。
- 扫描件 PDF / 图片型文档返空文本(不做 OCR)。
- `source_tier` 影响合并权重:`stated` > `inferred` > `imported`。

## See Also

- [../services/memory.md](../services/memory.md) §ingest — `PatchIngestPipeline` / `LongDocChunker` 细节
- [../cli-reference.md](../cli-reference.md) §`hyperion memory`
- [../services/trigger-parser.md](../services/trigger-parser.md) — `parse_issue`
