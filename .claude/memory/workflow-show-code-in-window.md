---
name: workflow-show-code-in-window
description: 代码我自己直接写文件;同时用大白话+比喻跟用户讲清在干啥(面向小白)
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-08-11T06:40:24.870Z
---

在 Hyperion 项目里,**代码我自己直接写**(Write/Edit `.py` 源码文件,不再只在窗口展示让用户手敲)。
同时**用大白话 + 比喻跟用户讲清楚每一步在干什么**,面向小白。

**分工:**
- `.py` 源码 / `config.yaml` / `pyproject.toml` / 测试 / 记忆 / 文档 → 我直接 Write/Edit。
- `uv sync` / `ruff` / `pytest` / `hyperion ...` 等验证命令 → 我执行(串行、先 `cd` 项目根,见 [[route2-call-chain-handoff]] 的 .venv 争用坑),正文打印"测了啥+期望+实际"。
- commit 用**显式路径**(别 `git add -A`,会误提交 [[commit-python-syntax-notes]] 的 Python语法.md/todo.md);push 单独确认。

**讲解要求(用户 2026-08-11 明确):**
- 干每个模块前/同时,**讲清它的作用**:在这套 agent 里负责什么、为啥需要、跟 deer-flow 对应零件的关系。
- 用**大白话 + 比喻**(面向小白),别晦涩、别默认读者懂底层。
- 代码的 **docstring 与注释一律中文**(见 [[comment-style-beginner-friendly]])。
- 注释解释 why 和非显而易见处,不复述代码。

**Why:** 2026-08-11 反转 —— 之前是"窗口展示、用户手敲"作学习手段;现在用户改为"我自己敲,听我讲解即可"。仍要吃透架构,但通过讲解而非亲手敲。

**How to apply:** 每个模块 —— 我直接写文件 + 正文用大白话讲清"这是啥、为啥这么写"(可给关键片段辅助讲解,但不是让用户敲)→ 跑验证 → 下一个。

**⚠️ 注意区分受众:** 代码**注释**面向小白(大白话+比喻);但 **skill / agent prompt** 面向**模型**(指令性,不教学、不叙事)—— 见 [[skill-prompt-writing-style]]。别混。
