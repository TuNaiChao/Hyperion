# CLI 参考

> 入口:`uv run rootrecall <子命令>`(脚本定义见 [cli.py](../src/rootrecall/cli.py))。启动时先把 `.env` 读入环境变量,再解析 config.yaml 里的 `$VAR`。
>
> 按用途分两档:**日常档**(models / index / lsp / memory / mcp)是 skill + MCP 主线的配套;**参考档**(bug-rca / research / patch-report)是早期自跑编排器,降级留作参考 —— 主线用法见 [README](../README.md)。

## 子命令一览

| 命令 | 档 | 作用 |
|---|---|---|
| [`models`](#models) | 日常 | 列出配置的模型 + 角色路由(验证配置) |
| [`index`](#index) | 日常 | 给仓库建索引(向量 + 结构图,一次到位;`--seed` 播种增量) |
| [`repo`](#repo) | 日常 | 仓库注册表与生命周期:ls / register / resolve / checkout / sync / gc |
| [`install`](#install--here) | 日常 | opencode 全局注册/卸载(任意目录免接线) |
| [`here`](#install--here) | 日常 | bug/工作目录轻标记(`.rootrecall.yaml` + 项目 opencode.json) |
| [`lsp`](#lsp) | 日常 | L2 精确导航(clangd)自检 / 冒烟 |
| [`memory`](#memory) | 日常 | 记忆管理:recall / add / ingest / list / consolidate / invalidate |
| [`mcp serve`](#mcp-serve) | 日常 | 启动 MCP server(17 个工具的入口) |
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
uv run rootrecall index <repo_path> [repo_name] [--force] [--seed <基线索引名>] [--no-graph]
```

| 参数 | 说明 |
|---|---|
| `repo_path` | 仓库根目录 |
| `repo_name` | 索引名(默认取目录名);MCP 工具按这个名字查 |
| `--force` | 强制全量重建 |
| `--seed` | 从同线基线索引播种:拷贝向量库+manifest(+结构图)再走增量,**只重嵌差异文件**(小版本索引省 95%+ 嵌入费;目标已存在则跳过拷贝) |
| `--no-graph` | 只建向量索引不建结构图(快;图系工具将不可用) |

结构图需要 `uv sync --extra code-review-graph`;没装会非致命降级(向量索引照建,提示装法)。

重跑语义:两条索引都增量 —— 向量按 manifest 只重嵌改动文件(重嵌前先清该文件的旧行,符号改名不留重复行;已删除的文件行也会被清掉);结构图按 `built_head` 快照只重解析改动 + 未跟踪新增的文件(社区按需重检测),无改动直接跳过。补丁打进工作区或合入后,重跑本命令刷新即可;`--force` 才全量重建(图拿不准的场合也会自动退回全量)。

`repo_path` 给哪个目录,索引就以它为根记相对路径:给仓库根就覆盖全仓,给代码子目录就只有子目录 —— **多次重建要给同一个根**,路径前缀变了等于换了一套主键。文件遍历尊重 `.gitignore`:工作区里 clone 进来当参考的外部仓只要 ignore 了,不会被扫进索引白付嵌入费。

```bash
uv run rootrecall index ~/src/wpa_supplicant wpa_supplicant
# 索引完成:向量 N chunk + 结构图 M 节点
```

## repo

仓库注册表(`data/repos.yaml`)与生命周期管理 —— 把「索引名↔仓库路径↔角色↔bug 关联」串成一条链。
注册表同时是 MCP 工具 `repo_path` 参数的反查源(注册表 → 索引清单 repo_path → data/repos 逐级),
`validate_patch` / `when_introduced` / `cross_version_diff` / `merge_eval` / `export_patch` 都能**直接传注册名**。

```bash
uv run rootrecall repo ls                                   # 全机资产一览(角色/路径/索引名)
uv run rootrecall repo register <名> --url <git地址> --role baseline --branch <分支>
uv run rootrecall repo register <名> --path <本地路径>       # 已有本地仓登记(upsert;--role 缺省=保留现值)
uv run rootrecall repo resolve <名或路径>                    # 反查本地绝对路径(打印命中来源)
uv run rootrecall repo rm <名>                               # 只删记录不删盘上文件

# 一次性 bug 检出(worktree 共享对象库,秒级;登记 ephemeral)
uv run rootrecall repo checkout <新名> --from <基线名> --ref <tag/分支/commit> [--bug <bug号>]
uv run rootrecall repo checkout bluez-v20-5.50.61 --from bluez-v20 --ref 5.50.61 --bug B-17 --index
#    --index:开仓顺手建索引 —— 播种基线索引后增量建(差异文件才重嵌,省 95%+ 嵌入费);
#             基线没建过索引则诚实走全量;embedder 不可用跳过建索引但不挡检出

# 基线同步(幂等,给定时器反复跑):fetch→ff→增量刷索引→(可选)上游三态分析报告
uv run rootrecall repo sync [基线名...] [--analyze <发行版仓名>] [--analyze-agent] [--ingest-report] [--no-index]
#    --analyze-agent:三态报告后 headless opencode 复核「该不该合」追加进报告(不在/失败诚实退纯三态)
#    --ingest-report:报告摄取进记忆(codebase=项目名),recall 能带出「上次评估为什么没合」

# 回收过期 ephemeral(级联:worktree+向量索引+结构图+记录;记忆不删;baseline 不碰)
uv run rootrecall repo gc [--dry-run] [--max-age-days 14] [--name <名>] [--prune-orphans]
```

| 角色 | 语义 |
|---|---|
| `baseline` | 共享基线(bluez 上游 / uos v20 线…):永久保留,`sync` 定时更新;首个 bare 镜像落 `data/mirrors/` |
| `ephemeral` | 某 bug 的一次性检出(`data/worktrees/`):到期 `gc` 级联回收,可点名强删 |
| `unmanaged` | `ensure_repo` 顺手 clone 的样机 / 手动 index 未声明角色的仓:gc 不碰 |

`sync --analyze` 的三态报告(已修/建议合/冲突)是纯 git 确定性事实(patch-id + merge-tree,零 LLM),
落 `data/upstream_reports/<基线名>/<时间戳>-sync.md`;「该不该真合」走 upstream-merge skill 复核。
定时部署样例(systemd user timer / cron)见 [deploy/](../deploy/README.md)。

> **自然语言 → 自动开仓**:`find_repo` MCP 工具(17 号)按「项目+版本」查注册表;版本没有精确
> 命中时返回基线清单 + 一条带安装根、bash 可原样跑的 `repo checkout … --index` 命令 —— agent
> 照跑即开仓建索引,全程不问用户要路径(bug-rca/backport SKILL 已接此路径)。

## install / here

opencode 接线的两条路,取代「每个 bug 目录跑一次 wire 脚本」:

```bash
uv run rootrecall install --global            # 全机一次:skills 软链 + mcp.rootrecall + AGENTS.md 路由段
uv run rootrecall install --global --uninstall  # 卸载(只摘自己写的;别人的配置绝不动)
uv run rootrecall here [--codebase <索引名>]   # 在 bug 目录里跑:写 .rootrecall.yaml + 项目 opencode.json
```

`install --global` 后**任意目录** `opencode` 免接线直接问(skill 走 `~/.config/opencode/skills/`、
MCP 走全局 opencode.json 的 `cwd` 锚回本仓、路由表走 `~/.config/opencode/AGENTS.md` 标记段落);
`here` 在当前目录补项目级默认检索库(`ROOTRECALL_CODEBASE`),已有别人配置时备份 `.bak` 后跳过。
注意:全局 AGENTS.md 会注入本机所有 opencode 会话(路由表自带条件判据,对无关项目只多占少量
system prompt);介意就用项目级 [wire_opencode.sh](../scripts/wire_opencode.sh)。

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

启动 MCP server —— 17 个工具的入口,详见 [MCP 工具参考](mcp-tools.md)。

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
- [MCP 工具参考](mcp-tools.md) — server 起来后有哪 17 个工具
- [README](../README.md) — quickstart 一键配置 + opencode 接入
