---
name: research-deerflow-first
description: 设计任何模块前先深入调研 + 参考 deer-flow 实现
metadata:
  type: feedback
---

用户要求:Hyperion 项目里**每个模块开始设计前,必须先做深入的前沿调研,并参考 deer-flow 的对应实现**,再给出方案/写代码。deer-flow 在仓库 `deer-flow/` 子目录是成熟参考(只读零件目录,见 [[agent-project-overview]])。

**Why:** deer-flow 已经把模型工厂、记忆中间件、沙箱、子代理、检索工具、配置系统等踩坑做透了;复用其验证过的模式能避免重复造轮子、少踩坑、保证工程质量。这也是用户明确多次强调的工作方式。

**How to apply:** 接到任何模块设计任务(如"做记忆服务""做沙箱""做 PR 跟踪"),先并行做两件事——① WebSearch 调研该方向的 2025-2026 前沿;② 在 `deer-flow/` 里定位并精读对应实现(用 Read/Explore,给出具体文件路径与关键代码)。综合两者再出方案。该准则已写入项目内 `CLAUDE.md`(随 git 跨机生效)。
