# Hyperion v2 设计(harness 转向后的权威设计)

> 2026-08-06 起,本目录是 Hyperion 的**当前权威设计**。`docs/设计/` 下的老文档
> (bug-rca-design.md / pr-review-design.md / deep-research-design.md)是 **pre-pivot** 产物,
> 部分已被本目录取代(见各文档顶部"取代说明")。`docs/设计/harness-pivot-design.md` 是**转向决策记录**
> (为什么转 + 证据),本目录是**转向后的完整设计**。
>
> 一句话身份:**Hyperion = 给系统软件(C 代码库,如 wpa_supplicant / bluez)做「带记忆的 bug 根因定位
> + 深度调研」的领域 harness —— 记忆 + 代码情报 + 日志取证 + 补丁验证 + 标准流程 skill,作为 MCP
> tool/skill server 供 opencode(主)/ codex / claude code 调用。不再自己调度 coding agent 跑固定管线。**

## 为什么转(摘要,详见 [harness-pivot-design.md](../harness-pivot-design.md))

老路线 = Hyperion 当 orchestrator,卡着 opencode 走固定六节点管线定位 bug → ① 跟 opencode 在代码推理上
竞争(必输);② 固定图压开放问题 → 脆弱(踩坑 #7 recursion_limit / #8 线程死锁 / #9 keying)+ 产次优补丁;
③ 价值其实在记忆/代码情报/日志/验证/方法论,打包成 **agent-agnostic 的 tool+skill** 反而增值。

证据:Anthropic 架构师 talk *"Don't Build Agents, Build Skills Instead"* + code-review-graph(已 vendor)/
Sourcegraph 都是 **tool-server(PROVIDER)** + 踩坑 #2 项目级泛化 + MCP 是 2026 给 coding agent 暴露能力的共识。

## 架构一图(详见 [01-architecture.md](01-architecture.md))

```
┌────────────────────────────────────────────────────────────────┐
│  用户的 coding agent(opencode 主 / codex / claude code)         │
│    ① 加载 skill(方法论 playbook)  ② 调 MCP 工具(手术刀)         │
└─────────┬──────────────────────────────────────────────────────┬┘
          │ skills(.claude/skills/*.md,跨平台 agentskills.io)     │ MCP
          ▼                                                       ▼
   ┌────────────────┐                          ┌────────────────────────────────┐
   │ bug-rca    ✅  │                          │ Hyperion MCP server            │
   │ patch-rca  ⏳  │ ──── 工具调用 ──────────▶│ (hyperion mcp serve,stdio/http)│
   │ research   ⏳  │                          │ 6 共享工具 + 专家工具(见下)     │
   └────────────────┘                          └──────────┬─────────────────────┘
                                                          │
              ┌───────────────────────────────────────────┼─────────────────────────┐
              ▼                                           ▼                         ▼
        code_index + CRG                            MemoryService              workspace/validate
        (代码情报 / 影响面 / 调用图)                 (记忆,recall 4 路)          (补丁 apply 验证)
                                                          │
              ┌───────────────────────────────────────────┴─────────────────────────┐
              ▼                                                                     ▼
   hyperion CLI(基建:index 建索引 / mcp serve / memory 查改 / research 跑批量调研)
```

**三层**(详见架构文档):① 共享底座(MCP server + 工具 + 记忆 + code_index + CRG);② skill 层(方法论 playbook,每用例一本);③ agent enforcement 层(opencode 专用 agent,把 playbook 烙进 prompt + 步数 + 硬门)。

## 工具 / skill 目录(详见 [01-architecture.md](01-architecture.md) §工具目录)

**共享工具(一个 MCP server,所有用例复用):**
| 工具 | 作用 | 状态 |
|---|---|---|
| `hyperion_memory_recall` | 翻长期记忆(bug 教训 / 代码事实),带 file:line 溯源 | ✅ |
| `hyperion_search_codebase` | 语义+符号检索,**只回索引里真实存在的符号**(防幻觉) | ✅ |
| `hyperion_filter_logs` | 大日志按 关键字∩时间窗 过滤成有界摘录 | ✅ |
| `hyperion_blast_radius` | 改动影响面(结构图 BFS) | ✅ 转向 D0 |
| `hyperion_validate_patch` | 补丁能否干净 apply(git apply --check,Tier 0 硬门) | ✅ 转向 D0 |
| `hyperion_memory_memorize` | 写一条记忆 / 沉淀教训 | ✅ |
| `hyperion_build_check` ⏳ | 补丁打上后能否编译(Tier 0.5,P-A 阶段 1) | ⏳ |
| `hyperion_call_chain` ⏳ | 函数调用链(CRG 多跳,feature 2a) | ⏳ |
| `hyperion_cross_version_diff` ⏳ | 两版本差异对比(feature 2b) | ⏳ |

**skill(.claude/skills/,跨平台):**
| skill | 用例 | 状态 |
|---|---|---|
| `bug-rca` | bug 根因定位 + 补丁 | ✅ |
| `patch-rca` ⏳ | 单补丁/PR 鉴定(该不该合) | ⏳ 阶段 1 |
| `patch-report` ⏳ | 批量补丁聚合报告 | ⏳ 阶段 2 |
| `research` ⏳ | 调用链 / 跨版本调研 | ⏳ 阶段 3-4 |

## 完整路线图(用户确认,以此为准)

**封顶**:patch 分析的 `correctness` 卡顶到 `builds`(apply+build 都过),**不跑测试、不复现**(用户定:系统软件测试/复现环境太重,不值)。详见各文档"验证分档"。

| 阶段 | 功能 | 形态 | 详见 |
|---|---|---|---|
| **1** | **1a** 单补丁/PR 分析(正确?/作用?/合入?+存库+auto-clone+GitHub 抓取) | `patch-rca` skill + `build_check` 工具 + fetcher + auto-clone + memorize 升级 | [03-patch-analysis.md](03-patch-analysis.md) §1a |
| 1 尾 | **1c** 补丁检索("蓝牙相关补丁") | recall 已能(1a 的 memorize 带 symptom/tags) | §1c |
| 1 | **1d** Gerrit | `PatchFetcher` ABC stub | §1d |
| **2** | **1b** 批量聚合报告(多 PR → 质量/安全/功能) | `patch-report` skill + 聚合工具(分桶 + map-reduce + cited 报告) | §1b |
| **3** | **2a** 调用链(bluez → 蓝牙连接函数链+说明) | `call_chain` 工具(CRG 多跳 + 语义种子 + PageRank)+ research skill | [04-deep-research.md](04-deep-research.md) §2a |
| **4** | **2b** 跨版本 diff(5.50 vs 5.85,上游修了?) | `cross_version_diff` 工具(两版本索引 + 符号对应 + git diff + LLM + 确定性门) | §2b |
| 横切 | 暴露给 opencode/codex/claude code | MCP(同一个 server;skills 跨平台)| [01-architecture.md](01-architecture.md) §暴露 |

**顺序**:阶段 1(1a → 1c/1d)→ 2(1b)→ 3(2a)→ 4(2b)。每阶段 = 加工具 + 加 skill + e2e + 经 MCP 暴露。

## 暴露给其他 agent(详见 [01-architecture.md](01-architecture.md) §暴露)

- **MCP 是 2026 共识**(opencode/codex/cursor/claude code 全原生支持)。deer-flow 走 REST 是因有 Web UI;Hyperion 给 coding agent 用 → 走 MCP。
- **一个 MCP server**,所有工具(共享 + 专家)都在里;skills 走 `.claude/skills/`(agentskills.io 跨平台标准,20+ agent 通用)。
- **transport**:stdio(默认,本地 1:1)+ **streamable-http**(`hyperion mcp serve --transport http`,warm 长进程,解 cold-boot)。
- **接法**:opencode → [config/opencode_hyperion.json](../../../config/opencode_hyperion.json);codex → [config/codex_hyperion.toml](../../../config/codex_hyperion.toml)(`[mcp_servers]` 下划线);claude code → `.mcp.json`。

## CLI 角色(保留,详见架构文档)

CLI 不删,从"跑 bug-RCA 流程"转成**基建 + serve + 运维**:`hyperion index`(建索引/图)、`hyperion mcp serve`(起工具服务,★核心)、`hyperion memory list/recall/ingest`(查改记忆)、`hyperion research`(批量调研报告,仍是 workflow)。**交互式 bug-RCA / patch 分析活儿在 agent 里**(opencode 加载 skill + 调 MCP 工具),不在 CLI。`hyperion bug-rca`(老 orchestrator)降级留兼容 + deprecate 提示。

## 文档导航

- [01-architecture.md](01-architecture.md) — 架构(三层 + 工具/skill 目录 + transport + 暴露 + CLI + 与老文档关系)
- [02-bug-rca.md](02-bug-rca.md) — bug-RCA(skill + agent + 硬门 + e2e 实证 + 验证封顶)
- [03-patch-analysis.md](03-patch-analysis.md) — P-A 补丁分析(1a/1b/1c/1d 全覆盖 + 决策卡 + 封顶)
- [04-deep-research.md](04-deep-research.md) — feature 2(2a 调用链 + 2b 跨版本)
- [../harness-pivot-design.md](../harness-pivot-design.md) — 转向决策记录(为什么转 + 证据,只读)

## 关联记忆(跨会程)
[[harness-pivot-handoff]] [[skill-design-decision]] [[pitfall-log]] #2/#7/#8/#9 [[delegate-already-localizes]]
