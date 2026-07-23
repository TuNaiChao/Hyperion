---
name: align-to-deerflow-production-grade
description: 实现优先对齐 deer-flow 代码质量;最小实现可起步但必须迭代到生产级;本项目是生产级非 demo
metadata:
  type: feedback
---

用户明确:**本项目目标是生产级项目,不是 demo。** 实现各类功能时:

- **优先对齐 deer-flow 的对应代码**(质量、边界处理、安全细节),不要自己另写一套次品。deer-flow 是成熟参考(见 [[research-deerflow-first]]、[[agent-project-overview]])。
- **起步可以做最小实现**(快速跑通),但**必须排期迭代到生产级**——"最小实现"是阶段性手段,不是终点。
- **每处简化都要记进待办**(见 [[backlog-production-grade]]),后续补齐,不能忘。

**Why:** 用户要的是能上线的工程,不是演示。早期为求速度做最小版可以理解,但若不显式记下"哪里简化了、要对齐 deer-flow 补成什么样",简化就会固化成技术债。

**How to apply:** 写一个模块时,先看 deer-flow 同款怎么写(search.py / local_sandbox.py / factory.py ...),照齐其结构;若当前阶段必须裁剪,在代码注释里标明"最小实现,待对齐 deer-flow 的 X",并立即把这条加进 [[backlog-production-grade]]。
