---
name: new-feature-run-opencode-e2e
description: 新功能做完我自己跑 opencode e2e(不用等用户自验)
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-08-14T03:29:00.293Z
---

2026-08-14 用户定:做了一个新功能后,**我自己跑 opencode e2e** 验证,不用「用户自验铁律」。

**Why**:之前的铁律(永不编译/不复现,用户真机自验)针对的是**系统软件本身的编译/运行验证**(bluez/wpa 能不能编、补丁对不对)。但新功能(skill / MCP 工具 / 记忆特性)的 **opencode e2e**(在 Hyperion 根启动 opencode + 给 codebase 绝对路径 + 跑 agent block 验工具链路)是 Hyperion 自己的集成测试,该我自己跑,不该甩给用户。

**How to apply**:做完一个新功能(改了记忆 schema / 加了 MCP 工具 / 加了 skill / 改了 consolidate 等核心逻辑),主动跑 opencode e2e 验证——见 [[opencode-mcp-wiring]] 的接线姿势(Hyperion 根启动 + `.env` 灌环境 `set -a; . ./.env; set +a` + 给仓库绝对路径,踩坑#17/#21)。区分:系统软件编译/复现仍用户自验;Hyperion 功能 e2e 我自跑。关联 [[show-test-steps-and-results-in-window]](e2e 结果也正文报告)。
