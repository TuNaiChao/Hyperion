---
name: multi-stage-delegate-decision
description: bug-RCA 委托改多阶段(localize→repair→verify→可选review)—— 解 glm-5.2 单loop不收敛;Agentless/MASAI 证分阶段又便宜又稳
metadata: 
  node_type: memory
  type: project
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-07-30T04:22:06.163Z
---

2026-07-30 定稿:bug-RCA delegate 从「单次复合委托」(opencode 单 agent loop,定位+补丁+报告一次产)改为**多阶段委托**(对齐 Agentless 三阶段 + MASAI 子 agent 元组):
- ① `localize_delegate`(有工具,只定位 root_cause/evidence,**禁补丁**)→ JSON
- ② `repair_delegate`(根因已锁,只改局部,采 N 候选)→ patch
- ③ `verify`(Hyperion 自跑,无 LLM:Tier 0 `git apply --check`/编译/apply-revert + Tier 1 repro test rerank)
- ④ `review_delegate`(可选,Tier 2 跨家族对抗审,reviewer 先判 intervene 防重写退化)
- ⑤ report+memorize

**Why:** glm-5.2 单 loop 跑 97K token 全工具调用,最后 prose「让我阅读...」**不收敛**(无 JSON) = SWE-agent 单 loop 失败形态。调研铁证(Agentless arXiv 2407.01489):同模型 GPT-4o,**分阶段 32%/$0.70/78K token** vs **单 loop 18.3%/$2.53/498K**(分阶段质量/成本/token 三项全胜,不是「贵但稳」是「又便宜又稳又准」);消融 **skeleton(698 行,58% 命中)完胜整文件(778 行,53.7%)** = lost-in-the-middle —— glm-5.2 内联大片代码过载是不收敛根因(手动测 15 锚点碰巧收敛、端到端 17 锚点更多反不收敛)。验证分层:执行信号(repro test F2P)是唯一硬信号(MASAI 证 LLM 单独选不准 patch);LLM judge 弱(偏 gold-like + SWE-bench 7.8% overfit);对抗审 cross-model 数据:reviewer ≥ writer 才涨点(Codex 自审 +12.9pp、Claude 自审 +0、弱审强 **-8.6pp 退化**)。

**How to apply:** R2 收尾拆 `node_delegate` → `node_delegate_localize` + `node_delegate_repair`(中间 state 传 `localization_json`),verify 用 tolerant apply(Tier 0,已有)。R3 加多候选采样(N=3)+ repro test rerank(workspace 6 步)。R5 加跨模型对抗审 + 2 轮反馈循环 + 退化熔断。`CodingAgentDelegate` 接口不用改(`run` 调多次,每次不同 schema)。完整设计 bug-rca-design.md §多阶段委托。关联 [[workspace-design-decision]]、[[agent-project-overview]]、[[align-to-deerflow-production-grade]]、[[deepseek-structured-output-gotcha]]。
