# CLAUDE.md

> 本文件是 Claude Code(及其他 coding agent)在本项目工作的始终生效上下文。随 git 跨机同步。

## Hyperion 是什么

**给系统软件代码库做"带记忆的 bug 根因定位 + 深度调研"的调度型 agent。** 差异化在「记忆 + 持续学习 + 精准调度」,不在重造一个 coding agent——重活委托给成熟 coding agent(omp/opencode),Hyperion 自做记忆 + 调度。三大支柱:

1. **(P1)代码仓深度调研** — 任意语言仓库(git/本地)→ 详细准确的架构/模块文档;含开源 PR 持续跟踪 + 合入建议(R4)。
2. **(P2)bug 根因定位** ★MVP — 源码 + 日志/漏洞报告 → 根因 + 补丁 + 分析报告;**重活委托** omp/opencode,Hyperion 负责召回+组装精确上下文+调度+沉淀。
3. **(P3)记忆与持续学习** ★特色 — 把"代码库调研知识"和"bug 分析报告"沉淀成可检索、带溯源、团队共享、持续学习的记忆。

三者共享一个**平台 + 共享服务层**(代码理解、记忆、沙箱、检索、可观测),解决三大痛点:① 记忆跨会话;② 省 token(委托前组装手术刀级上下文);③ 流水线(一条命令跑完)。Tagline:*Light on every root cause.*

> v2(2026-07-28)产品重规划:从 v0.1"先建深地基再接场景"改为"编排 + 记忆 + 委托"。已建的 code_index(P1.0–P1.5)作为资产保留。完整架构见 [docs/设计/architecture.md](docs/设计/architecture.md);三支柱详细设计见 [memory-design.md](docs/设计/memory-design.md) / [bug-rca-design.md](docs/设计/bug-rca-design.md) / [deep-research-design.md](docs/设计/deep-research-design.md)。

## ⭐ 工作准则(必读)

**设计任何模块前,先做这两件事,再动手写代码:**

1. **前沿调研** — 用 WebSearch 调研该方向的 2025-2026 最新进展,不要只看旧资料。
2. **参考 deer-flow** — `deer-flow/` 子目录是 ByteDance 的成熟实现(只读参考,`.gitignore` 掉,各自 clone)。精读对应模块,引用具体文件路径与关键代码片段。

综合两者再给方案、再写代码。不要凭空造轮子。

**实现时对齐 deer-flow,目标生产级(不是 demo):** 各功能优先照齐 deer-flow 对应代码的质量与边界处理;起步可做最小实现,但**必须排期迭代到生产级**。**本项目是生产级项目,不是 demo**——"最小实现"是阶段性手段,不是终点。每处简化都记入 `.claude/memory/backlog-production-grade.md`,后续补齐。

## 仓库地图

```
Hyperion/
├── src/hyperion/
│   ├── platform/     # ✅ Harness(已实现):模型工厂 / 配置 / 反射 / 沙箱 / 可观测 / demo agent
│   ├── services/
│   │   ├── code_index/  # ✅ 代码理解(已实现 P1.0–P1.5):parser/chunker/embed/store/retrieval/index/lsp/outline/eval
│   │   └── memory/      # ✅ 记忆核心(R1 已实现):MemoryService 契约 + native 后端(SQLite+FTS5+向量)+ tools/mcp
│   ├── workflows/    # 🆕 三工作流(R1+):bug_rca / deep_research / pr_tracker
│   ├── tools/        # ✅ 导航/沙箱工具(12 个)+ memory 工具(R1)+ 🆕 委托 delegate(R2)
│   └── cli.py        # ✅ 入口(models/run/index/tools/lsp/memory/mcp 已实现;🆕 R2+ 加 bug-rca/research)
├── config/           # config.yaml(模型/工具/记忆/委托 声明式)+ extensions_config.json(MCP/skills)
├── docs/             # 已完成/ · 调研/ · 设计/(architecture + memory/bug-rca/deep-research + p1-code-understanding)
├── example/          # demo1/demo2 金标准(输入 wpa + 日志/漏洞 → 补丁 + 报告)
├── scripts/          # setup.sh(系统工具) / setup_claude.sh(记忆软链)
├── .claude/memory/   # Claude Code 项目记忆(随 git 跨机)
├── deer-flow/        # 只读参考(.gitignore)— 架构主脊 + Reporter + MemoryManager
├── oh-my-pi/         # 只读参考(.gitignore)— 委托目标 omp + mnemopi 记忆
└── code-review-graph/  # 只读参考(.gitignore)— 结构图引擎(blast-radius/架构地图)
```

