# 架构

> 三层(平台 → 服务 → 工作流)+ 横切的 `tools/`(MCP 暴露 + 委托)与 `cli.py`(入口)。

## 系统架构

```
                      ┌──────────────────────────────────────────────┐
   coding agent       │  opencode / codex / claude code              │
   (opencode 主)      │  + bug-rca / patch-review skill (.claude/)   │
                      └──────────────────────┬───────────────────────┘
                                             │  MCP (stdio / streamable-http)
                                             ▼
                      ┌──────────────────────────────────────────────┐
                      │  tools/mcp_memory.py   — 9 个 MCP 工具(暴露面)│
                      │  tools/delegate.py     — 委托 coding agent    │
                      └──────────────────────┬───────────────────────┘
                                             │
        ┌────────────────────────────────────┼────────────────────────────────┐
        ▼                                    ▼                                 ▼
┌──────────────────┐        ┌────────────────────────────┐        ┌────────────────────┐
│  workflows/      │        │  services/                 │        │  platform/         │
│  deep_research   │───────▶│  code_index (P1)           │        │  config            │
│  patch_report    │        │  memory (P3)               │        │  models (工厂)      │
│  bug_rca (参考)  │        │  patch / repos / trigger   │        │  reflection        │
└──────────────────┘        │  workspace                 │        │  sandbox           │
        │                   └────────────────────────────┘        │  runtime (中间件)   │
        │                              │                           │  tracing           │
        └──────────────────────────────┴───────────────────────────┘
                                      │
                                      ▼
                      ┌──────────────────────────────────────────────┐
                      │  data/  (持久化)                              │
                      │  code_index/<repo>/   LanceDB(向量+FTS)       │
                      │  structgraph/<repo>/  CRG graph.db(结构图)    │
                      │  memory/memory.db     SQLite+FTS5+向量(记忆) │
                      │  workspaces/  runtime/  repos/                │
                      └──────────────────────────────────────────────┘
```

## 组件细节

每层的入口文件与职责;详细 API 见各自的模块文档。

### `platform/` — 平台共享层

| 文件 | 职责 | 详见 |
|---|---|---|
| `config.py` | 加载 `config.yaml`、解析 `$ENV`、暴露 `AppConfig` | [configuration.md](configuration.md) |
| `models.py` | `create_chat_model` 反射造模型 + role 路由 + thinking 归一化 | [platform/models.md](platform/models.md) |
| `reflection.py` | `'module:Class'` → 真实类(模型 / 工具加载基础) | — |
| `sandbox/` | `Sandbox` ABC + `LocalSandbox` + grep 引擎 + env 刮密钥 | [platform/sandbox.md](platform/sandbox.md) |
| `runtime/` | `create_hyperion_agent` + 4 中间件 + checkpointer | [platform/runtime.md](platform/runtime.md) |
| `tracing.py` | Langfuse callback(opt-in,缺包静默降级) | [guides/observability.md](guides/observability.md) |

### `services/` — 服务层(三支柱)

| 目录 | 支柱 | 职责 | 详见 |
|---|---|---|---|
| `code_index/` | P1 | tree-sitter 解析 → 切块 → embedding → LanceDB → 混合检索 + 重排 + LSP + 结构图 | [services/code-index.md](services/code-index.md) |
| `memory/` | P3 | `KnowledgeItem` + 可换后端(v1 native = SQLite+FTS5+向量)+ 四路召回 + 摄取 | [services/memory.md](services/memory.md) |
| `patch/fetcher.py` | P-A | URL(PR)→ unified diff + meta | [services/patch-fetcher.md](services/patch-fetcher.md) |
| `repos/resolver.py` | P-A | 本地缺仓 → auto-clone | [services/repos.md](services/repos.md) |
| `trigger_parser/parser.py` | P2 | issue 文档 → 纯文本 | [services/trigger-parser.md](services/trigger-parser.md) |
| `workspace/` | P2 | 每 bug 一个工作目录 + `validate_patch`(Tier 0) | [services/workspace.md](services/workspace.md) |

### `workflows/` — 工作流层(LangGraph)

