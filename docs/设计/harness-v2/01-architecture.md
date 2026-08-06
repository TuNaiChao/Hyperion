# 01 · 架构(harness 转向后)

> 取代:老 `docs/设计/architecture.md` 的"调度型 agent"框架(其平台/服务层描述仍有效,但"调度/委托"框架已被本文档的 tool+skill 模型取代)。

## 三层架构

Hyperion 的价值按三层组织:**共享底座 → skill 层 → agent enforcement 层**。用例(bug-RCA / patch 分析 / 调研)不是独立子系统,而是**同一个底座上的不同 skill + 专家工具组合**。

### 第 1 层:共享底座(一个 MCP server + 服务)
所有用例复用。这是"领域 harness"的核心。

| 组件 | 位置 | 作用 |
|---|---|---|
| **MCP server** | [tools/mcp_memory.py](../../../src/hyperion/tools/mcp_memory.py) `build_server()` | 一个 FastMCP server,暴露所有工具(共享 + 专家);opencode 按 `hyperion_<tool>` 前缀 |
| **MemoryService** | [services/memory/](../../../src/hyperion/services/memory/) | 记忆核心:KnowledgeItem + native 后端(SQLite+FTS5+向量)+ recall 4 路(BM25+向量+code+structural)RRF+rerank+衰减 |
| **code_index + CRG** | [services/code_index/](../../../src/hyperion/services/code_index/) | 代码情报(parser/chunker/embed/store/retrieval/index/lsp/outline)+ CRG 结构图(callers/callees/impact_radius) |
| **workspace/validate** | [services/workspace/](../../../src/hyperion/services/workspace/) | 补丁验证(validate_patch = git apply --check)+ workspace 管理 |
| **platform** | [platform/](../../../src/hyperion/platform/) | harness 基础:models 工厂 / config / sandbox / observability |

### 第 2 层:skill(方法论 playbook)
`.claude/skills/<name>/SKILL.md`,跨平台标准(agentskills.io)。skill = "这本菜谱怎么做这个用例",advisory(灵活自纠)但 mandate 关键硬门(如 validate/memorize)。agent 按任务翻开对应 skill。

- `bug-rca/SKILL.md` ✅ —— 9 步:recall→search→filter→假设+证伪→blast→改+validate(硬门)→落盘 patch(硬门)→memorize(硬门)→落盘 report(硬门)。
- `patch-rca/SKILL.md` ⏳(阶段 1)—— 抓 PR→读补丁→apply 门→build 门→blast→LLM 鉴定→memorize。
- `research/SKILL.md` ⏳(阶段 3-4)—— 调用链查询 / 跨版本对比。
- opencode 原生发现 `.claude/skills/`(走项目根向上找);agent 调 `skill(name=)` 按需加载(非 auto-inject)。

### 第 3 层:agent enforcement(opencode 专用 agent)
opencode 配置里把 playbook 烙进 agent 的 system prompt(常驻 > 等 skill 自动触发)+ 给够步数 + 把硬门写成"不做完不算完成"。skill(方法论,可移植)与 agent(enforcement,opencode 专属)正交。配置:[config/opencode_hyperion.json](../../../config/opencode_hyperion.json)。

- `hyperion-bug-rca` ✅ —— steps=25,edit/bash/hyperion* allow,validate+export_patch+memorize+export_report 硬门。
- `hyperion-localize` / `hyperion-repair` —— 老 delegate 两阶段 agent(降级 orchestrator 用,保留)。
- `hyperion-patch-rca` ⏳(阶段 1)。

## 工具目录(MCP server 暴露的全部工具)

**共享工具(所有用例复用,8 个已落 + build_check 待落):**

| 工具 | 签名要点 | wrap 的服务 | 状态 |
|---|---|---|---|
| `hyperion_memory_recall(query, top_k=5)` | 翻记忆,带 file:line 溯源 | `svc.recall`(4 路 + bump) | ✅ |
| `hyperion_memory_memorize(kind, summary, file, line, root_cause)` | 写记忆/沉淀教训 | `svc.memorize` | ✅(阶段 1 升级:接 fix_patch/symptom/blast/commit_sha) |
| `hyperion_search_codebase(query, top_k=5)` | 语义+符号检索,**只回真实符号**(防幻觉) | code_index retrieve | ✅ |
| `hyperion_filter_logs(log_path, keywords, since, until, max_lines)` | 大日志时间窗过滤 | `filter_log_window` | ✅ |
| `hyperion_blast_radius(changed_files, codebase=None)` | 改动影响面 BFS | `CodeGraph.impact_radius` | ✅ |
| `hyperion_validate_patch(patch, repo_path)` | 补丁能否 apply(Tier 0 硬门) | `validate_patch`(git apply --check) | ✅ |
| `hyperion_export_patch(repo_path, out_dir="data/bug_rca")` | 把补丁落盘成 `.patch`(交付硬门;空 diff 自检) | `git add -A && git diff --cached` → 写文件 | ✅ |
| `hyperion_export_report(content, repo_path, out_dir="data/bug_rca")` | 把报告落盘成 `-rca.md`(交付硬门;空内容自检) | agent 传 content → 写文件 | ✅ |

