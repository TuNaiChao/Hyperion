---
name: agent-project-overview
description: Hyperion 项目的目标、架构决策与设计文档位置
metadata:
  type: project
---

**项目名:Hyperion**(GitHub `TuNaiChao/Hyperion`,本地目录 `/home/tnc/Desktop/Agent/Hyperion`,Python 包名 `hyperion`)。tagline:*Light on every root cause.* 两台机开发:Linux + macOS,uv 管 Python、`scripts/setup.sh` 装系统工具、`.claude/memory/` 随 git 同步记忆。

**v2(2026-07-28)产品重规划后的定位:**
> **给系统软件代码库做"带记忆的 bug 根因定位 + 深度调研"的调度型 agent。差异化在「记忆 + 持续学习 + 精准调度」,不在重造一个 coding agent——重活委托给成熟 coding agent(omp/opencode),Hyperion 自做记忆 + 调度。**

三大支柱:**P1 代码仓深度调研**(任意语言仓库→架构/模块文档;含 PR 跟踪 R4)/ **P2 bug 根因定位 ★MVP**(源码+日志/漏洞→根因+补丁+报告,委托 omp/opencode)/ **P3 记忆与持续学习 ★特色**(代码库知识+bug 报告沉淀,团队共享、带溯源)。v0.1 的"先建深地基再接场景"是过度设计;v2 改为"编排 + 记忆 + 委托"。已建的 code_index(P1.0–P1.5,语义+LSP)作为**资产保留**,降级为记忆/检索后端 + 调研/委托的上下文源。

**三锁定决策(2026-07-28):**
1. **记忆底座** = 自有 `MemoryService` 契约(deer-flow `MemoryManager` ABC + oh-my-pi backend-swap 形状);**v1 后端 = 组合已有 code_index(语义)+ code-review-graph(结构)**;cognee/mem0 作可切换备选后端(零锁死)。不直接用 cognee——避免第三套重叠检索栈。
2. **bug-RCA 委托** = 抽象 `CodingAgentDelegate` 接口;**v1 默认 omp**(本地已装 + schema 校验子 agent 结构化产出 + `/review` 判级 + `--mode rpc`);opencode 作团队分发后端(R4);两者配置可换。
3. **MVP 顺序** = bug-RCA 先(有 `example/demo1`、`demo2` 金标准可对照)。

设计文档:**[architecture.md](../../docs/设计/architecture.md)**(v2 总纲)+ [memory-design.md](../../docs/设计/memory-design.md) / [bug-rca-design.md](../../docs/设计/bug-rca-design.md) / [deep-research-design.md](../../docs/设计/deep-research-design.md) + [p1-code-understanding-design.md](../../docs/设计/p1-code-understanding-design.md)(code_index 已成层)。计划文件:`~/.claude/plans/crystalline-dazzling-ladybug.md`。

**路线 R0–R5**(见 architecture.md §8):R0 规划落地(文档/裁剪)→ R1 记忆核心 → R2 ★bug-RCA MVP(对照 demo2 金标准)→ R3 代码仓深度调研(Aider repomap + 架构文档)→ R4 团队/多库 + PR 跟踪 + opencode 后端 → R5 生产化。

核心约束(易忘、非代码可见):
- **参考实现(只读,.gitignore,各自 clone)**:`deer-flow/`(架构主脊 + Reporter + MemoryManager ABC)、`oh-my-pi/`(委托目标 omp + mnemopi 记忆件)、`code-review-graph/`(结构图引擎,blast-radius/架构地图)。其它高星参考见 architecture.md §10(aider/agentless/swe-agent/openhands 等)。
- 模型工厂用**反射 + 配置声明**(`use: module:ClassName`)多 provider 自适应,加厂家零代码只改 config(`src/hyperion/platform/models.py`)——**已实现**。默认 DeepSeek,可换。
- **.py 源码**:在窗口展示含中文注释、用户手敲;我不 Write/Edit .py 逻辑(例外:ruff --fix/format、我错误注释清理、显式委托 test)。config.yaml/pyproject/uv sync/验证/IDE 配置我直接做。注释面向小白。
- v1 裁掉:`log_symbolizer`/`static_analysis`(委托给 omp/opencode 做),记 backlog;域工具(bluez/wpa plugins)暂缓。

本机环境:Python 3.12;`uv sync` 装依赖;clangd/ctags/bear/compiledb 由 `scripts/setup.sh` 装。code_index + L2(LSP)已实测绿。
