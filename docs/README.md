# docs/ —— 文档导航

Hyperion 的文档分三层,**按需进入**:

| 目录 | 是什么 | 看它的时机 |
|---|---|---|
| **[`docs/`](docs/)** | ★ **权威文档**(按最新代码写,准确易读) | 日常用 —— 装配、用法、API、配置、运维 |
| [`设计/`](设计/) · [`调研/`](调研/) | 设计历史(决策背景,仍有效) | 想懂「为什么这么设计」时 |
| [`archive/`](archive/) | 退役噪音(踩坑叙事、被取代的旧设计) | 仅考古,**别当现状** |

## 新读者从哪开始

→ 直接进 [`docs/README.md`](docs/README.md)(权威文档索引),按它的目录树挑。

几条快速路径:

- **第一次用**:[`docs/getting-started.md`](docs/getting-started.md)
- **做 bug-RCA**:[`docs/guides/bug-rca-opencode.md`](docs/guides/bug-rca-opencode.md)(主路径)
- **查 MCP 工具**:[`docs/tools/mcp-tools.md`](docs/tools/mcp-tools.md)
- **查 CLI 命令**:[`docs/cli-reference.md`](docs/cli-reference.md)
- **看数据落盘**:[`docs/operations/`](docs/operations/)

## 各层说明

### `docs/` —— 权威(2026-08 重写)

按当前代码重写的全模块文档,Diátaxis 风格(概览 / 操作指南 / API 参考 / 运维)。**与代码对齐,去除了开发噪音**。所有交叉链接都在这一层内闭环。索引:[`docs/README.md`](docs/README.md)。

### `设计/` + `调研/` —— 设计历史

设计文档(architecture / 三支柱 / pivot / runtime / skill / workspace …)与前沿调研(向量库选型 / CRG 借鉴 / deer-flow 参考 / OMP 演进)。**作为决策背景仍有效**,但代码细节以 `docs/` 为准(若两者冲突,`docs/` 赢)。

### `archive/` —— 退役

踩坑叙事、被取代的旧设计(详见 [`archive/README.md`](archive/README.md))。保留只为考古。

### `已完成/`(原地保留)

`已完成/lancedb-使用说明.md` 与 `已完成/langfuse.md` 的内容**已被 `docs/operations/` 与 `docs/guides/` 覆盖**,原地保留不删(有历史细节),日常请看新版。
