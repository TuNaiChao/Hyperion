# Hyperion 概览

> 给系统软件代码库做「带记忆的 bug 根因定位 + 深度调研」的领域 harness —— 作为 MCP tool / skill server,供 opencode(主)/ codex / claude code 调用。

## 它解决什么

任何 coding agent 都有两个硬伤:**每次新会话失忆**,且对**大型系统软件 C 仓的调用链 / 影响面掌握不足**。Hyperion 不自己调度 coding agent 跑固定管线,而是把三件难造、复用性高的能力做成工具与流程,让成熟的 coding agent 按需调用:

1. **代码情报** —— 任意语言仓库 → 向量 + 符号 + 结构图检索、调用链、影响面。
2. **记忆** —— 把"代码仓调研知识"和"bug 分析报告"沉淀成可检索、带溯源、跨会话、持续学习的记忆。
3. **标准流程 + 硬门** —— bug-RCA / patch-review / research 的 skill,以及补丁 apply 验证、落盘交付等确定性硬门。

重活(读码、改代码、跑命令)仍归 coding agent;Hyperion 负责**召回 + 组装精确上下文 + 沉淀**。差异化在「记忆 + 持续学习 + 精准的工具与菜谱」,不在重造 agent。

## 三支柱

| 支柱 | 含义 | 主要模块 |
|---|---|---|
| **P1** 代码仓深度调研 | 任意 git / 本地仓 → 架构 / 模块文档 | [code-index](services/code-index.md)、[deep-research](workflows/deep-research.md) |
| **P2** bug 根因定位 ★MVP | 源码 + 日志 / 漏洞报告 → 根因 + 补丁 + 报告 | [workspace](services/workspace.md)、[bug-rca](workflows/bug-rca.md)、opencode + bug-rca skill |
| **P3** 记忆与持续学习 ★特色 | 知识可检索、带溯源、跨会话、持续学习 | [memory](services/memory.md) |

三者共享一个**平台层**(配置、模型工厂、沙箱、运行时、可观测)。

## 定位:tool + skill server

post-pivot(2026-08)后,bug-RCA 的主路径是:**opencode + bug-rca skill + Hyperion MCP 工具**,agent 自驱、能自纠。Hyperion 把能力做成 9 个 MCP 工具:

- **差异化核心**:`memory_recall` / `memory_memorize` / `search_codebase` / `blast_radius` / `fetch_patch`
- **确定性硬门**:`validate_patch` / `export_patch` / `export_report` / `ensure_repo`

> [!NOTE]
> 老的 `bug_rca` LangGraph 编排器(六节点固定管线)降级为**参考路径**,详见 [workflows/bug-rca.md](workflows/bug-rca.md)。bug-RCA 的实际主路径见 [guides/bug-rca-opencode.md](guides/bug-rca-opencode.md)。

## 能力总览

- **9** 个 MCP 工具 —— 见 [tools/mcp-tools.md](tools/mcp-tools.md)
- **8** 个 CLI 子命令 —— 见 [cli-reference.md](cli-reference.md)
- **4** 个运行时中间件(`ToolOutputBudget` / `TokenBudget` / `LoopDetection` / `TurnBudget`)—— 见 [platform/runtime.md](platform/runtime.md)
- **两阶段**代码检索(BM25 + 向量 → RRF 融合 → cross-encoder 重排)
- **四路**记忆召回(memory·BM25 + memory·vector + code + structural → RRF → 衰减)
- 多 provider 模型工厂(加 provider 通常零代码,只改配置)

## 模型:多 provider 自适应

不硬编码任何厂家。在 `config/config.yaml` 的 `models:` 每项声明 `use: <module>:<ClassName>`,工厂 `create_chat_model` 用反射加载任意 LangChain chat model 类。详见 [platform/models.md](platform/models.md) 与 [configuration.md](configuration.md)。

## 验证封顶(重要约定)

Hyperion 的"验证"只到**补丁能否干净 apply**(Tier 0)。**编译、测试、复现一律不做** —— 全部由用户在真机自验(系统软件环境重、构建信号歧义)。因此 Hyperion 给出的结论是"apply 过、读码推理靠谱",不报 `tested` / `verified`;`validate_patch` 的结果只有 `verified: true/false`(指 apply 是否干净),不保证补丁语义正确。

## See Also

- [architecture.md](architecture.md) — 三层架构与数据流
- [getting-started.md](getting-started.md) — 快速开始
- [../../CLAUDE.md](../../CLAUDE.md) — 始终生效的项目上下文(路线、工作准则)
