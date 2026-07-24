---
name: commit-python-syntax-notes
description: "Python语法.md 要随 git 提交(2026-07-24 用户反转了之前的\"不提交\"约束)"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-07-24T10:04:12.975Z
---

`Python语法.md`(用户的 Python 语法个人笔记)**要随项目 git 提交**。用户 2026-07-24 明确说"以后都要提交",反转了之前的"始终不提交"约束。

**Why:** 用户接受它进 git 历史 + push GitHub——两台机(Linux+macOS)跨机同步的需求优先于"个人笔记不入库"。

**How to apply:** 提交检查点时把 `Python语法.md` 一起 `git add`,不再排除。push 仍按"对外动作单独确认"原则([commit-python-syntax-notes] 本身不授权 push)。关联 [[workflow-show-code-in-window]]。
