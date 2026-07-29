---
name: show-test-steps-and-results-in-window
description: "跑测试/验证时,在回复正文里打印\"测了哪几步 + 每步结果\",不只甩 Bash 输出块"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-07-29T01:42:13.544Z
---

跑测试 / 验证(pytest、smoke、`uv run` 自检等)时,在回复正文里把**测了什么 + 结果**打印出来,不要只贴 Bash 输出块就完。

**Why:** 用户要能直接在对话里看到"验证了哪些点、每点绿没绿",而不是去翻 tool 的原始输出。

**How to apply:** 每次验证后,正文里用简短清单写清三件事:**① 步骤(测了啥)② 期望 ③ 实际(绿/红 + 关键数值)**,再附原始输出摘要(可裁剪噪音如 proxy 日志)。适用于所有阶段验证(R1 测试、CRG smoke、demo eval…)。关联 [[workflow-show-code-in-window]]([[workflow-show-code-in-window]] 管 .py 展示,本条管测试结果展示)。
