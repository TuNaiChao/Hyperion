# CLI 参考

> 入口:`uv run hyperion <subcommand>`(pyproject 脚本 `hyperion = "hyperion.cli:main"`)。
> 启动时先 `load_dotenv()` 把 `.env` 读进环境变量,再解析 `config.yaml` 里的 `$VAR`。

## 子命令一览

| 命令 | 作用 |
|---|---|
| [`hyperion models`](#hyperion-models) | 列配置的模型 + role 路由 |
| [`hyperion index`](#hyperion-index) | 为仓库建 / 更新向量索引 |
| [`hyperion lsp`](#hyperion-lsp) | L2 精确导航(clangd)自检 / 冒烟 |
| [`hyperion memory`](#hyperion-memory) | 记忆核心:recall / add / ingest / list / consolidate / invalidate |
| [`hyperion mcp`](#hyperion-mcp-serve) | 启动 MCP server |
| [`hyperion bug-rca`](#hyperion-bug-rca) | ⚠️ bug-RCA 编排器(降级参考路径) |
| [`hyperion research`](#hyperion-research) | 代码仓深度调研(P1) |
| [`hyperion patch-report`](#hyperion-patch-report) | 批量 PR 聚合报告(P-A) |

---

## hyperion models

验证 config + 模型工厂加载,列出模型与角色路由。

```bash
uv run hyperion models
```

```
- deepseek-v4-pro       langchain_openai:ChatOpenAI  [thinking, vision]

roles:
  default              -> deepseek-v4-pro
  locator              -> deepseek-v4-pro
  ...
```

## hyperion index

为代码仓建 / 更新 code_index 向量索引(全量原子 swap 或增量对账)。

```bash
uv run hyperion index <repo_path> [repo_name] [--force]
```

| 参数 | 说明 |
|---|---|
| `repo_path` | 仓库根目录 |
| `repo_name` | 索引名(默认取目录名);**必须**与 `config.code_index.repo` 一致,否则 `search_codebase` 查空表 |
| `--force` | 强制全量重建 |

```bash
uv run hyperion index ~/src/wpa_supplicant wpa_supplicant
# 索引完成 [incremental]:wpa_supplicant  1234 chunk  commit=abc123def0
```

## hyperion lsp

L2 精确导航(clangd via multilspy)自检与冒烟。前提:仓库根有 `compile_commands.json`。

```bash
hyperion lsp health [repo_root]                       # 检测 clangd + compile_commands 是否就位
hyperion lsp refs <file> <line> <col> [repo_root]     # 打一次 references(1-based 行列)
```

```bash
uv run hyperion lsp health ~/src/wpa_supplicant
uv run hyperion lsp refs src/scan.c 142 5 ~/src/wpa_supplicant
```

详见 [services/code-index.md](services/code-index.md) §LSP。

## hyperion memory

记忆核心子命令(`recall` / `add` / `ingest` / `list` / `consolidate` / `invalidate`)。

### recall — 翻记忆(多路召回)

```bash
hyperion memory recall "<query>" [--top-k N] [--repo X]
```

### add — 记一条 或 从报告抽

```bash
# 直接记一条
hyperion memory add --kind bug_lesson --summary "..." [--root-cause "..."] [--detail "..."]
                    [--file F --line L] [--commit-sha SHA] [--repo X]

# 从报告文件抽(LLM extract)
hyperion memory add --from-report <报告.md> [--commit-sha SHA] [--repo X]
```

`--kind` 取值:`bug_lesson` | `codebase_fact`。

### ingest — 摄取文档 / 补丁 → 记忆

```bash
hyperion memory ingest <path> [--kind auto|report|patch] [--source-tier imported|stated|inferred]
                      [--commit-sha SHA] [--repo X]
```

按扩展名自动分流:`.md/.txt/.pdf` → 报告路(extract);`.patch/.diff` → 补丁路(retrieve-then-summarize)。详见 [guides/memory-ingest.md](guides/memory-ingest.md)。

### list / consolidate / invalidate

```bash
hyperion memory list [--kind K] [--include-invalid] [--repo X]   # 列知识项
hyperion memory consolidate [--repo X]                            # 巩固:升级 mental_model
hyperion memory invalidate <id> [--reason "..."] [--repo X]      # 失效一条(软删)
```

详见 [services/memory.md](services/memory.md)。

## hyperion mcp serve

启动 MCP server,把 Hyperion 能力做成工具给 coding agent 调。

```bash
hyperion mcp serve [--codebase X] [--transport stdio|http] [--host H] [--port P]
```

| 参数 | 说明 |
|---|---|
| `--codebase` | 查哪个代码库(= 建索引时的 name);默认 `config.code_index.repo` |
| `--transport` | `stdio`(默认,子进程 1:1)\| `http`(streamable-http,warm 长进程) |
| `--host` / `--port` | http 模式绑定(默认 `127.0.0.1:8765`) |

```bash
uv run hyperion mcp serve --codebase wpa_supplicant
uv run hyperion mcp serve --transport http --codebase wpa_supplicant
# → http://127.0.0.1:8765/mcp
```

详见 [tools/mcp-tools.md](tools/mcp-tools.md) 与 [guides/bug-rca-opencode.md](guides/bug-rca-opencode.md)。

## hyperion bug-rca

> [!WARNING]
> **降级参考路径**。bug-RCA 主路径已转向 opencode + bug-rca skill + Hyperion MCP 工具。本编排器命令保留向后兼容,仍可跑,但会打印转向提示。主路径见 [guides/bug-rca-opencode.md](guides/bug-rca-opencode.md)。

```bash
hyperion bug-rca --repo <path> --trigger "<线索>" [--log <日志文件>]
```

| 参数 | 说明 |
|---|---|
| `--repo` | 仓库根目录(required) |
| `--trigger` | bug 线索(日志摘要 / 问题描述 / 漏洞关键句);纯日志驱动可省 |
| `--log` | 原始日志文件(交给 opencode 用 grep / awk 按时间窗切) |

至少给 `--trigger` 或 `--log` 之一。详见 [workflows/bug-rca.md](workflows/bug-rca.md)。

## hyperion research

代码仓深度调研(P1):repo → 架构 / 模块报告 + `codebase_fact` 入记忆。

```bash
hyperion research --repo <path> --codebase <name> [--owner <owner>]
```

| 参数 | 说明 |
|---|---|
| `--repo` | 仓库根目录(required) |
| `--codebase` | 仓库名(= 建索引 name;CRG db / 记忆 scope.codebase 用)(required) |
| `--owner` | 记忆 `scope.owner`(默认 `default`) |

需先 `hyperion index` 建索引。详见 [workflows/deep-research.md](workflows/deep-research.md) 与 [guides/run-research.md](guides/run-research.md)。

## hyperion patch-report

批量 PR 聚合报告(P-A):一组 PR → fetch → 逐 PR 分析 → 跨 PR 聚合 → cited 报告。

```bash
hyperion patch-report --prs <url...> --repo <path> --codebase <name>
                     [--owner <owner>] [--deep] [--concurrency N]
```

| 参数 | 说明 |
|---|---|
| `--prs` | PR URL 列表(GitHub `github.com/.../pull/N`;Gerrit 同接口)(required) |
| `--repo` | 代码仓根(CRG 图 + validate_patch 用;需先 `hyperion index`)(required) |
| `--codebase` | 仓库名(required) |
| `--deep` | 高风险 / security 子集走 ReAct 深审(默认 light) |
| `--concurrency` | 并发抓取 / 分析(默认 3,GitHub 限速友好) |

> [!NOTE]
> GitHub 匿名调用限速严重,建议配 `GITHUB_TOKEN`。详见 [workflows/patch-report.md](workflows/patch-report.md) 与 [guides/run-patch-report.md](guides/run-patch-report.md)。

## See Also

- [configuration.md](configuration.md) — 配置全段
- [getting-started.md](getting-started.md) — 快速开始
- [tools/mcp-tools.md](tools/mcp-tools.md) — MCP 工具(另一入口)
