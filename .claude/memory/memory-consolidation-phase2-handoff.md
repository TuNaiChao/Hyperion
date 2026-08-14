---
name: memory-consolidation-phase2-handoff
description: 2026-08-14 consolidate Phase 2(B3 已合入上游 + B4 过期)落地;e2e 抓 2 真 bug(标签竞写+矛盾误报)同修
metadata: 
  node_type: memory
  type: project
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-08-14T06:26:42.602Z
---

# consolidate Phase 2 交接(B3 + B4,五 pass 收口)

**commit**:`32d5cfd`(feat)+ `84983db`(roadmap 状态)。**测试**:全套 274 绿 + ruff clean。**e2e**:真 DB(data/memory/memory.db,种子 codebase=e2e_phase2_test 用后即清)+ 真 git 仓(/tmp/e2e_b3_repo,原始 commit + 守卫行写进工作树不 commit = "已合入"态)。

## 落了什么

consolidate 五 pass 收口:① promote mental_model ② 矛盾检测(needs_review)③ 语义去重候选 ④ **B3 已合入上游** ⑤ **B4 过期**。

- **B3**:bug_lesson 带 fix_patch → `git apply --check --reverse`(LF 归一化,踩坑#15)判改动是否已在仓里 → 打 `merged_upstream` 标签 + confidence×discount(config `merged_upstream_discount` 默认 0.5)。**只标不删不 set_invalid**:`invalid_at` 语义是"知识错了"不是"bug 修了"(考古要靠);reverse 只证改动在树里可能是等价修复 → 留人在环。
- **B4**:`last_recalled`(或 `created_at` 取较晚)超 `stale_after_days`(config 默认 365)→ 打 `stale` 标签。**只标不降权**:recall 打分已有 exp 衰减,consolidate 再降是双杀。
- **计数=当前态**(重跑统计稳定,体检要总数),**写入=幂等**(标签加一次、打折一次,防 confidence 被反复折到 0)。
- `repo_path` 只在显式 consolidate(CLI `--repo-path`)传;recall 自转(_safe_consolidate)不知道仓在哪不猜 → B3 只在显式路径跑。
- 与原 roadmap 两处**实质偏离**(已在 roadmap B3/B4 小节写"偏离记录",非静默漂移):B3 不 set_invalid 改标签+打折;B4 不降 conf 改只标。

## e2e 抓到的 2 真 bug(单测全绿也拦不住,自跑 e2e 的价值)

1. **标签竞写(严重)**:五 pass 共享一份 `list_items` 快照,pass ④ 打的 `merged_upstream` 不在 pass ⑤ 拿到的快照里 → pass ⑤ `set_tags([*it.tags,"stale"])` 整体覆盖写回把 ④ 的标签洗掉。**连带双杀**:标签是 pass ④ 的幂等守卫,被洗掉后重跑会再打折(0.25→0.125)。修:提 `_add_tag` helper(写前 `store.get` 重读 DB 最新 tags 合并,pass ①②③④⑤ 统一走它)。锁测:`test_consolidate_tags_not_clobbered_across_passes`(既合入又过期 → 双标签共存)。
2. **矛盾误报**:Phase 1 加的"symptom 空 → 回退 evidence 同文件=同主题"把 scan.c:2 溢出和 scan.c:3 越界误判矛盾(同文件≠同 bug,wpa 的 scan.c 几十个 bug)。修:`_same_subject` 回退收紧为**同文件且行号差 ≤5**(`_NEARBY_LINE_WINDOW`,同 bug 两派诊断锚同一处,不同 bug 至少隔函数体);相邻不同 bug 仍可能误报 → "只标不裁"人在环兜底。锁测:`test_same_subject_nearby_lines_only`(远行号不加矛盾数)。

**教训**:多 pass 竞写同一列时,set 整列的操作必须写前重读(或 SQL 原子 append);启发式回退要防"过宽"(Phase 1 那个回退本身就是 e2e 抓漏报的修复,这次同一回退又制造误报——启发式要两头卡边界)。

## 相关

- Phase 1(矛盾+去重):[[memory-consolidation-phase1-handoff]]
- 路线图与偏离记录原在 docs/memory-module-roadmap.md(**该文件 Phase 3 后已删**;偏离记录要点已并入 docs/memory-module-analysis.md §7)
