# Memory Index

- [Hyperion 项目总览](agent-project-overview.md) — **v2(2026-07-28 重规划)**:调度型 agent(编排+记忆+委托);三支柱 P1 调研/P2 bug-RCA★MVP/P3 记忆★特色;三锁定决策 + R0–R5 路线;deer-flow/oh-my-pi/code-review-graph 只读参考。
- [设计前先调研+参考 deer-flow](research-deerflow-first.md) — 每个模块设计前必须先深入调研前沿并精读 deer-flow 对应实现。
- [代码在窗口展示由用户手敲](workflow-show-code-in-window.md) — .py 源码我展示、用户敲入;config.yaml/pyproject/uv sync/验证我直接做。
- [对齐 deer-flow,目标生产级非 demo](align-to-deerflow-production-grade.md) — 实现优先照齐 deer-flow;最小实现可起步但必须迭代到生产级。
- [生产级补齐待办清单](backlog-production-grade.md) — "最小实现→生产级"跨阶段待办 #1–#44;**v2 新借鉴 #38–#44**(Aider repomap/Agentless 漏斗/mini-swe-agent ACI/OpenHands 记忆/graphiti 时序/委托多档/C-RCA 论文)。
- [注释面向小白](comment-style-beginner-friendly.md) — 注释/docstring 用大白话+类比讲清每个库的作用,别晦涩、别默认读者懂。
- [Python语法.md 要提交](commit-python-syntax-notes.md) — 用户 2026-07-24 反转原约束,以后都随 git 提交(跨机同步优先)。
- [oh-my-pi 调研与后续设计演进](oh-my-pi-research-and-design-evolution.md) — 2026-07-27 报告 + **v2 更正**:三层栈是用户心智模型(omp 三件未串成管线);记忆改自建 MemoryService(借 mnemopi 巩固),omp 同时是委托目标。
- [DeepSeek 结构化产出踩坑](deepseek-structured-output-gotcha.md) — DeepSeek-v4-pro 思考模式不支持 tool_choice/response_format json_schema;结构化产出改"喂 Schema+直出 JSON+解析"(R2 委托契约同样适用)。embedding/rerank 走 DashScope 不是 DeepSeek。
- [测试步骤+结果在窗口打印](show-test-steps-and-results-in-window.md) — 跑测试/验证时正文里写清"测了啥+期望+实际(绿/红)",不只甩 Bash 输出块。
- [workspace 设计决策](workspace-design-decision.md) — 2026-07-29 定稿:bug-RCA 每 bug 一个 workspace 目录(七段)+本地默认/Docker R5+大日志分层预筛(Hyperion粗筛+delegate深挖)+补丁6步验证+复用 deer-flow sandbox;R2末最简/R3完整/R5 Docker。
- [多阶段委托决策](multi-stage-delegate-decision.md) — 2026-07-30 定稿:delegate 拆 localize→repair→verify→可选review(解 glm-5.2 单loop不收敛);Agentless 32%/$0.70 vs SWE-agent 18.3%/$2.53(分阶段又便宜又稳);验证分层(执行信号硬/对抗审弱);R2收尾两阶段/R3多候选+repro/R5对抗审。
- [runtime 中间件策略](runtime-middleware-policy.md) — 2026-07-30:不抄 deer-flow 30+,pull-by-need 加(R3.0=2/R3.2=5-8/R5选配);扩展口已留(middleware 列表+state_schema 自动合并+TypedDict+tool_output 沙箱钩子);将来 skills/MCP/鉴权/沙箱/artifacts/前端 R4/R5 加而不改;@Next/@Prev 链>7 再移植;记忆自建不抄。
