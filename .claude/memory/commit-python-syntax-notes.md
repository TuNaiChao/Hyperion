---
name: commit-python-syntax-notes
description: "Python语法.md 不提交(2026-07-31 再次反转)—— 别 stage/commit 它;规则历史反复,以最新为准"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-07-31T02:12:43.098Z
---

**2026-07-31 决定:以后不提交 `Python语法.md`。** 它是用户个人的 Python 语法笔记,不进版本库。它会一直挂在 `git status` 的 unstaged 改动里(显示 `M Python语法.md`)—— **这正常,别去 stage / commit / "清理"它**。

**Why:** 个人笔记,不需要跨机同步/进 git 历史。这条规则**历史多次反复**(早期"不提交" → 2026-07-24 改"都提交" → 2026-07-31 改回"不提交"),**永远以用户最新一次指示为准**;若与旧记忆冲突,信最新的。

**How to apply:** commit 时**用显式文件路径** stage(如 `git add src/... tests/...`),**别用 `git add -A` / `git add .`**,否则会把 `Python语法.md` 带进去。提交前扫一眼 `git diff --cached --name-only` 确认没有它。要彻底让 git 忽略它可 `git rm --cached Python语法.md` + 加 `.gitignore`,但那会让它退出版本库,**需用户先确认**,别擅自动。关联 [[workflow-show-code-in-window]]。
