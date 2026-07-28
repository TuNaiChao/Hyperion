# Memory Index

- [Hyperion 项目总览](agent-project-overview.md) — **v2(2026-07-28 重规划)**:调度型 agent(编排+记忆+委托);三支柱 P1 调研/P2 bug-RCA★MVP/P3 记忆★特色;三锁定决策 + R0–R5 路线;deer-flow/oh-my-pi/code-review-graph 只读参考。
- [设计前先调研+参考 deer-flow](research-deerflow-first.md) — 每个模块设计前必须先深入调研前沿并精读 deer-flow 对应实现。
- [代码在窗口展示由用户手敲](workflow-show-code-in-window.md) — .py 源码我展示、用户敲入;config.yaml/pyproject/uv sync/验证我直接做。
- [对齐 deer-flow,目标生产级非 demo](align-to-deerflow-production-grade.md) — 实现优先照齐 deer-flow;最小实现可起步但必须迭代到生产级。
- [生产级补齐待办清单](backlog-production-grade.md) — "最小实现→生产级"跨阶段待办 #1–#44;**v2 新借鉴 #38–#44**(Aider repomap/Agentless 漏斗/mini-swe-agent ACI/OpenHands 记忆/graphiti 时序/委托多档/C-RCA 论文)。
- [注释面向小白](comment-style-beginner-friendly.md) — 注释/docstring 用大白话+类比讲清每个库的作用,别晦涩、别默认读者懂。
- [Python语法.md 要提交](commit-python-syntax-notes.md) — 用户 2026-07-24 反转原约束,以后都随 git 提交(跨机同步优先)。
- [oh-my-pi 调研与后续设计演进](oh-my-pi-research-and-design-evolution.md) — 2026-07-27 报告 + **v2 更正**:三层栈是用户心智模型(omp 三件未串成管线);记忆改自建 MemoryService(借 mnemopi 巩固),omp 同时是委托目标。
