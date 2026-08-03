---
name: r32-research-e2e-handoff
description: "R3.2 深度调研 e2e 已跑通(2026-08-03 GREEN)。含退出闸结果 + 跑通中修的 4 个真 bug + 关键设计(优雅降级)。"
metadata: 
  node_type: memory
  type: project
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-08-03T08:11:08.815Z
---

**R3.2「代码仓深度调研」e2e 已 GREEN(2026-08-03)。** `hyperion research --repo example/demo2/wpa --codebase wpa` 端到端跑通,三退出闸全过。关联 [[agent-project-overview]] [[pitfall-log]] [[runtime-middleware-policy]]。

## 退出闸结果(全绿)
1. **report.md §5 骨架** ✅:`data/research/wpa__<ts>/report.md`。结构 TL;DR/系统架构/关键模块深挖×8/结构风险/来源/**Verifier**。系统架构由 CRG 社区检测图驱动(非 LLM 编);80 条 file:line 引用全核验通过(0 幻觉,100% 模块覆盖)。
2. **recall 命中 codebase_fact** ✅:`hyperion memory recall "..." --repo wpa` 首 hit 即 codebase_fact(conf 0.95,带多 file:line 溯源)。P1→P2 闭环成立。
3. **CLI 打印 `CodebaseFact 入记忆: 8 条`** ✅(N>0)。

## 跑通中修的 4 个真 bug(都是阻断 e2e 的)
- **Bug A · recursion_limit 单位错**:`_research.py` 把 `_MAX_TURNS`(意为"轮")直接当 `recursion_limit`。LangGraph 的 recursion_limit 按 *superstep* 计,标准 ReAct 每轮=2 superstep。→ 改 `_RECURSION_LIMIT=_MAX_TURNS*2`。
- **Bug B · memorize 没 await**:`node_memorize` 是同步 def 却调异步 `svc.memorize()` → coroutine 没 await → 0 条入记忆(RuntimeWarning)。→ 改 `async def node_memorize` + `await`。graph 本就 ainvoke,async 节点会被 await。
- **Bug C/D · 子 agent 不收敛=全丢(最关键)**:reasoning 模型(deepseek-v4-pro)一路探索到 recursion_limit 还不吐 JSON,GraphRecursionError **把已做的全丢**→"(调研失败)"。调大 limit / 改 prompt 都不治本(8/8 撞墙)。→ **优雅降级**:`astream(stream_mode=values)` 流式留 state,catch GraphRecursionError 后把已收集证据(`_compact_evidence` 压成文本)喂回**裸模型**(不带工具)逼它只产 JSON。smoke 验证:即使 limit=8(4 轮)也能产 8 条真实 cited finding。现在每模块必出 finding。
- **Bug E · 线程内懒导入死锁**:三个 @tool 在函数体里 `from ...code_index... import`,langgraph tool_node 用线程池(run_in_executor)跑工具,首次在线程 import 重模块触发 `_DeadlockError`(包 __init__ 争 import lock)。e2e 里因 node_index 先跑过碰巧不显,但直接调/单测必挂。→ 把 code_index 4 个 import 提到 `_research.py` 模块顶层。
- (附)Bug F · Verifier 章节本只在有幻觉时才附;gate 要"末尾 Verifier 章节"→ 改成**始终附**(透明展示核验结果)。

## 已知设计(非 bug)
- research 每模块**一个 ReAct 子 agent**(B 路线,用户拍板),非单次 LLM。优雅降级不改变这个路线,只在模型不收敛时兜底。
- reasoning 模型倾向过度探索 → 几乎每模块都走降级(正常收敛是少数)。这是 model 行为,降级已覆盖。pull-by-need:若想省 token,可降 `_MAX_TURNS`(现 20;smoke 显示 4 轮证据已够产好 finding)。
- `acc=0` 的 codebase_fact = 未人工验收但已存(recall 不受影响);gate 只看命中。

## 复跑成本
~12-15min:CRG 重建(~2min)+ 8 模块×~20 轮(并发 3)+ 强制收尾 + report/verifier + memorize extract。索引(LanceDB `data/code_index/wpa`)幂等复用。

## 关键文件
`workflows/deep_research/{state,graph,nodes,report,_research,_verify}.py`、`services/code_index/code_graph.py`、`cli.py:cmd_research`。设计 `docs/设计/deep-research-design.md`。**已落地(2026-08-03)**:优雅降级=踩坑 #7、线程死锁=踩坑 #8,均五段式写入 `docs/踩坑记录.md`;整个 R3.2 已 **commit `c03008c`**(29 文件 +1917 −94,pitfall-log/MEMORY 记忆亦同步)。下一步 R3.3(opencode serve persistent #55 + bug-rca report 精修 #46)/ R3.4(文档摄取→学习→记忆 ingest)。
