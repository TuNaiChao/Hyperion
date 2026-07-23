---
name: comment-style-beginner-friendly
description: "代码注释/docstring 要面向小白,讲清每个库/工具的作用,别晦涩、别默认读者懂"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-07-23T13:00:53.729Z
---

写代码注释和 docstring 时要**面向小白**:用大白话 + 类比把概念讲透,尤其要解释每个外部库/工具"是干什么的、为什么用它",不要默认读者懂、不要堆术语。

**Why:** 用户反馈 `parser.py` 的模块 docstring"太晦涩"——直接讲"tree-sitter 容错解析、抽取符号、GRAMMARS 数据驱动",却没解释 tree-sitter 到底是什么。用户要的是能看懂的说明,不是术语堆砌。

**How to apply:**
- docstring 先讲"这一层干什么、为什么需要它"(用具体场景),再用类比(如:tree-sitter ≈ 给代码标"主谓宾"的语法分析器;符号卡片 ≈ 每个函数/类一张索引卡)。
- 提到新库/工具时,先用一两句大白话说它是什么、在本项目里担什么角色,再讲技术细节。
- 中文、详细但不啰嗦;技术术语第一次出现时顺带解释一句。
- 与 [[workflow-show-code-in-window]] 配合:我展示给用户敲的 .py,注释本身就得达标。
