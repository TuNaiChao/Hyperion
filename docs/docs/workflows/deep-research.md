# 工作流 · 代码仓深度调研(deep_research)

> `workflows/deep_research/` —— **P1 代码仓深度调研**。输入 repo → 架构 / 模块调研报告 + `codebase_fact` 入记忆。
> CLI:`hyperion research --repo <path> --codebase <name>`。

## 概览

一条 LangGraph 六节点流水线:先建 code_index + CRG 结构图,**用 CRG 的社区当模块边界**,给每个模块起一个 ReAct 子 agent 并行深挖(用 code_index 检索 + grep/read 工具),最后 cited-reporter 写报告 + Verifier 逐符号@行回查防幻觉,产出的 codebase_fact 入记忆。

## 源码

| 文件 | 职责 |
|---|---|
| `deep_research/state.py` | `DeepResearchState` + `ModulePlan` + `ModuleFinding` TypedDict |
| `deep_research/graph.py` | `build_graph()` + `async run(repo_root, *, codebase, owner="default") -> dict` |
| `deep_research/nodes.py` | 六节点函数;常量 `_MAX_MODULES = 8` |
| `deep_research/_plan.py` | `plan_modules(candidates, codebase, model) -> list[ModulePlan]` —— 一次 LLM batch 给每模块 STORM 多视角 focus |
| `deep_research/_research.py` | ★`_research_modules`(并发 3)+ `_research_one_module`(闭包工具 + ReAct 子 agent) |
| `deep_research/_verify.py` | `_verify_report_citations(report_md, state) -> tuple[str, dict]` —— 逐符号@行核验 |
| `deep_research/report.py` | `render_report(state) -> str` |

## API

```python
def build_graph() -> CompiledStateGraph
async def run(repo_root: str, *, codebase: str, owner: str = "default") -> dict
    # 返回 {report_path, facts_memorized, aggregate?, ...}
```

## 流程(六节点)

```
ingest ──▶ index ──▶ plan ──▶ research ──▶ report ──▶ memorize
```

1. **ingest**:读 repo(确认路径 / trigger)。
2. **index**:建 code_index 向量索引 + CRG 结构图(`CodeGraph.build`)。
3. **plan**:`plan_modules` —— 取 CRG 社区当候选模块,一次 LLM batch 给每模块定 STORM 多视角 focus;封顶 `_MAX_MODULES=8`。
4. **research** ★:`_research_modules`(并发 3)→ 每模块 `_research_one_module`:
   - 闭包工具 `grep_symbol` / `read_function` / `search_code`(`@tool`,限定 repo_root);
   - `create_hyperion_agent` 起 ReAct 子 agent + 紧 `TurnBudget(max_turns=20)` + `_recursion_limit_for(max_turns, n_middleware)` 动态算 recursion_limit;
   - 撞 recursion_limit 有 astream + 裸模型**强制收尾补救**(优雅降级,避免整段结果丢失)。
5. **report**:`render_report` + `_verify_report_citations` 逐符号@行核验(四档:strict / near(±5 容差)/ file / bad),给 Existence@Line Ratio。
6. **memorize**:产出的 `codebase_fact` 入记忆。

## 配置 / 前置

- 需先 `hyperion index <path> <name>`(节点 2 也会建,但预建更快)。
- 模型经 `model_roles` 路由(`planner` / `default`)。
- CRG 是可选 extra;deep_research 用它切模块,不装则退化为按目录切。

## 边界与限制

- 模块数封顶 `_MAX_MODULES=8`(太多会爆 token / 时间)。
- 子 agent 用动态 recursion_limit(踩坑教训:中间件是独立图节点,一轮 = N+2 superstep;固定值会撞墙),见 [../platform/runtime.md](../platform/runtime.md)。
- cited-reporter + Verifier 防"引用不存在的符号@行";报告会标 Existence@Line Ratio。
- 真调模型建子 agent,整条流水线较慢(数分钟)。

## 示例

```bash
uv run hyperion research --repo ~/src/wpa_supplicant --codebase wpa_supplicant
# 报告:data/research/<repo>-research.md
# CodebaseFact 入记忆:N 条
```

## See Also

- [../cli-reference.md](../cli-reference.md) §`hyperion research`
- [../guides/run-research.md](../guides/run-research.md) — 跑通步骤
- [../services/code-index.md](../services/code-index.md) — 检索 + CRG
- [../platform/runtime.md](../platform/runtime.md) — ReAct 子 agent 的护栏
- 上级 [../设计/deep-research-design.md](../../设计/deep-research-design.md)
