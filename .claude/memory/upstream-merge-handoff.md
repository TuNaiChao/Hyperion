---
name: upstream-merge-handoff
description: 上游 commit 合入评估(merge_eval 工具 + upstream-merge skill,第 13 MCP 工具)2026-08-11 完成交接
metadata:
  type: project
---

**2026-08-11 低优 backlog #1「上游 commit 合入评估」完成,代码完未 commit**。CLAUDE.md 低优 backlog 标 ✅。

## 需求(用户定方向)

维护系统软件 fork(wpa/bluez),上游持续出修复/安全补丁,要判断哪些该 backport —— 三态决策:**已修→不合 / 还在且相关→建议合 / 冲突大→人工**。用户原话:"分析上游 commit,判断是否值得合入当前 fork 的仓";方向定调:**先把上游拉到本地,再本地逐个分析每个 commit**。

## 调研背书

- **机制**:`git patch-id` / `git cherry` 是「已修」检测黄金标准(对 diff 算哈希,免疫空白/commit message 变更;backport 改了补丁文本才漏检 → 那时需 LLM 语义判断)。
- **分层**:确定性地板(patch-id/apply)+ LLM 天花板(相关性推理,VeriPort/PortGPT 自动 backport 印证)。"能 apply ≠ fork 需要它"。
- deer-flow 无 git/patch 工具,N/A。

## 设计(pivot 对齐:1 薄工具 + 1 skill)

**两个新东西,重活归 agent**(不包 `fetch_upstream` 工具 → agent `git show` 等价,踩坑#2;不建 workflow → orchestrator 已降级):

1. **`merge_eval` MCP 工具**([code_graph.py](../../src/hyperion/services/code_index/code_graph.py) **模块级函数**,cross_version_diff 姐妹,git 为核图可选):
   ```
   merge_eval(upstream_base_ref, upstream_head_ref, *, fork_ref, repo_path,
              concern_files=None, max_commits=50, graph=None) -> dict
   ```
   逐个 upstream commit 三态判定(**确定性地板,不判相关性**):
   - **already_fixed**:patch-id 等价于 fork —— `git log --cherry-pick --right-only fork...upstream_head` 的 shown = 没等价进 fork 的候选;`equiv = commit_shas − shown`(取反集)。
   - **conflict**:不等价 且 `git apply --recount --check`(strict 一步)对当前 worktree 失败。
   - **recommend_merge**:不等价 且 apply 过。
   - **uncertain**:apply 检查 subprocess 异常。
   - CRG 可选:`touched_functions`(≤12 commit 才跑,`parse_git_diff_ranges`+`map_changes_to_nodes`)。
   - 返回 `{commits:[{sha,subject,equivalent_in_fork,applies_cleanly,touched_files,touched_functions,state}], summary:{total,already_fixed,recommend_merge,conflict,uncertain}, note}`。
2. **`upstream-merge` skill**([.claude/skills/upstream-merge/SKILL.md](../../.claude/skills/upstream-merge/SKILL.md)):面向模型指令性(踩坑#13);8 步(确认 fork → 拉上游本地 → checkout fork_ref+干净 → 定范围 → merge_eval → 查相关性 → 决策表 → 用户验证后才 memorize);2 边界(只评估不修改 / 未验证不 memorize);`bash` 只许读类 git(`fetch` 拉上游除外)。

## ⚠️ 关键 gotcha:patch-id 等价检测用「取反集」非「收集 = 标记」

设计初稿想用 `git log --cherry-mark` 收集 `=` 标记。**3 个 git 探针证伪**:git 2.x 的 `--cherry-mark`/`--cherry-pick`/`git cherry`/对称差 patch-id **都是把等价 commit 从 `fork...upstream` 对称差里直接剔除,不标 `=`**。故正确逻辑 = **取反**:`shown = git log --cherry-pick --right-only --format=%H fork...upstream_head`(还在 fork 外的候选),`equiv = 范围内 commit_shas − shown`(被剔除的 = 已修)。比逐个算 patch-id 高效(只走对称差不扫 fork 全史)。

## 复用 + 抽取

- 抽 module-level `_run_git(repo, args, *, timeout) -> str`(cross_version_diff 原内部 `_git` 闭包提上来,cross_version_diff + merge_eval 共用,消重复)。**先抽再改 cross_version_diff 调它,保回归绿**(cross_version_diff 测全过)。

## 探针结果(三态质量已证)

- **hermetic** `test_merge_eval_three_states`:真 git 仓,造 main+upstream(U1/U2/U3)+fork(cherry-pick U1 + 改 b.py 与 U3 冲突)。U1→already_fixed(cherry-pick 等价检出)/ U2→recommend_merge(新文件干净 apply)/ U3→conflict(b.py 上下文冲突)+ summary 计数 + CRG touched_functions 全对。
- **真仓 wpa**(`release/eagle` fork vs `origin/master`):range 88e66b5..origin/master(3 commit)→ **全 3 正确判 already_fixed**(fork 历史含 master,patch-id 等价检出;applies=False 合理因补丁已在 fork → apply 必败,但 equiv 优先 → already_fixed 非 conflict,三态优先级对)。**注:demo 数据方向是 fork 领先上游 25/0**(无 recommend_merge/conflict 可探 → 那两态靠 hermetic 覆盖)。

## 验证(全绿)

`uv run pytest tests/test_code_graph.py tests/test_mcp_tools.py -v -k "not kind_filter"` → **35 passed, 1 deselected**(kind_filter 挂真 DashScope 网络,无 key 跳过)。含 3 code_graph 测 + 3 MCP 壳测(bad_ref/not_a_repo/success_via_fake)。ruff 干净(修了 5 个 E702 分号)。

## backlog / 故意不做(YAGNI)

- apply 检查对 **worktree**(MVP)→ 生产级升 `git merge-tree --write-tree`(git 2.38+,不 touch worktree、可并发、对脏 worktree 安全)= **[backlog #60](backlog-production-grade.md)**。触发条件 = 需并发多仓 / fork_ref 不能 checkout。
- 不包 `fetch_upstream_commit` 工具(agent `git show` 等价,踩坑#2)。
- 不建 workflow(pivot 已降级 orchestrator)。

## 测试 gotcha

- `uv run` 串行跑(后台并行争同一 .venv);**cwd 漂**(探针 cd 进子仓后 `uv run` 相对路径失效)→ 探针/调用一律用绝对 `repo_path`,或先 `cd /home/tnc/Desktop/Agent/Hyperion`。
- `test_memory_recall_kind_filter` 挂真 DashScope 网络 → 无 key 环境用 `-k "not kind_filter"` 跳过。

关联 [[toolset-after-audit-2026-08-10]] [[pitfall-log]] [[route3-cross-version-handoff]](cross_version_diff 姐妹) [[avoid-overengineering]] [[skill-prompt-writing-style]]。
