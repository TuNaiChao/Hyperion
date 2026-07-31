---
name: avoid-overengineering
description: "设计任何模块前必做:① 充分评估是否过度设计(YAGNI)② 查最新最佳实践(2025-2026);用户对复杂度的直觉通常对"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-07-31T05:11:32.864Z
---

**设计任何模块/功能前,牢牢记住两件事(缺一不可):**

1. **充分评估是否过度设计(YAGNI)**:现在真需要吗?有没有更简的路径?这些是过度设计信号 —— ①「默认关的兜底 / 死代码开关」;②「为分摊成本而多处复用」;③「为投机未来预建的扩展口」;④「没查前提条件就上复杂机制」。宁可先做最小实现 + 记 backlog,也别预先建投机性复杂度。
2. **查最新最佳实践(2025-2026)**:WebSearch 调研该方向的最新进展 + 最佳实践,**别凭旧经验/直觉硬上**。趋势会变(如 bug 修复:多采样投票 → 单轨迹+执行验证;若没查就会逆趋势建一堆该删的东西)。

**Why:** rerank 三段反转的教训(见 [[pitfall-log]] #1 + [[rerank-mechanism-where-it-shines]])—— patch 投票 rerank 白建白拆,根因正是设计前没查前提(无 oracle → 投票必然平凡)+ 没跟上 SOTA 趋势。**用户对复杂度的直觉通常对**:用户说"这是不是过度设计"时,往往是,该认真调研验证而非为已有代码辩护。

**How to apply:** 每次设计模块前,显式自问「这是不是过度设计?最简方案是什么?前提条件成立吗?」,并把"查最新最佳实践"和 [[research-deerflow-first]](前沿调研 + 参考 deer-flow)一起做。和 [[align-to-deerflow-production-grade]](最小实现→迭代到生产级)配合 —— 最小实现是手段,不是为复杂度找借口。
