---
name: harness-route-review-2026-08-07
description: 2026-08-07 pivot 后全面路线复核:R4/R5 取消(本地 harness 非 SaaS)+ obsolete 一批(③/Tier1/build_check/artifacts/lazy-load/Skill S1-5)+ 收敛三件事 + 不编译强化 + 核心顺序 filter_logs→多库→2a→2b
metadata: 
  node_type: memory
  type: project
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-08-07T09:44:26.292Z
---

2026-08-07 对全部待做项的全面深入审视(用户逐项拍板)。权威落盘:**CLAUDE.md「路线复核」段**(以此为准)。

## 复核 lens
Hyperion 收敛成**三件事**:① 代码情报(检索/调用链/影响面)② 记忆(bug 教训+代码事实,带溯源持续学习)③ 标准流程 skill+工具(bug-RCA/patch-review/research + apply 验证 + 日志取证)。**不在三件里的 = 偏离 = 砍**。三标准:① 落在三件事内?② 被"不编译/不复现"影响?③ YAGNI?

## 砍/obsolete
- **R4/R5 产品化阶段取消**:pre-pivot「SaaS orchestrator」思路产物,pivot 到本地 harness 后偏离。
  - R4 多用户/租户/鉴权 ❌(本地 MCP server 不需要)
  - R5 Docker 沙箱 ❌(不编译/不复现→无用途)、前端 ❌(harness 无 UI,交互在 coding agent)、artifacts ❌(**并入记忆**不单建,记忆 bug_lesson 带 fix_patch/commit_sha 已是成果载体)、Tier1 运行时验证 ❌(与不编译冲突)
- **③ opencode serve persistent** obsolete(delegate cold-boot 前提消失 + D0 http + lazy 已覆盖;[[opencode-serve-persistent-research]])
- **build_check 接回流程** 取消(与不编译冲突;工具保留按需,踩坑#14)
- **按 intent lazy-load MCP 工具** 降级 YAGNI(当前 ~12 工具,等 20+;前沿 lazy-load 是多 server/多工具规模场景)
- **Skill 子系统 S1-5** 暂缓 YAGNI(opencode 原生发现 .claude/skills/ 已工作,等跨 agent;[[skill-design-decision]])

## 保留碎片(并入功能线,不单独成阶段)
- **多库支持**(同时多仓刚需 → **地基性,前移**;code_index 多实例 + 工具加 codebase 参数 + 记忆全局带 codebase 标签;2a/2b 依赖)
- **可观测增强**(可选运维)

## 验证封顶(强化)
apply(Tier 0,Hyperion 验)。**编译/测试/复现永不做 —— 全部用户(真机)自验**。correctness 基于 apply+读码推理,不报 tested/verified。

## 核心待做顺序(用户拍板)
1. filter_logs 强制注入因果起点行(治 bug-RCA MVP 命中率短板,踩坑#11 真正解;小-中)
2. **多库地基**(同时多仓刚需,2a/2b 依赖,前移;中)
3. feature 2a 调用链(call_chain,CRG 多跳+PageRank;中)
4. feature 2b 跨版本 diff(cross_version_diff,常用,依赖 2a;中-高)
5. 记忆自动 query(P1)

低优 backlog:stdio→http(待 opencode)/ P-A 遗留 / 委托项 / #1-44。

关联:[[harness-pivot-handoff]] [[opencode-serve-persistent-research]] [[skill-design-decision]] [[pitfall-log]] #11/#14 [[avoid-overengineering]] [[delegate-already-localizes]]。完整设计 docs/设计/harness-v2/。