> 状态标记:✅ 已实现 · 🆕 待建(R1 起)。日志符号化(log_symbolizer)/ 静态分析(static_analysis)在 v2 **裁出 v1**(委托给 omp/opencode),记 backlog。

## 模型:多 provider 自适应

不硬编码任何厂家。在 `config/config.yaml` 的 `models:` 每项声明 `use: <module>:<ClassName>`,工厂 `hyperion.platform.models.create_chat_model` 用反射加载任意 LangChain chat model 类。**加新 provider 通常零代码,只改配置。** 详见 architecture.md §4.1。

## 命令

```bash
uv sync                  # 装/同步 Python 依赖(两台机一致,靠 uv.lock)
uv run hyperion models   # 列出配置的模型(验证 config + 工厂加载)
uv run pytest            # 测试
uv run ruff check .      # lint
bash scripts/setup.sh    # 装系统工具(Linux/macOS 自动适配)+ 记忆软链
```

## 两台机协作(Linux + macOS)

- **Python**:uv + `uv.lock` 保证两台一致;`.python-version` 锁定解释器版本。
- **系统工具**:`scripts/setup.sh` 按 OS 分发(macOS 用 brew / Linux 用 apt)。
- **记忆**:每次 fresh clone 后跑 `bash scripts/setup_claude.sh`,把 Claude Code 记忆软链到仓库内 `.claude/memory/`,随 git 同步。建议两台机仓库路径一致(如都放 `~/Desktop/Agent/Hyperion`),记忆 slug 自然对齐。
- **密钥**:`.env`(gitignore)按 `.env.example` 填,勿提交。

## 扩展性

工具是**声明式 + 反射 + 插件**:`config.yaml` 的 `tools:` 声明工具的 `use: <module>:func`,按需加载。domain 工具(bluez/wpa 解析等)放 `src/hyperion/tools/plugins/<name>/`,在配置里开关,不改核心。

## 路线(v2,2026-07-28 重规划)

**R0** ✅规划落地(文档/裁剪)→ **R1** ✅记忆核心(MemoryService + native 后端 code_index+code-review-graph + MCP + CLI,2026-07-29)→ **R2** ✅bug-RCA MVP(委托 opencode **多阶段** localize→repair + **A+C**:自定义 agent + `steps` 强制收敛 + session 续接;2026-07-30 端到端 delegate 收敛达标,产出报告+补丁+记忆闭环;patch apply + 根因准确性留 R3)→ **R3** 代码仓深度调研 + **workspace_changes**(opencode edit + git diff 根治 patch 格式)+ 多候选/repro(根因准确性)+ runtime 骨架 + CRG → **R4** 团队/多用户(租户隔离 + 鉴权)+ 多库 + PR 跟踪 + skills/MCP → **R5** 生产化(沙箱 Docker + artifacts + 前端 + 可观测)。**这些是规划内扩展面,非临时发现**:runtime 从 R3.0 起即保扩展口 —— `create_hyperion_agent(middleware=...)` 接任意链、create_agent 自动合并 middleware 的 `state_schema`、HyperionState 是 TypedDict,将来 skills/鉴权/沙箱/artifacts 等「加而不改」(中间件按 **pull-by-need** 加,链 >7 再移植 `@Next/@Prev`;记忆仍走自有 MemoryService,不抄 deer-flow MemoryMiddleware)。详见 [architecture.md §8](docs/设计/architecture.md)。

**三锁定决策:** ① 记忆 = 自有 MemoryService 契约 + v1 native 后端(组合 code_index+code-review-graph),cognee/mem0 可换;② bug-RCA 委托给 coding agent,抽象 `CodingAgentDelegate`,v1 默认 omp,opencode 可换;③ MVP 先 bug-RCA。详见各设计文档。
