# Memory Index

- [Hyperion 项目总览](agent-project-overview.md) — **v2(2026-07-28 重规划)**:调度型 agent(编排+记忆+委托);三支柱 P1 调研/P2 bug-RCA★MVP/P3 记忆★特色;三锁定决策 + R0–R5 路线;deer-flow/oh-my-pi/code-review-graph 只读参考。
- [踩坑记录文档](pitfall-log.md) — `docs/踩坑记录.md` 位置:项目走过的弯路汇总(每条 现象→弯路→根因→教训→现状);设计前先查、踩坑后往上加。#1 = patch 投票 rerank 三段反转;**#2 = Hyperion 侧定位漏斗与 opencode 重复(double localization)→ 改 MCP 工具驱动**。
- [设计前先调研+参考 deer-flow](research-deerflow-first.md) — 每个模块设计前必须先深入调研前沿并精读 deer-flow 对应实现。
- [设计前必查过度设计+最新最佳实践](avoid-overengineering.md) — 牢记:设计前充分评估是否过度设计(YAGNI)+ 查 2025-2026 最新最佳实践;用户对复杂度的直觉通常对(rerank 三段反转教训)。
- [委托型 agent 不建平行管线](delegate-already-localizes.md) — 建 Hyperion 能力前先问"opencode 会不会?";会→做 MCP 工具给它调,别重造(opencode 本就强定位;漏斗→工具反转 = 踩坑 #2)。
- [代码在窗口展示由用户手敲](workflow-show-code-in-window.md) — .py 源码我展示、用户敲入;config.yaml/pyproject/uv sync/验证我直接做。
- [对齐 deer-flow,目标生产级非 demo](align-to-deerflow-production-grade.md) — 实现优先照齐 deer-flow;最小实现可起步但必须迭代到生产级。
- [生产级补齐待办清单](backlog-production-grade.md) — "最小实现→生产级"跨阶段待办 #1–#44;**v2 新借鉴 #38–#44**(Aider repomap/Agentless 漏斗/mini-swe-agent ACI/OpenHands 记忆/graphiti 时序/委托多档/C-RCA 论文)。
- [注释面向小白](comment-style-beginner-friendly.md) — 注释/docstring 用大白话+类比讲清每个库的作用,别晦涩、别默认读者懂。
- [Python语法.md 不提交](commit-python-syntax-notes.md) — 2026-07-31 再次反转:以后**不提交** Python语法.md(个人笔记);commit 用显式路径,**别 `git add -A`**;规则历史反复以最新为准。
- [oh-my-pi 调研与后续设计演进](oh-my-pi-research-and-design-evolution.md) — 2026-07-27 报告 + **v2 更正**:三层栈是用户心智模型(omp 三件未串成管线);记忆改自建 MemoryService(借 mnemopi 巩固),omp 同时是委托目标。
- [DeepSeek 结构化产出踩坑](deepseek-structured-output-gotcha.md) — DeepSeek-v4-pro 思考模式不支持 tool_choice/response_format json_schema;结构化产出改"喂 Schema+直出 JSON+解析"(R2 委托契约同样适用)。embedding/rerank 走 DashScope 不是 DeepSeek。
- [测试步骤+结果在窗口打印](show-test-steps-and-results-in-window.md) — 跑测试/验证时正文里写清"测了啥+期望+实际(绿/红)",不只甩 Bash 输出块。
- [workspace 设计决策](workspace-design-decision.md) — 2026-07-29 定稿(+07-31 简化):bug-RCA 每 bug 一个 workspace 目录(七段)+本地默认/Docker R5+大日志过滤(filter_logs MCP 工具,addr2line/折叠 defer R5)+补丁 git diff 观察+validate Tier0+复用 deer-flow sandbox;R2末最简/R3完整/R5 Docker。
- [多阶段委托决策](multi-stage-delegate-decision.md) — 2026-07-30 定稿 + **07-31 反转(#54-rework B)**:delegate 拆 localize→repair 两阶段(解 glm-5.2 单loop不收敛);R3.1 弃「多候选采样投票」改**迭代 verify-refine(B)**(同 opencode 会话双循环 + verdict 证伪自审 + validate_patch 执行门控);**07-31 patch 投票 rerank 整体移除**(无 oracle 平凡白烧 token)。
- [rerank 投票适用边界](rerank-mechanism-where-it-shines.md) — **2026-07-31 patch 投票 rerank 整体移除**(无 oracle 平凡白烧 token;现代 SOTA 转单轨迹+执行验证);检索 rerank 保留;有 oracle 再评估,不预建。
- [runtime 中间件策略](runtime-middleware-policy.md) — 2026-07-30:不抄 deer-flow 30+,pull-by-need 加(R3.0=2/R3.2=5-8/R5选配);扩展口已留(middleware 列表+state_schema 自动合并+TypedDict+tool_output 沙箱钩子);将来 skills/MCP/鉴权/沙箱/artifacts/前端 R4/R5 加而不改;@Next/@Prev 链>7 再移植;记忆自建不抄。
- [opencode MCP 接线硬细节](opencode-mcp-wiring.md) — 2026-08-03 源码核实:opencode 配置用顶层 `mcp` 键(非 mcpServers)、`command` 单数组、env 字段叫 `environment` 且 local **不展开 `{env:}`** → codebase 走进程 env 继承(HYPERION_CODEBASE);工具名 `server_tool`(单下划线);坑 #33397/#16491(子 agent)/listTools 5s+#17099/<8KB/PYTHONUNBUFFERED。R3.1 把 Hyperion 能力作 MCP 工具给 opencode 调。
