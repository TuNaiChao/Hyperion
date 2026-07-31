---
name: pitfall-log
description: "踩坑记录文档(docs/踩坑记录.md)位置 —— 项目走过的弯路汇总;设计前先查、踩坑后往上加"
metadata:
  node_type: memory
  type: reference
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-07-31T08:37:30.422Z
---

`docs/踩坑记录.md` 是专门记录**走过的弯路 / 踩过的坑**的累积文档(每条五段:现象 → 弯路 → 根因 → 教训 → 现状)。

**何时查 / 何时写**:① 设计新模块前先翻一遍(避免重复踩已知坑);② 做了设计反转 / 删了已建代码 / 用户指出过度设计 / 调研推翻既有方案 时,往上加一条(模板在文档末尾)。

**首条 #1(2026-07)**:patch 投票 rerank 的**三段反转**(当主路径 → 降级默认关兜底 → 整体移除)。根因:无 oracle 时投票平凡 + 现代 SOTA 转单轨迹+执行验证;"默认关兜底"是伪安全(死代码是债)。关联 [[rerank-mechanism-where-it-shines]]。

**#2(2026-07)**:Hyperion 侧定位漏斗(file→function→line)建到一半发现**与委托目标 opencode 重复定位**(double localization)→ 整套砍,改"opencode 自主定位 + Hyperion 把记忆/检索/日志过滤做成 MCP 工具给它调"。根因同 #1:**设计前没核前提**(opencode 本就会定位;Hyperion 是委托型不是独立 agent,误搬 Agentless 漏斗)。教训:建 Hyperion 能力前先问"opencode 会不会?",会→做工具别重造(见 [[delegate-already-localizes]])。
