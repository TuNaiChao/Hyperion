# 指南 · 跑通代码仓深度调研

> `hyperion research` —— 给一个代码仓,自动产**架构 / 模块调研报告** + 把代码事实沉淀进记忆。
> 对标 P1「代码仓深度调研」。适合接手一个陌生系统软件代码库时,先建立全局认知。

## 它做什么

一条 LangGraph 流水线跑六步:

```
ingest → index → plan → research → report → memorize
```

1. **ingest**:登记仓库 + scope。
2. **index** ★:**自动建索引**(code_index 语义索引 + CRG 结构图)—— **不需要你先跑 `hyperion index`**,这步内部自己做。
3. **plan**:CRG 社区检测 = 模块边界,产出调研计划(调研哪些模块)。
4. **research**:每个模块起一个 ReAct 子 agent,用 `search_codebase` / `blast_radius` / LSP 深挖。
5. **report**:cited-reporter 写报告(§5 结构,引用真实符号)+ Verifier 零幻觉回查。
6. **memorize**:`codebase_fact` 入记忆(带溯源,供后续 bug-RCA 复用)。

> [!NOTE]
> research 和 patch-report 的索引处理**不同**:research 内部自己建索引(步骤 2);patch-report **要求你先手动建索引**(`hyperion index`),因为它直接复用已有 CRG 图做 risk_score。别搞混。

## 命令

```bash
hyperion research --repo <仓库路径> --codebase <名字> [--owner <owner>]
```

| 参数 | 必填 | 说明 |
|---|---|---|
| `--repo` | ✅ | 仓库根目录 |
| `--codebase` | ✅ | 仓库名(= 索引表名 + 记忆 `scope.codebase`) |
| `--owner` | ❌ | 记忆 `scope.owner`(默认 `default`;多用户隔离用) |

## 示例

```bash
# 调研 wpa_supplicant 示例仓
uv run hyperion research --repo example/demo2/wpa --codebase wpa
```

跑完(数分钟,真调模型建子 agent):

```
报告:data/deep_research/wpa-report.md
CodebaseFact 入记忆:12 条
```

## 前置

- 配好 `.env`(模型 + embedding key,见 [../getting-started.md](../getting-started.md))。
- `model_roles` 里 research 用到的角色(planner / researcher / writer / verifier)都映射到可用模型,见 [../configuration.md](../configuration.md) §model_roles。
- CRG 是可选 extra(`uv sync --extra graph`);不装则 plan 步降级(无社区检测,按文件树粗分)。

## 产物

| 文件 | 路径 |
|---|---|
| 调研报告 | `data/deep_research/<codebase>-report.md`(§5 结构:cited) |
| 记忆 | 记忆库(`kind=codebase_fact`,带 `source` 溯源) |

报告和记忆随后都能被 bug-RCA 召回复用 —— 这就是 P1→P2 的协同:调研沉淀的事实,帮下次定位根因。

## 边界与限制

- **真调模型,较慢**(数分钟,每模块一个子 agent);大仓建议先在子集上试。
- **验证封顶 = 报告准确性**:不编译代码、不跑测试;report 步有 Verifier 零幻觉回查(引用回查),但不保证调研结论的工程正确性,需人复核。
- 子 agent 撞 recursion_limit 会**优雅降级**(astream + 强制收尾),不致整条 workflow 崩。

## 排查

| 现象 | 解解 |
|---|---|
| 超时 / 子 agent 不收敛 | 大仓 + 弱模型易发;`config.runtime` 调大 turn/recursion 限额,或先在子集跑 |
| report 步报引用未回查 | Verifier 拦了幻觉项,正常;查报告里 `[cited: …]` 是否对得上真实符号 |
| 记忆没写入 | `scope` 没对(检查 `--codebase` / `--owner`);或 `facts_memorized=0`(本次无新事实) |

## See Also

- [../workflows/deep-research.md](../workflows/deep-research.md) — 流水线节点 + API 细节
- [../cli-reference.md](../cli-reference.md) §`hyperion research`
- [../services/code-index.md](../services/code-index.md) — index 步用到的检索栈
- [../guides/memory-ingest.md](memory-ingest.md) — 手动摄取报告进记忆(对比自动 memorize)
