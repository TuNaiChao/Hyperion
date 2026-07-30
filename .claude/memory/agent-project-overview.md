---
name: agent-project-overview
description: Hyperion 项目的目标、架构决策与设计文档位置
metadata: 
  node_type: memory
  type: project
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-07-29T03:36:06.203Z
---

**项目名:Hyperion**(GitHub `TuNaiChao/Hyperion`,本地目录 `/home/tnc/Desktop/Agent/Hyperion`,Python 包名 `hyperion`)。tagline:*Light on every root cause.* 两台机开发:Linux + macOS,uv 管 Python、`scripts/setup.sh` 装系统工具、`.claude/memory/` 随 git 同步记忆。

**v2(2026-07-28)产品重规划后的定位:**
> **给系统软件代码库做"带记忆的 bug 根因定位 + 深度调研"的调度型 agent。差异化在「记忆 + 持续学习 + 精准调度」,不在重造一个 coding agent——重活委托给成熟 coding agent(omp/opencode),Hyperion 自做记忆 + 调度。**

三大支柱:**P1 代码仓深度调研**(任意语言仓库→架构/模块文档;含 PR 跟踪 R4)/ **P2 bug 根因定位 ★MVP**(源码+日志/漏洞→根因+补丁+报告,委托 omp/opencode)/ **P3 记忆与持续学习 ★特色**(代码库知识+bug 报告沉淀,团队共享、带溯源)。v0.1 的"先建深地基再接场景"是过度设计;v2 改为"编排 + 记忆 + 委托"。已建的 code_index(P1.0–P1.5,语义+LSP)作为**资产保留**,降级为记忆/检索后端 + 调研/委托的上下文源。

**三锁定决策(2026-07-28):**
1. **记忆底座** = 自有 `MemoryService` 契约(deer-flow `MemoryManager` ABC + oh-my-pi backend-swap 形状);**v1 后端 = 组合已有 code_index(语义)+ code-review-graph(结构)**;cognee/mem0 作可切换备选后端(零锁死)。不直接用 cognee——避免第三套重叠检索栈。
2. **bug-RCA 委托** = 抽象 `CodingAgentDelegate` 接口;**v1 默认 opencode(2026-07-29 调研修正:omp 本机装不上 github 墙+bun;opencode v1.18.3 已装,`run --format json` 事件流绕结构化坑)**;omp(strict schema 强校验,待本机可用)/ claude 作可配置后端。
3. **MVP 顺序** = bug-RCA 先(有 `example/demo1`、`demo2` 金标准可对照)。

**★ 第四决策(2026-07-29 修正):runtime harness 自建。** Hyperion 自己要有 agent 运行时上下文管理(对标 deer-flow 中间件链 + OpenHands 3 层 Condenser→View→ConversationMemory),能跑长 agent(深度调研 R3 必需,读几千文件上下文必爆);**coding 能力仍委托 opencode/omp** 不变。边界:coding 动作→委托;运行时(压历史/token 预算/截工具输出/并行子任务/断点续跑)→自建。设计 [runtime-harness-design.md](../../docs/设计/runtime-harness-design.md) + 对标 [deer-flow-runtime-参考.md](../../docs/调研/deer-flow-runtime-参考.md)。排期:**R3 开场搭骨架**(factory+TokenBudget+ToolOutputBudget+HyperionState+checkpointer;2026-07-30 从 R2末挪 R3:R2 九步不依赖 runtime、不验,R3 深度调研边搭边验)→ R3 中上场(summarization+loop+subagents)→ R5 补齐。详见 backlog #45。

设计文档:**[architecture.md](../../docs/设计/architecture.md)**(v2 总纲)+ [memory-design.md](../../docs/设计/memory-design.md) / [bug-rca-design.md](../../docs/设计/bug-rca-design.md) / [deep-research-design.md](../../docs/设计/deep-research-design.md) + [p1-code-understanding-design.md](../../docs/设计/p1-code-understanding-design.md)(code_index 已成层)。计划文件:`~/.claude/plans/crystalline-dazzling-ladybug.md`。

**路线 R0–R5**(见 architecture.md §8):R0 ✅规划落地 → **R1 ✅记忆核心(2026-07-29)** [MemoryService ABC + native 后端(SQLite+FTS5+向量,组合 code_index 语义 + code-review-graph 结构可选)+ memorize/recall/consolidate + mnemopi 式 Bayes 合并/bi-temporal 软删 + memory @tool + MCP server(反向:delegate 查 Hyperion)+ CLI `hyperion memory|mcp`;8 离线测试绿;DeepSeek 抽取+DashScope 向量/rerank 全链验通] → **R2 ✅bug-RCA MVP(2026-07-30 达标)**(委托 opencode **多阶段** localize→repair 两 delegate + **A+C**:自定义 agent + `steps` 强制收敛 + session 续接;端到端 delegate 收敛、产出报告+补丁+记忆闭环;patch apply + 根因准确性留 R3)→ R3 代码仓深度调研(**workspace_changes** patch 根治 + 多候选/repro 根因准确性 + runtime 骨架 + CRG)→ R4 团队/多库 + PR 跟踪 → R5 生产化。

**R1 deferred(记 backlog)**:CRG 结构路实测(待装 extra + tree-sitter-c 给 wpa/bluez,R3);Weibull 衰减(生产跑 exp halflife);CJK BM25 分词(jieba);本地 ONNX 向量档。

核心约束(易忘、非代码可见):
- **参考实现(只读,.gitignore,各自 clone)**:`deer-flow/`(架构主脊 + Reporter + MemoryManager ABC)、`oh-my-pi/`(委托目标 omp + mnemopi 记忆件)、`code-review-graph/`(结构图引擎,blast-radius/架构地图)。其它高星参考见 architecture.md §10(aider/agentless/swe-agent/openhands 等)。
- 模型工厂用**反射 + 配置声明**(`use: module:ClassName`)多 provider 自适应,加厂家零代码只改 config(`src/hyperion/platform/models.py`)——**已实现**。默认 DeepSeek,可换。
- **.py 源码**:在窗口展示含中文注释、用户手敲;我不 Write/Edit .py 逻辑(例外:ruff --fix/format、我错误注释清理、显式委托 test)。config.yaml/pyproject/uv sync/验证/IDE 配置我直接做。注释面向小白。
- v1 裁掉:`log_symbolizer`/`static_analysis`(委托给 omp/opencode 做),记 backlog;域工具(bluez/wpa plugins)暂缓。

本机环境:Python 3.12;`uv sync` 装依赖;clangd/ctags/bear/compiledb 由 `scripts/setup.sh` 装。code_index + L2(LSP)已实测绿。