| 目录 | 支柱 | 职责 | 详见 |
|---|---|---|---|
| `deep_research/` | P1 | 六节点:ingest→index→plan→research→report→memorize | [workflows/deep-research.md](workflows/deep-research.md) |
| `patch_report/` | P-A | 六节点:ingest→fetch_prs→analyze→aggregate→report→memorize | [workflows/patch-report.md](workflows/patch-report.md) |
| `bug_rca/` | P2 | ⚠️ 降级参考路径(主路径 = opencode + skill + MCP) | [workflows/bug-rca.md](workflows/bug-rca.md) |

### `tools/` — MCP 暴露 + 委托

| 文件 | 职责 | 详见 |
|---|---|---|
| `mcp_memory.py` | `build_server()` → `FastMCP`,注册 9 个工具;stdio / http | [tools/mcp-tools.md](tools/mcp-tools.md) |
| `delegate.py` | `CodingAgentDelegate` ABC + `OpencodeDelegate`(subprocess + NDJSON 解析) | [workflows/bug-rca.md](workflows/bug-rca.md) |

### `cli.py` — 入口

`uv run hyperion <subcommand>`,8 个子命令,详见 [cli-reference.md](cli-reference.md)。

## 数据流(典型场景)

**P1 深度调研**:`hyperion research` → 建 code_index + CRG → CRG 社区当模块边界 → 每模块起 ReAct 子 agent(用 code_index 检索 + grep/read 工具)→ cited 报告(Verifier 逐符号@行回查)→ memorize `codebase_fact`。

**P2 bug-RCA(主路径)**:opencode 读 bug-rca skill → `memory_recall` 召回历史教训 → `search_codebase` 定位 → agent 自驱读码 / 改码(grep / awk 切日志)→ `validate_patch` 验 apply → `export_patch` / `export_report` 落盘 → `memory_memorize` 沉淀 `bug_lesson`。

**P-A 批量 PR 报告**:`hyperion patch-report` → `fetch_patch` 抓各 PR diff → `validate_patch` + CRG 影响面(risk_score)→ 跨 PR 聚合(模块 / 安全 / 功能分桶)→ cited 报告 → memorize。

## 三支柱映射

| 支柱 | 服务层 | 工作流 | MCP 工具 |
|---|---|---|---|
| **P1** 调研 | `code_index` | `deep_research` | `search_codebase` |
| **P2** bug-RCA | `workspace` | `bug_rca`(参考) | `validate_patch` / `export_patch` / `export_report` |
| **P3** 记忆 | `memory` | (被所有 workflow 收尾调用) | `memory_recall` / `memory_memorize` |
| **P-A** 补丁/PR | `patch` / `repos` | `patch_report` | `fetch_patch` / `ensure_repo` / `blast_radius` |

## 扩展性

- **工具声明式 + 反射 + 插件**:`config.yaml` 声明 `use: <module>:func`,按需加载。domain 工具放 `tools/plugins/<name>/`,配置里开关,不改核心。
- **记忆后端可换**:丢一个 `services/memory/backends/<name>/`(暴露 `BACKEND_CLASS`)+ 改 `memory.backend`;支持 `'pkg.mod:Cls'` 点路径。拒绝静默回退(配错必报错)。
- **运行时中间件 pull-by-need**:`create_hyperion_agent(middleware=...)` 接任意链,`HyperionState` 是 `TypedDict`,中间件按需加。

## MCP transport

| transport | 用法 | 何时用 |
|---|---|---|
| `stdio`(默认) | agent 拉起子进程 1:1 | 本地单机、delegate、最简 |
| `http`(streamable-http) | 一个 warm 长进程,多 agent 共用 | 省每 bug 重启加载 ~1.2GB(sentence-transformers)的冷启动 |

详见 [configuration.md](configuration.md) §mcp 与 [guides/bug-rca-opencode.md](guides/bug-rca-opencode.md)。

## See Also

- [overview.md](overview.md) — 是什么 / 为什么
- [configuration.md](configuration.md) — 配置全段
- [../../CLAUDE.md](../../CLAUDE.md) — 仓库地图与路线
