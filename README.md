# Hyperion

> *Light on every root cause.*

**给系统软件(C 代码库,如 wpa_supplicant / bluez)做「带记忆的 bug 根因定位 + 深度调研」的领域 harness —— 记忆 + 代码情报 + 日志取证 + 补丁验证 + 标准流程 skill,作为 MCP tool/skill server 供 opencode(主)/ codex / claude code 调用。** 不自己调度 coding agent 跑固定管线;重活(读码 / 改代码)归成熟 coding agent,Hyperion 当后勤(递手术刀 + 菜谱 + 记忆库)。

## 能做什么

三大场景,共享一个 harness(同一个 MCP server + 记忆 + 代码情报),各配一本 skill(菜谱):

- **bug 根因定位** ✅ —— opencode + `bug-rca` skill + 7 个 hyperion 工具,agent 自驱定位根因、改代码、验证补丁、落盘补丁、沉淀教训(不走固定管线,能自纠)。
- **补丁/PR 分析** ⏳ —— 给个补丁文件 / GitHub PR 链接 → 结合代码库分析(正确?作用?该不该合?)+ 存知识库;本地没仓自动 clone。批量聚合报告 + 检索 + Gerrit(留接口)。
- **代码库深度调研** ⏳ —— 函数调用链(如"蓝牙连接流程涉及哪些函数")+ 跨版本差异(5.50 vs 5.85,上游修了?给参考代码)。

> 验证诚实:补丁质量封顶在 **apply + build**(能打上、能编译),**不跑测试 / 不复现**(系统软件测试环境太重,不划算);标注"看着靠谱",不假装包对。

## 快速开始

```bash
# 1. 装系统工具(Linux/macOS 自动适配)+ Python 依赖 + Claude 记忆软链
bash scripts/setup.sh

# 2. 填密钥
cp .env.example .env   # 然后编辑填 API key(只查非空,不打印值)

# 3. (可选)拉只读参考实现(deer-flow / oh-my-pi / code-review-graph,.gitignore)
#    bash scripts/setup.sh 已处理;或各自 clone 见 CLAUDE.md

# 4. 验证配置 + 模型加载
uv run hyperion models

# 5. 给一个代码库建索引(给 search_codebase / blast_radius / call_chain 用)
uv run hyperion index <仓库路径> <仓库名>

# 6. 起 MCP 工具服务,给你的 coding agent 接
uv run hyperion mcp serve                           # stdio(本地 1:1,默认)
uv run hyperion mcp serve --transport http --port 8765   # streamable-http(warm 长进程,多 agent 共用)
```

**接上你的 agent:**
- **opencode**(主):配置 [config/opencode_hyperion.json](config/opencode_hyperion.json)(注册 hyperion MCP + `hyperion*` 工具放行 + `hyperion-bug-rca` 等 agent);skill 放 [.claude/skills/](.claude/skills/),opencode 自动发现。
- **codex**:[config/codex_hyperion.toml](config/codex_hyperion.toml)(`[mcp_servers.hyperion]` 带下划线)。
- **claude code / 其他**:标准 MCP(`.mcp.json`)+ skills 走 `.claude/skills/`(agentskills.io 跨平台)。

然后在 agent 里翻开对应 skill(`bug-rca` / 即将的 `patch-rca` / `research`)干活。

## 架构(三层)

```
用户的 coding agent(opencode / codex / claude code)
   ↓ 加载 skill(playbook)         ↓ 调 MCP 工具(手术刀)
[ skills: bug-rca ✅ | patch-rca ⏳ | research ⏳ ]
   ────────────────→  Hyperion MCP server(hyperion mcp serve)
                          8 共享工具(recall / search_codebase / filter_logs /
                          blast_radius / validate_patch / export_patch / memorize / export_report)+ 专家工具(⏳ build_check / call_chain / cross_version_diff)
                          ↓
                  code_index + CRG(代码情报)· MemoryService(记忆,recall 4 路)· workspace(补丁验证)
                          ↓
                  hyperion CLI(基建:index / mcp serve / memory / research)
```

一个 MCP server、N 个 skill、N+M 个工具,所有 agent 都能用。Hyperion 不抢 agent 的活,只递工具 + 菜谱。

## 八个 MCP 工具(共享,已落)

| 工具 | 作用 |
|---|---|
| `hyperion_memory_recall` | 翻长期记忆(历史 bug 教训 / 代码事实),带 file:line 溯源 |
| `hyperion_search_codebase` | 语义 + 符号检索,**只回索引里真实存在的符号**(防幻觉) |
| `hyperion_filter_logs` | 大日志按 关键字 ∩ 时间窗 过滤成有界摘录 |
| `hyperion_blast_radius` | 改动影响面(结构图 BFS:改这些文件会波及谁) |
| `hyperion_validate_patch` | 补丁能否干净 apply(`git apply --check`,执行硬门) |
| `hyperion_export_patch` | 把补丁落盘成 `data/bug_rca/<repo>.patch`(交付硬门;空 diff 报错) |
| `hyperion_export_report` | 把分析报告落盘成 `data/bug_rca/<repo>-rca.md`(交付硬门;空内容报错) |
| `hyperion_memory_memorize` | 写记忆 / 沉淀教训 |

## 文档

- **当前权威设计(post-pivot)**:[docs/设计/harness-v2/](docs/设计/harness-v2/)(README 总览 + 架构 + bug-RCA + patch 分析 + 深度调研 + 完整路线图)
- 转向决策记录(为什么从 orchestrator 转 tool+skill server):[docs/设计/harness-pivot-design.md](docs/设计/harness-pivot-design.md)
- 工作约定 + 仓库地图 + 命令:见仓库根 [CLAUDE.md](CLAUDE.md)
- 踩坑记录(设计前先查):[docs/踩坑记录.md](docs/踩坑记录.md)

## 技术栈

Python 3.12 · LangGraph + LangChain · uv 管理依赖 · **mcp** SDK(MCP server,stdio + streamable-http)· tree-sitter / CRG(代码结构图)+ clangd LSP(精确导航)+ LanceDB(向量)· **native 记忆后端**(SQLite + FTS5 + 向量,recall 4 路 RRF+rerank+衰减;mem0 / cognee 可换)· 多 provider 模型工厂(反射加载,加 provider 通常零代码)。

## 状态

post-pivot 地基已落(commit `47654bd`,已 push):D0 MCP 双 transport + 8 工具 + `bug-rca` skill + `hyperion-bug-rca` opencode agent(validate/export_patch/memorize/export_report 硬门,e2e 绿)+ 老 orchestrator 降级留参考。**下一步**:补丁分析(1a)→ 批量报告(1b)→ 调用链(2a)→ 跨版本(2b)。详见 [docs/设计/harness-v2/README.md](docs/设计/harness-v2/README.md) 路线表。
