---
name: r32x-p1-turn-budget-handoff
description: "R3.2.x P1 TurnBudgetMiddleware e2e GREEN(2026-08-03)。e2e 暴露并修了 2 个真 bug:keying 用 id(runtime) 每轮清零 + recursion_limit 漏算中间件节点(superstep=N+2 非 2)。"
metadata:
  node_type: memory
  type: project
---

**R3.2.x P1(TurnBudgetMiddleware)e2e GREEN,已 commit(2026-08-03)。** 关联 [[r32-research-e2e-handoff]] [[pitfall-log]](坑 #9) [[runtime-middleware-policy]]。

## 状态(当前:完成)
- **e2e GREEN**(wpa):日志见 `Turn budget hard stop @20/20`(TurnBudget 生效)、**无** `撞 recursion_limit`、exit 0;报告 267 引用 / Verifier 92 核验 0 幻觉 / `CodebaseFact 入记忆: 10 条` / `recall --repo wpa` 命中 codebase_fact。四闸全过。
- **代码**:[turn_budget.py](src/hyperion/platform/runtime/middlewares/turn_budget.py)(新中间件)+ [factory.py](src/hyperion/platform/runtime/factory.py)(默认链接入)+ [_research.py](src/hyperion/workflows/deep_research/_research.py)(动态 recursion_limit + 统一降级补救)+ [test_turn_budget.py](tests/runtime/test_turn_budget.py)(6 测,含 keying 回归)+ [test_smoke.py](tests/runtime/test_smoke.py)+ 设计文档 §8。
- **单测**:`uv run pytest -q` = **81 passed**(R3.2 原 75 + 5 原 turn_budget + 1 keying 回归),ruff clean。

## P1 干了啥(一句话)
research 子 agent 8/8 撞 `recursion_limit` 硬墙 → 旧现状 `astream+catch+裸模型`降级。P1 加 `TurnBudgetMiddleware`:第 `max_turns-1` 轮 warn、第 `max_turns` 轮 strip `tool_calls`,让模型常规情形**自收尾**(不撞墙)。**关键调研结论**:核 deer-flow 源码确认其 turn 轴**也是 catch `GraphRecursionError`**,strip 只用于 token/loop 两轴 —— Hyperion 把 warn+strip 搬到 turn 轴是**创新**(优于 deer-flow catch+抢残文 / 旧现状 catch+裸模型)。

## ⚠ e2e 暴露并修了 2 个真 bug(必读,踩坑 #9)
P1 初版"代码完+单测绿"但 e2e 红(3 模块撞 recursion_limit)。两个独立 bug:

1. **keying 用 `id(runtime)` 每轮清零**:初版按 `run_id` 分桶,取不到 fallback `id(runtime)`。但 `runtime.context` **没有 `run_id`**(config 只设 thread_id)→ 永远走 `id(runtime)`,而 `Runtime` 对象每 superstep 可能新建 → **计数每轮清零 → 永远到不了 max_turns**。**修**:改按 `thread_id` 分桶(同 LoopDetection)。单测加 `test_counting_keys_on_thread_id_not_object_identity`(每轮换新 runtime 对象、只 thread_id 不变)锁回归 —— 旧代码必挂、新代码绿。
2. **recursion_limit 漏算中间件节点(主因)**:`_RECURSION_LIMIT=(max_turns+2)*2` 按"每轮 2 superstep"算,但**每个中间件的 after_model 是独立图节点**(`create_agent` 给每个 hook `add_node`),一轮 ReAct 实际 = `1(model)+N(after_model 链)+1(tools)=N+2` superstep。5 中间件 → 每轮 ~7 superstep,limit=44 只够 ~6 轮,**TurnBudget 的 max_turns=20 永远到不了**。**修**:删固定常量,`_research_one_module` 里动态算 `recursion_limit = max_turns×(len(middleware)+2)+2×len(middleware)+20`。
3. (统一降级补救,初版已有,保留):strip 后 final 是停止提示无 JSON → 把恢复从 `except` 挪到**循环后统一判 `final 抠不出 JSON → 喂 _compact_evidence 回裸模型`**,覆盖 clean/strip/撞墙 三情形。

**诊断利器(留 /tmp 不提交)**:集成探针 = 桩模型(永远吐 tool_call)+ 真 `create_agent` + 全 5 中间件链。几秒确定性验证"hook 被调 / strip 终止 loop / keying 累积",比烧 15min 真模型 e2e 便宜。详见踩坑 #9 教训 #3。

## 日志判定(供复跑参考)
```bash
uv run hyperion research --repo example/demo2/wpa --codebase wpa 2>&1 | tee /tmp/r32x-p1-e2e.log
```
- ✅ 出现 `Turn budget hard stop: thread default at turn 20/20`(WARNING,实时 flush;TurnBudget 生效)。
- ✅ **不**出现 `research 模块 X 撞 recursion_limit`(出现 = TurnBudget/递归配置错,查踩坑 #9)。
- 退出闸:① `report.md` 全骨架在(267 引用);② Verifier `疑似幻觉 0`;③ CLI `CodebaseFact 入记忆: N 条`(N>0);④ `hyperion memory recall --repo wpa "..."` 命中 codebase_fact。
- **注意**:默认 root logger 是 WARNING 级(无 basicConfig),所以 TurnBudget 的 `warn@N-1` 是 **INFO 不显示**;只有 `hard stop`(WARNING)和 `撞 recursion`(WARNING)可见。判定以 hard-stop 出现 / recursion 不出现 + 四闸为准。

## 下一步
R3.2.x P1 完。后续 R3.3 / R3.4(见 todo.md / backlog)。本 handoff 已归档为完成态。
