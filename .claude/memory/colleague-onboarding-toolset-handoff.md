---
name: colleague-onboarding-toolset-handoff
description: 2026-08-18 四件套落地:同事安装链路(wire 脚本硬化)+ MCP 工具门控(ROOTRECALL_MCP_TOOLS)+ 记忆/索引两套命名约定 + CRG 增量刷新接入(index 重跑不再全量/跳过)
metadata:
  type: project
---

# 同事安装链路 + 工具门控 + 命名约定 + CRG 增量(2026-08-18)

背景:repo 已公开,用户目标 = 任意同事 `git clone → 脚本自动配置 → bug 目录起 opencode → 直接用`,同时尽量省上下文。四件落地:

## A. MCP 工具门控 `ROOTRECALL_MCP_TOOLS`

- 位置:mcp_memory.py `_resolve_mcp_tools()` + build_server 里 `_tool(name)` 装饰器工厂;16 个 `@mcp.tool()` 全改 `@_tool("<名>")`。
- 取值:unset/空/`full` → 全 16;预设 `minimal`(记忆3+search_codebase+硬门3 = 7)/ `research`(记忆3+情报8 = 11);显式逗号清单;未知名 → **启动即 ValueError**(诚实失败)。
- 关键语义:**未注册的工具不进 tools/list**,模型看不见 → 真省上下文(opencode permission deny 只是调不了,schema 照占位)。
- 写在 opencode `mcp.rootrecall.environment` 里即可(README 给同事安装节有示例)。
- ⚠️ 工具/skill/agent 集合要配套:minimal 下 memory-health-check 的 `memory_dump` 依赖缺失会破 skill —— 裁剪时想清楚。

## B. 同事安装链路(wire_opencode.sh 硬化 + README 节)

- `git init` bug 目录(opencode 项目发现沿 git 根爬;[踩坑#17](pitfall-log.md) 同族)。
- `--codebase <索引名>` → 写进生成配置的 `ROOTRECALL_CODEBASE`(该目录会话默认检索库,检索类工具免传参 —— 这是全局注册给不了的)。
- 路线拍板(用户确认):**每 bug 目录接线为主** + 门控当调音旋钮。全局注册 ≈ 每无关会话常驻 6-9k token(16 schema 4-6k + 8 skill 元数据 ~1k + 10 agent 描述 1-2k),先不做,方案记档 docs/mcp-guide.md 姿势③。
- setup.sh 第 3 步 `ROOTRECALL_CLAUDE_LINK=0` 可跳(外部使用者没 Claude Code);README 新增「给同事安装」节(5 命令)。
- 依赖命名约定见 C。

## C. 记忆/索引两套命名约定(防版本孤岛)

- **索引名 =「项目-版本线」**(wpa-v25):检索/情报类工具用(search_codebase/blast_radius/call_chain/repo_map/repo_overview/cross_version_diff/merge_eval/when_introduced)。
- **记忆 codebase = 项目名**(wpa):记忆类工具用(memory_recall/memorize/dump)。原因:记忆按 codebase 标签隔离(mcp_memory.py Scope),传版本名 = v20 会话永远翻不到 v25 教训。版本上下文写进 summary/evidence。
- 落点:3 个 docstring + bug-rca/backport/compare 三个 SKILL + mcp-guide FAQ + README。**旧 SKILL 真有反例**(backport 曾写「codebase 指向 v20」)。

## D. CRG 增量刷新接入(index 重跑语义升级)

- 旧状:已建图**整个跳过**(--force 才重建)→ 补丁后图静默 stale,比全量重建还糟。
- 新:code_graph.py 加 `CodeGraph.update(repo_root, repo_name) -> tuple[CodeGraph, dict]`,cmd_index 的「已建图」分支改调它。
- 机制:build 后把 `git rev-parse HEAD` 写进 `<图目录>/built_head` 快照;update 算 `git diff --name-only <快照>` ∪ `git ls-files --others`(未跟踪,git diff 看不见)→ CRG `incremental_update(changed_files=...)`(内部还有 per-file sha256 快筛,清单给宽不亏)→ `incremental_detect_communities`(不触及社区就跳过)→ 推进快照。
- **兜底链(宁可贵不错)**:图/快照不存在(旧版图、非 git 仓)/ 非 git 仓 / 快照 commit 不可解析(rebase 改史)→ 全量 build;full_build 是 per-file 原子替换(store_file_nodes_edges 先删该文件旧数据),在旧库上重跑安全。
- summary mode ∈ incremental | noop | full_rebuild;cmd_index 三条打印对应。
- 真机冒烟(wpa 同款姿势,本仓根跑):建图→noop 跳过→改 a.py+加 c.py → 增量重解析 3+1 文件,新符号 zeta/epsilon 带跨文件调用边进图。
- ⚠️ **cwd 陷阱**(冒烟抓的):cmd_index 的 `data/` 是相对路径,从 bug 仓 cwd 跑 index 会把 data/ 写进 bug 仓 → 图库文件被 `ls-files --others` 当未跟踪源码。文档用法(本仓根跑)没这问题;wire 接线的 cwd 锚定也不受影响。4 单测(全量兜底/增量+新符号/noop/非 git 退全量)+ 295 全绿。

关联:[[opencode-only-positioning]](姿势③记档)、[[tier2-index-prerequisite-handoff]](index 一键建,其「跳过」表述已更新)、[[multi-codebase-per-call-handoff]](per-call codebase 地基)、[[pitfall-log]](#17/#21)。
