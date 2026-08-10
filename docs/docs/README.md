# Hyperion 文档

> 权威文档首页。本目录随代码更新,描述 Hyperion **当前**的实现(而非开发历程)。
> 设计动机与决策见上级 `docs/设计/`、外部参考调研见 `docs/调研/`;退役的开发笔记在 `docs/archive/`。

Hyperion 是给系统软件代码库做「带记忆的 bug 根因定位 + 深度调研」的领域 harness ——
把记忆、代码情报、日志取证、补丁验证做成 MCP 工具与标准流程 skill,供 opencode(主)/ codex / claude code 调用。
Tagline:*Light on every root cause.*

## 文档索引

### 总览

| 文档 | 描述 |
|---|---|
| [overview.md](overview.md) | Hyperion 是什么:三支柱、定位、能力总览 |
| [architecture.md](architecture.md) | 三层架构与数据流 |
| [getting-started.md](getting-started.md) | 从零跑起来(uv sync → 配置 → 建索引 → 调研) |
| [configuration.md](configuration.md) | `config.yaml` 全段说明 |
| [cli-reference.md](cli-reference.md) | 全部 CLI 子命令 |

### 平台共享层 `platform/`

| 文档 | 描述 |
|---|---|
| [platform/models.md](platform/models.md) | 多 provider 模型工厂(反射 + role 路由) |
| [platform/sandbox.md](platform/sandbox.md) | 沙箱抽象 + grep 引擎 |
| [platform/runtime.md](platform/runtime.md) | agent 运行时 + 4 个中间件 + checkpoint |

### 服务层 `services/`

| 文档 | 描述 |
|---|---|
| [services/code-index.md](services/code-index.md) | **P1** 代码理解:解析 → 切块 → 向量 → 检索 → LSP → 结构图 |
| [services/memory.md](services/memory.md) | **P3** 记忆核心:schema、四路召回、摄取 |
| [services/patch-fetcher.md](services/patch-fetcher.md) | GitHub / Gerrit 补丁抓取 |
| [services/repos.md](services/repos.md) | 缺仓自动 clone |
| [services/trigger-parser.md](services/trigger-parser.md) | issue 文档解析 |
| [services/workspace.md](services/workspace.md) | bug 工作目录 + 补丁验证 |

### 工具与工作流

| 文档 | 描述 |
|---|---|
| [tools/mcp-tools.md](tools/mcp-tools.md) | 9 个 MCP 工具参考 |
| [workflows/deep-research.md](workflows/deep-research.md) | **P1** 代码仓深度调研工作流 |
| [workflows/patch-report.md](workflows/patch-report.md) | **P-A** 批量 PR 聚合报告工作流 |
| [workflows/bug-rca.md](workflows/bug-rca.md) | ⚠️ bug-RCA 工作流(降级参考路径) |

### 指南与运维

| 文档 | 描述 |
|---|---|
| [guides/bug-rca-opencode.md](guides/bug-rca-opencode.md) | 在 opencode 上做 bug-RCA(主路径) |
| [guides/run-research.md](guides/run-research.md) | 跑一次深度调研 |
| [guides/run-patch-report.md](guides/run-patch-report.md) | 跑一次批量 PR 报告 |
| [guides/memory-ingest.md](guides/memory-ingest.md) | 把报告 / 补丁摄取进记忆 |
| [guides/observability.md](guides/observability.md) | Langfuse 可观测 |
| [operations/inspect-code-index.md](operations/inspect-code-index.md) | 查看 code_index 的 LanceDB |
| [operations/inspect-memory.md](operations/inspect-memory.md) | 查看 memory 的 SQLite |

## 目录树

```
docs/docs/
├── README.md            本文件(索引)
├── overview.md          是什么
├── architecture.md      架构
├── getting-started.md   快速开始
├── configuration.md     配置
├── cli-reference.md     CLI 参考
├── platform/            平台共享层
├── services/            三支柱服务
├── tools/               MCP 工具
├── workflows/           LangGraph 工作流
├── guides/              操作指南
└── operations/          运维(查数据)
```

## 文档约定

- 正文中文;标识符 / API 名 / 配置项 / 代码块一律英文。
- 模块参考文档统一章节:**概览 → 源码 → API → 流程 → 配置 → 边界与限制 → 示例 → See Also**。
- 三支柱记号:**P1** 代码仓调研 · **P2** bug 根因定位 · **P3** 记忆与持续学习。
- 每篇 `## See Also` 用相对路径交叉链接。

## 相关位置

- [../../CLAUDE.md](../../CLAUDE.md) — 始终生效的项目上下文(路线、工作准则)
- [../设计/](../设计/) — 设计文档(架构 / memory / bug-rca / deep-research / harness-v2)
- [../调研/](../调研/) — 外部参考调研(deer-flow / oh-my-pi / code-review-graph / 向量库选型)
- [../archive/](../archive/) — 退役的开发笔记(考古用,非当前真相)
