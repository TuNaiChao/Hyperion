# CLI 参考

> 入口:`uv run rootrecall <子命令>`(脚本定义见 [cli.py](../src/rootrecall/cli.py))。启动时先把 `.env` 读入环境变量,再解析 config.yaml 里的 `$VAR`。
>
> 按用途分两档:**日常档**(models / index / lsp / memory / mcp)是 skill + MCP 主线的配套;**参考档**(bug-rca / research / patch-report)是早期自跑编排器,降级留作参考 —— 主线用法见 [README](../README.md)。

## 子命令一览

| 命令 | 档 | 作用 |
|---|---|---|
| [`models`](#models) | 日常 | 列出配置的模型 + 角色路由(验证配置) |
| [`index`](#index) | 日常 | 给仓库建索引(向量 + 结构图,一次到位) |
| [`lsp`](#lsp) | 日常 | L2 精确导航(clangd)自检 / 冒烟 |
| [`memory`](#memory) | 日常 | 记忆管理:recall / add / ingest / list / consolidate / invalidate |
| [`mcp serve`](#mcp-serve) | 日常 | 启动 MCP server(16 个工具的入口) |
| [`bug-rca`](#bug-rca) | 参考 | bug 根因定位编排器(降级参考) |
| [`research`](#research) | 参考 | 深度调研编排器(降级参考) |
| [`patch-report`](#patch-report) | 参考 | 批量 PR 聚合报告(降级参考) |

## models

验证配置 + 模型工厂加载,列出模型与角色路由。**配置改完先跑它**,能列出来说明 key 与反射加载都通。

```bash
uv run rootrecall models
```

## index

给代码仓建索引 —— 检索类工具(search_codebase / blast_radius / call_chain / repo_map / repo_overview)的前置。

```bash
uv run rootrecall index <repo_path> [repo_name] [--force] [--no-graph]
```

| 参数 | 说明 |
|---|---|
| `repo_path` | 仓库根目录 |
| `repo_name` | 索引名(默认取目录名);MCP 工具按这个名字查 |
| `--force` | 强制全量重建 |
| `--no-graph` | 只建向量索引不建结构图(快;图系工具将不可用) |

结构图需要 `uv sync --extra code-review-graph`;没装会非致命降级(向量索引照建,提示装法)。

```bash
uv run rootrecall index ~/src/wpa_supplicant wpa_supplicant
# 索引完成:向量 N chunk + 结构图 M 节点
```

## lsp

L2 精确导航(clangd via multilspy)的自检与冒烟。前提:仓库根有 `compile_commands.json`。

```bash
uv run rootrecall lsp health [repo_root]                  # clangd + compile_commands 是否就位
uv run rootrecall lsp refs <file> <line> <col> [repo_root] # 打一次引用查找(1-based 行列)
```

## memory

记忆库的命令行管理(与 MCP 的 memory_* 工具操作同一个库)。

### recall — 翻记忆

```bash
uv run rootrecall memory recall "p2p scan 泄漏" [--top-k 5] [--repo wpa_supplicant]
```

### add — 记一条(或从报告抽)

```bash
# 直接记一条
uv run rootrecall memory add --kind bug_lesson --summary "..." \
    [--root-cause "..."] [--detail "..."] [--file F --line L] \
    [--source-url URL] [--commit-sha SHA] [--repo X]

# 或从报告文件抽(走 LLM 抽取)
uv run rootrecall memory add --from-report 报告.md [--commit-sha SHA] [--repo X]
```

`--kind`:`bug_lesson` / `codebase_fact` / `domain_knowledge`。`--source-url` 配 domain_knowledge(网调知识的溯源链接)。

### ingest — 摄取文档 / 补丁 → 记忆

```bash
uv run rootrecall memory ingest <path> [--kind auto|report|patch] \
    [--source-tier imported|stated|inferred] [--commit-sha SHA] [--repo X]
```

按扩展名分流:`.md/.txt/.pdf` 走报告抽取路,`.patch/.diff` 走补丁路(按 diff 内容算 id,防重复入库)。

### list / consolidate / invalidate

```bash
uv run rootrecall memory list [--kind K] [--include-invalid] [--repo X]  # 列知识项
uv run rootrecall memory consolidate [--repo-path <git仓>] [--repo X]    # 巩固(五 pass:升级/矛盾/去重/已合入/过期)
uv run rootrecall memory invalidate <id> [--reason "..."] [--repo X]     # 失效一条(软删,留档可审计)
```

`consolidate` 给 `--repo-path` 才做「补丁已合入上游」检测(要跑 git 对账)。

## mcp serve

启动 MCP server —— 16 个工具的入口,详见 [MCP 工具参考](mcp-tools.md)。

```bash
uv run rootrecall mcp serve [--codebase X] [--transport stdio|http] [--host H] [--port P]
```

| 参数 | 说明 |
|---|---|
| `--codebase` | 默认查哪个仓的索引 / 记忆(默认 `config.code_index.repo`;多仓靠工具的 per-call `codebase` 参数切) |
| `--transport` | `stdio`(默认,推荐)| `http`(warm 长进程) |
| `--host` / `--port` | http 模式绑定(默认 `127.0.0.1:8765`) |

## bug-rca(降级参考)

早期自跑编排器;主线是 opencode + `bug-rca` skill + MCP 工具。命令保留可跑,用于对照。

```bash
uv run rootrecall bug-rca --repo <path> --trigger "<线索>" [--log <日志文件>]
```

`--trigger` 与 `--log` 至少给一个。

## research(降级参考)

代码仓深度调研编排器,产架构 / 模块报告 + codebase_fact 记忆;主线是 `compare` / `onboarding` skill。

```bash
uv run rootrecall research --repo <path> --codebase <name> [--owner default]
```

## patch-report(降级参考)

一组 PR → 抓取 → 逐个分析 → 跨 PR 聚合报告;主线是 `patch-review` / `upstream-merge` skill。

```bash
uv run rootrecall patch-report --prs <url...> --repo <path> --codebase <name> [--concurrency 3]
```

`--prs` 支持 GitHub 与 Gerrit 链接(Gerrit 需配鉴权环境变量,见[配置参考](configuration.md)密钥速查)。

## 相关文档

- [配置参考](configuration.md) — 模型 / 记忆 / MCP 各段配置
- [MCP 工具参考](mcp-tools.md) — server 起来后有哪 16 个工具
- [README](../README.md) — quickstart 一键配置 + opencode 接入
