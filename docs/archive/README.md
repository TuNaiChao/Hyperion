# archive —— 历史噪音(考古用)

这里是**退役的开发期文档**,保留仅为考古 —— 记录开发过程中踩过的坑、被取代的设计、过时的调研。

> [!WARNING]
> **别把这些当现状。** 它们反映的是某个历史时刻的判断,多数已被后续设计取代或撤销。
> **权威文档在 [`../docs/`](../docs/)**(按最新代码写的准确文档)。
> 设计历史(仍有效、用作决策背景)在 [`../设计/`](../设计/) 与 [`../调研/`](../调研/)。

## 为什么留着

- 踩坑记录能解释「为什么代码长这样」(某个看似怪异的写法,往往是一次踩坑的遗产)。
- 设计演变史能看出哪些路试过、为什么不走。

## 内容

| 文件(原位置) | 是什么 | 现状 |
|---|---|---|
| `踩坑记录.md`(`docs/`) | 开发踩坑汇总(每条 现象→弯路→根因→教训) | 教训已固化进代码 / CLAUDE.md / `docs/docs/` |
| `设计演变史.md`(`docs/`) | 架构演变时间线(v0→v2→pivot) | 现状以 `docs/docs/overview.md` + CLAUDE.md 路线为准 |
| `r2-bug-rca-research.md`(`docs/调研/`) | R2 bug-RCA 立项调研 | 已被 `设计/harness-v2/02-bug-rca.md` 取代 |
| `workflow-orchestration-参考.md`(`docs/调研/`) | 编排型 orchestrator 参考调研 | pivot 后 Hyperion 转向 tool+skill server,orchestrator 降级 |
| `bug-rca-design.md`(`docs/设计/`) | 早期 bug-RCA 设计 | 已被 `设计/harness-v2/02-bug-rca.md` 取代 |
| `pr-review-design.md`(`docs/设计/`) | R4.1 PR 批量分析设计 | 已实装,文档见 `docs/docs/workflows/patch-report.md` |

## 还有效的设计 / 调研(原地保留,**不**在此)

下列仍在 `docs/设计/` 与 `docs/调研/` 原地,作为决策背景(不是噪音):

- `设计/harness-v2/*` —— v2 各支柱设计(权威设计层)
- `设计/architecture.md` / `memory-design.md` / `p1-code-understanding-design.md`
- `设计/{harness-pivot,runtime-harness,skill,workspace,deep-research}-design.md`
- `调研/{向量数据库设计分析报告,code-review-graph-调研与借鉴,deer-flow-runtime-参考,后续设计演进报告-oh-my-pi与最佳实践}.md`