**专家工具(特定用例,随阶段加):**

| 工具 | 用例 | 设计 | 阶段 |
|---|---|---|---|
| `hyperion_build_check(patch, repo_path)` | patch 分析 | 补丁打副本→跑构建(自动认 Makefile/meson/CMake/configure)→`{builds, errors}`;best-effort(缺依赖→降级"build 未检") | 1 |
| `hyperion_call_chain(seed, direction, depth)` | 调用链 | CRG 多跳 callers/callees BFS + 自然语言→入口符号 + PageRank/社区排序 | 3 |
| `hyperion_cross_version_diff(repo_a, repo_b, concern)` | 跨版本 | 两版本索引 + 符号对应 + `git diff` + LLM"修了?"+ 确定性门 `git patch-id` | 4 |

工具粒度原则:精炼、命名清晰、workflow 形(各自内聚内部 plumbing),远低于"工具过载"阈值(arXiv:2605.24660)。**禁止**整条流程包一个 god 工具(命名反模式:rigid + opaque + 不能自纠)。

## MCP transport

- **stdio**(默认):`hyperion mcp serve`。agent 拉起子进程 1:1,本地最简(delegate 老路径 / 零配置)。
- **streamable-http**:`hyperion mcp serve --transport http --host --port`。warm 长进程,多 agent 共用,**解 ③ cold-boot**(省每 bug 重启加载 sentence-transformers ~1.2GB)。mcp SDK 1.28.1 内置;FastMCP 构造吃 host/port → settings → uvicorn(`run()` 不收 host/port)。端点 `http://<host>:<port>/mcp`。
- 配置:[config.yaml](../../../config/config.yaml) `mcp:` 段(transport/host/port 默认);CLI 标志覆盖。

## 暴露给其他 agent(接法)

**MCP 是 2026 共识**(opencode/codex/cursor/claude code 全原生支持 MCP 客户端)。Hyperion 给 coding agent 用 → 走 MCP(deer-flow 走 REST 是因它有 Web UI + IM 网关,场景不同)。

| agent | 接法 | 配置文件 |
|---|---|---|
| **opencode(主)** | MCP `local`(stdio,`command:["hyperion","mcp","serve"]`)或 `http`(url);agent 用 `hyperion*` glob 放行工具 + `skill()` 加载菜谱 | [config/opencode_hyperion.json](../../../config/opencode_hyperion.json) |
| **codex** | `[mcp_servers.hyperion]`(**带下划线**,issue #3441 高频踩坑);stdio(command)或 http(url) | [config/codex_hyperion.toml](../../../config/codex_hyperion.toml) |
| **claude code / 其他** | `.mcp.json`(stdio 或 http);skills 走 `.claude/skills/` 自动发现 | 标准 MCP |

skills 是 agentskills.io 跨平台标准(同一份 SKILL.md 在 opencode/codex/cursor/claude code/Gemini CLI/Copilot 等 20+ 平台通用)→ **一套 skill + 一个 MCP server,所有 agent 都能用**,不锁定单一 agent。

## CLI 角色(保留,基建 + serve + 运维)

| 命令 | 角色 | post-pivot |
|---|---|---|
| `hyperion mcp serve [--transport http]` | **起工具服务**给 agent 接 | ★ 核心 |
| `hyperion index <repo> <name>` | 建代码索引/结构图(给 search/blast/call_chain 用) | 基建 |
| `hyperion memory list/recall/ingest` | 看记忆 / 手动喂报告·补丁 | 运维 |
| `hyperion research` | 深度调研(批量报告,仍是 workflow) | 保留 |
| `hyperion bug-rca` | 老 orchestrator | 降级,留兼容 + deprecate 提示 |

**交互式 bug-RCA / patch 分析活儿不在 CLI 里跑** —— 在 agent 里(opencode 加载 skill + 调 MCP 工具)。CLI 只负责"把灶台支好、把工具服务开起来"。

## 与老设计文档的关系

| 老文档 | 状态 |
|---|---|
| `docs/设计/architecture.md` | 平台/服务层描述仍有效;"调度型 agent"框架被本文档取代 |
| `docs/设计/bug-rca-design.md` | pre-pivot orchestrator 设计;**被 [02-bug-rca.md](02-bug-rca.md) 取代**(老文档保留作历史) |
| `docs/设计/pr-review-design.md` | pre-pivot workflow 设计;**被 [03-patch-analysis.md](03-patch-analysis.md) reconcile**(单补丁→tool+skill;批量→batch) |
| `docs/设计/deep-research-design.md` | deep_research workflow(批量调研报告)仍有效;feature 2(调用链/跨版本)是**新增工具**,见 [04-deep-research.md](04-deep-research.md) |
| `docs/设计/harness-pivot-design.md` | **转向决策记录**(只读,证据库) |
