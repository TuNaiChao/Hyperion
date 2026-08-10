# MCP 工具参考

> `tools/mcp_memory.py` —— Hyperion 把差异化能力做成 **9 个 MCP 工具**,给 coding agent(opencode 主 / codex / claude code)调。
> 入口:`hyperion mcp serve [--codebase NAME]`(需 `uv sync --extra mcp`)。server 名 `hyperion`,opencode 按 `hyperion_<tool>` 给工具加前缀。

## 概览

工具分两类:

- **差异化核心**(coding agent 做不好 / 做不了的):`memory_recall` / `memory_memorize` / `search_codebase` / `blast_radius` / `fetch_patch`
- **确定性硬门**(交付 / 验证,零 LLM):`validate_patch` / `export_patch` / `export_report` / `ensure_repo`

> [!NOTE]
> 下列工具**均已撤**(2026-08-10 工具审核):`filter_logs`(opencode 的 read/grep/awk 等价)、`build_check`(构建信号歧义 + opencode 能 make)、`patch_search`(被 `memory_recall(kind=...)` 吸收)、`code_nav` @tool 层(能力经这些 MCP 工具暴露)。本表只列现存 9 个。

## 工具一览

| 工具 | 类别 | 一句话 |
|---|---|---|
| [`memory_recall`](#memory_recall) | 核心 | 翻长期记忆(历史 bug 教训 / codebase fact),带 file:line 溯源 |
| [`memory_memorize`](#memory_memorize) | 核心 | 写一条记忆(ad-hoc;报告/补丁走 workflow 自动记) |
| [`search_codebase`](#search_codebase) | 核心 | 语义+符号检索,**只回索引里真实存在的符号**(防幻觉) |
| [`blast_radius`](#blast_radius) | 核心 | 改动影响面(结构图 BFS) |
| [`fetch_patch`](#fetch_patch) | 核心 | GitHub PR URL → diff + meta |
| [`validate_patch`](#validate_patch) | 硬门 | 补丁能否干净 apply(零 LLM) |
| [`export_patch`](#export_patch) | 硬门 | 补丁落盘 `.patch`(空 diff 自检拒写) |
| [`export_report`](#export_report) | 硬门 | 报告落盘 `.md`(空内容拒写) |
| [`ensure_repo`](#ensure_repo) | 硬门 | 本地没有 → auto-clone |

## codebase 解析

所有工具查的代码库由 `_resolve_codebase` 定:**`--codebase` 参数 > `HYPERION_CODEBASE` 环境变量 > `config.code_index.repo` > 进程 cwd 目录名**。`HYPERION_CODEBASE` 由 delegate(opencode 父进程)注入、经进程 env 继承透传(local server 的 `environment` 字段不展开 `{env:}`)。

---

## memory_recall

翻长期记忆:历史 bug 教训 / codebase facts,**定位 / 改补丁前先调**,复用同库的历史根因 / 修法。

```python
async def memory_recall(query: str, top_k: int = 5, kind: str | None = None) -> str
```

| 参数 | 类型 | 说明 |
|---|---|---|
| `query` | `str` | 自然语言查询(概念,不是猜的符号名) |
| `top_k` | `int` | 返回条数(默认 5) |
| `kind` | `str?` | 过滤:`"bug_lesson"` 只回历史补丁/修法(排除 codebase fact + 裸代码);省略 = 全部。给 `kind` 时会多取再过滤(`max(top_k*3, top_k)`),不会饿死结果 |

**输出**:每条一行,带 file:line 溯源 + 置信度 + 日期(对标 mem0 v3 时序)。无结果返回提示串。详见 [memory recall](../services/memory.md#recallpy四路融合)。

## memory_memorize

写一条记忆(知识项)。报告 / 补丁走 workflow 会自动记;这是 ad-hoc 入口(delegate 现场发现的事实 / 教训)。

```python
async def memory_memorize(
    kind: Literal["codebase_fact", "bug_lesson"], summary: str,
    file: str | None = None, line: int | None = None,
    root_cause: str = "", fix_patch: str = "", symptom: str = "",
    blast_radius_files: list[str] | None = None,
    commit_sha: str | None = None, tags: list[str] | None = None,
) -> str
```

> [!NOTE]
> 传了 `fix_patch`(unified diff)→ id **按补丁内容**算(`make_id(scope, kind, fix_patch)`),同一补丁重复 memorize 会**合并(置信度累加)**而非重复入库。配 `blast_radius_files` + `commit_sha` + `tags`(如 `["patch_insight"]`)让教训可检索、可溯源。鉴定结论(intent / 正确性 / 合入建议)放 `summary` + `root_cause`。

**输出**:`memorized id=<id> kind=<kind> (<n> merged/added)`。source_tier 固定 `delegate`(最可信)。

## search_codebase

语义 + 符号检索(BM25 + 向量 + RRF + rerank)。**传概念 / 自然语言**(如 `"p2p scan result routing"`),不是猜的文件名 / 函数名。

```python
async def search_codebase(query: str, top_k: int = 5) -> str
```

**防幻觉契约**:结果**只来自真实索引** —— 每条带 `file:start-end (kind symbol) score` + 首行,模型拿不到编造的路径。比手 grep 全树更准更省。

**前置**:需先 `uv run hyperion index <path> <name>`;表空返回"未建索引"提示。
**输出**:检索路径(`hybrid+rerank` / `hybrid` / `rerank-failed:hybrid` / `empty`)+ top-k 真实符号。

## blast_radius

改动影响面(结构图 BFS):给一组被改文件,返回还会波及谁(callers / callees / dependents)—— "动了这些会断哪"。图驱动,零 LLM。

```python
async def blast_radius(changed_files: list[str], codebase: str | None = None) -> str
```

| 参数 | 类型 | 说明 |
|---|---|---|
| `changed_files` | `list[str]` | 被 patch / PR 改动的文件路径 |
| `codebase` | `str?` | 覆盖查哪个库的图(默认 = server 的 codebase) |

**前置**:需 `uv sync --extra code-review-graph` + 已建结构图(`data/structgraph/<repo>/graph.db`);否则返回可操作提示(未装 / 未建)。

## fetch_patch

给一个 GitHub PR URL,抓回 diff + meta(title / body / changed_files / merge_commit_sha)。

```python
async def fetch_patch(url: str) -> str
```

带 `GITHUB_TOKEN`(若设)做鉴权(私有仓 / 提速提额);网络错 / 404 / 非 GitHub URL 返回友好错误串。详见 [patch-fetcher.md](../services/patch-fetcher.md)。

---

## validate_patch(执行硬门)

补丁能否干净 apply —— 确定性硬门(零 LLM),贴上才信。

```python
async def validate_patch(patch: str, repo_path: str) -> str
```

| 参数 | 类型 | 说明 |
|---|---|---|
| `patch` | `str` | unified diff 文本 |
| `repo_path` | `str` | 目标 repo 工作树的**绝对路径** |

**流程**:正向 `git apply --check`(strict → `--3way` → `patch -p1` 降级)。本工具**只正向**;反向回退验证需已贴过的树(bug_rca workflow 里有完整 forward+reverse)。
**输出**:`✅ 能干净 apply` / `❌ apply 失败` + `method` + git 诊断。

> [!NOTE]
> 对 agent 传参做了末尾换行归一化,避免 agent `rstrip` 掉末尾换行导致 `git apply` 误判"补丁损坏"。

## export_patch(交付硬门)

把你的改动落盘成 `.patch` —— **bug-RCA 没把补丁写盘就不算完**(聊天回复不是交付)。

```python
async def export_patch(repo_path: str, out_dir: str = "data/bug_rca") -> str
```

**流程**:`git add -A && git diff --cached`(含新增文件)→ 写 `<out_dir>/<repo-name>.patch`。**空 diff 拒写**(治"改错树 / 没保存 / 被 gitignore")。
**输出**:`✅ 已落盘` + `path` + 行数;空 diff 返回 `❌ 空 diff` 诊断。
> apply 验证**不在这做**(对"已改过的树"正向 `--check` 必失败);先调 `validate_patch`(对干净树)。

## export_report(交付硬门)

把分析报告落盘成 `.md` —— 跟补丁一样的交付标准。

```python
async def export_report(content: str, repo_path: str, out_dir: str = "data/bug_rca") -> str
```

| 参数 | 类型 | 说明 |
|---|---|---|
| `content` | `str` | 完整 markdown 报告(根因 + 证据 + 补丁要点 + validate 结果 + patch 路径 + memorize id) |
| `repo_path` | `str` | repo 绝对路径(仅用于取文件名) |
| `out_dir` | `str` | 输出目录(默认 `data/bug_rca`) |

**流程**:写 `<out_dir>/<repo-name>-rca.md`,**空 / 空白内容拒写**。建议顺序:`export_patch` → `memory_memorize` → `export_report`(报告可引用前两步的路径与 id)。

## ensure_repo

把代码库解析到本地路径,缺则 auto-clone。

```python
async def ensure_repo(name_or_url: str) -> str
```

给仓库名(查 `config.patch.git.remotes`)、git URL、或已有本地路径。返回本地绝对路径;`data/repos/<name>` 已有则复用(幂等,不重复 clone)。`validate_patch` 前仓不在本地时先调它。详见 [repos.md](../services/repos.md)。

---

## transport

| transport | 启动 | 适用 |
|---|---|---|
| `stdio`(默认) | `hyperion mcp serve --codebase X` | 本地单机、opencode local server、最简 |
| `http`(streamable-http) | `hyperion mcp serve --transport http --codebase X` | warm 长进程,多 agent 共用,省每 bug 重启加载 ~1.2GB(sentence-transformers)的冷启动 |

http 模式端点 `http://<host>:<port>/mcp`。详见 [configuration.md](../configuration.md) §mcp。

## 边界与限制

- **工具返回串,不抛异常**:几乎所有工具把失败(未索引 / 未装 / 网络错)转成可操作的中文错误串,不崩调用方 agent。
- `search_codebase` / `blast_radius` 需先建索引 / 结构图;否则返回提示。
- `validate_patch` 只验 apply(Tier 0),**不**保证补丁语义对 —— 语义靠读码推理 + 用户真机。
- `export_patch` 会 `git add -A`(stage 改动,可 `git reset` 撤)。
- opencode 1.18.x 的 http(streamable-http)MCP **不注册原生工具**(agent 绕 curl),故 opencode 接 Hyperion **用 local stdio**(timeout ≥ 120000ms 防首次冷启)。

## See Also

- [../cli-reference.md](../cli-reference.md) §`hyperion mcp serve`
- [../guides/bug-rca-opencode.md](../guides/bug-rca-opencode.md) — opencode 接线 + 工具在主路径里的用法
- [../services/memory.md](../services/memory.md) / [../services/code-index.md](../services/code-index.md) / [../services/workspace.md](../services/workspace.md) — 各工具背后的服务
- [../configuration.md](../configuration.md) §mcp
