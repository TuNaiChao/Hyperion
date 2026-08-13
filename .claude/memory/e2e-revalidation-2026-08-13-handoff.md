---
name: e2e-revalidation-2026-08-13-handoff
description: "第二轮 e2e 验证(2026-08-13 晚):upstream-merge 补 agent block 后 skill 级 e2e 全绿 + onboarding 重跑验本轮 repo_overview 截断修/多 evidence 全绿 + memory-health 重跑验 correction 闭环"
metadata:
  type: project
---

**2026-08-13(晚)**:backlog 碎片补完(碎片#2 repo_overview 大仓截断 + 碎片#3 memory_memorize 多 evidence/kind_detail)后,跑第二轮 e2e 验证三个口:**upstream-merge**(7 skill 里唯一零 skill 级 e2e,根因 agent block 漏配)+ **onboarding 重跑**(验本轮 repo_overview 截断修 + 多 evidence/kind_detail)+ **memory-health 重跑**(验 correction-link 闭环已修后走通)。**三个全绿。** 详见 [[e2e-validation-2026-08-13-handoff]](第一轮) [[upstream-merge-handoff]] [[onboarding-skill-handoff]] [[correction-link-handoff]]。

## upstream-merge:补 agent block → skill 级 e2e 全绿

**根因(踩坑#20 实证)**:upstream-merge 是 7 skill 里**唯一零 skill 级 e2e** 的。排查发现 `hyperion-upstream-merge` **agent block 漏配**(config/opencode_hyperion.json 里只有 8 个 agent,缺这个)→ opencode 找不到 agent 就 fallback 到默认 agent + MCP 工具全不注册。

**修**:补 agent block(mode=primary/steps=28/edit=deny/bash=allow,prompt 镜像 SKILL 的 8 步:确认 fork→fetch 上游→切 fork 干净态[硬门]→定范围→merge_eval[硬门]→查相关性→决策表→验证后才 memorize + 两边界:只评估不改 fork / 未验证不 memorize)。commit `6f076d2`。

**e2e 真机全绿**:建 `/tmp/upstream_merge_e2e/base` 测试仓(三分支三文件模拟 fork/upstream,三态各 1)。agent 自驱:
- **硬门守对**:merge_eval 前确认 fork checkout + worktree 干净。
- **真用 MCP 工具**:`hyperion_merge_eval` 实调用,三态 `already_fixed 1 / recommend_merge 1 / conflict 1` 与金标吻合。
- **独立实证复核**(踩坑#20 纪律):自己跑 patch-id 验 `84a27c1` 与 fork `261436b` 同为 `3c098e9d`(双验 already_fixed 非误报)。
- **相关性查对(step⑥)**:`ad6f5de` 虽 recommend_merge(能合),但 agent 查 `new_upstream_func` 在 fork 无调用者 → 正确降到「可选/弱相关」。**核心教义抓到:apply 过 ≠ fork 需要它**。
- **两边界守全**:只读(无 apply/cherry-pick/merge)+ 显式「🔒 未 memorize」。

**⚠️ 事故(踩坑#21,已清)**:建 `/tmp` 测试仓时 Bash cwd 漂到 Hyperion 主仓,`git init/commit/checkout-b` 全跑在主仓 → main 压垃圾 commit `804f66e`(测试文件 + 本不提交的 Python语法.md/todo.md)+ 多测试 fork 分支。真实历史 reflog 未丢。**修复**(用户确认后):main reset 回 `6a79586` → 删 fork → 把 `804f66e` 唯一有价值的 upstream-merge block 抠出干净重加(`6f076d2`)→ 本地文件恢复 untracked。**教训**:git init/commit 前 pwd 确认 cwd;建测试仓全程用 `git -C <path>` 不 cd;opencode e2e 必须在 Hyperion 根启动(踩坑#17/#21)。详见 docs/archive/踩坑记录.md #21。

**踩坑#17 复现 + 坐实**:opencode e2e 第一次在 `/tmp/upstream_merge_e2e`(测试仓目录)启动 → fallback 到默认 agent + `merge_eval tool not found`(工具全不注册)。opencode 读 **cwd 根** `opencode.json` symlink 才拿得到 agent + MCP。**正确姿势:在 Hyperion 根启动 opencode + 给 agent 仓库绝对路径**。这点对所有 skill e2e 都成立(onboarding/memory-health 同款)。

## onboarding 重跑:本轮两修全验证

**验证本轮两个修复**(碎片#2/#3,commit `5659768`):

1. **repo_overview 746 社区大仓不爆截断** ✅ — agent 跑 repo_overview(codebase=wpa),Header 诚实报 `746 communities (本调用含 30 / 共 746)`,**输出顺序 hub → bridge → warnings → 耦合边 → communities(最末)**,截断提示明确。**丢的只是社区明细,架构关键全在前** —— 这正是碎片#2 重排 body 顺序的目的(旧版末尾的 hub/bridge 被 communities 撑爆 8000 截断静默丢;新版 communities 移末尾,即便截断丢的也是社区清单)。

2. **架构事实带多 evidence + kind_detail=architecture** ✅ — DB raw 查 memorize `74c2aeea718eff54`:`kind_detail=architecture`(透传成功,旧版全落默认 module)+ **evidence 7 条**(每条 file:line+snippet,旧版 evidence=[] 空)+ confidence=0.85。**两个渣症都治好**(上轮 e2e 暴露的 evidence 空 + kind_detail 全 module)。

**流程质量**:recall 命中上轮 `7add3ff5`(短路判定正确,但任务要求验证故重跑)→ repo_overview 746社区/15hubs/15bridges + repo_map PageRank 俯瞰 → 主旅程 STA 连接 WPA2 AP 六步(connect→associate→start_assoc_cb→event→event_assoc→set_state)每步 read 坐实 → export_report + memorize(读码即记)。

**Agent 自己抓到一个真观察**:图索引路径(`example/demo2/wpa` demo 副本)与真实源码(`/home/tnc/src/hostap`)是**两个不同 checkout,行号偏移**(索引说 associate 在 1931、真实 2843)。Agent 正确处理:**索引图作结构参考,所有行号 read 真实路径坐实** —— 这正是导览防幻觉的核心。建议(低优):后续可考虑用真实路径重索引让图与读码行号对齐。

## memory-health 重跑:correction 闭环验证

**e2e 全绿**,agent 审 wpa 记忆库 77 条(51 active + 11 STALE + 4 CORRECTED + 余被取代),**④ 未决矛盾 = 0 条**(上轮是两派打架根因,本轮纠正链闭环后归零)。验证上轮 correction-link 修复(corrects/corrected_by 双字段 + 检索降权 0.3× + `_render_audit_card` 加 CORRECTED 标记)端到端走通。

**用户三个验证点全 ✅**:
1. **纠正链正确标上** ✅ — DB raw 查证非幻觉:A 派误诊 4 条(`b448561a`/`4f739d5a`/`8de9ae88`/`b1e79133`,P2P StopFind abort-failure 误诊)**全部 `corrected_by=d0a311d0`**;B 派 `d5ad928d` summary 明写「【纠正先前 abort-failure 误诊】」+ 锚 scan-only 覆盖竞态。降权 0.3× 生效 → 两派矛盾从「未决」变「已闭环」。**这正是上轮 correction-link 修复想达到的效果**(上轮 e2e 发现两派打架 → 补双字段 → 真数据重放 corrected_by 0→4 → 本轮体检正确读出闭环)。
2. **体检只读不改** ✅ — 全程仅 memory_dump(3 次翻页摊全 51 条),未删/未改 confidence/未 consolidate,未调任何写工具(比 onboarding/compare 更严的边界守住了)。
3. **默认不 memorize** ✅ — 未决矛盾=0,无新知识需记,不 memorize(守边界)。

**Agent CORRECTED 标记正确显示** —— 这是上轮 bug 1 修复(`_render_audit_card`/`RecallHit.render` 加 CORRECTED 标记)的直接体现:体检报告正确显示「4 条 CORRECTED by d0a311d0」,上轮「id 不渲染 + 前缀不匹配」两 bug 修后链路全通。

**Agent 抓到的真健康信号**(非 bug,体检只读只建议):
- **P1 空卡 `0de99bc3`**:conf=0.75 但 summary 空 + 无锚点 + 无 sha(像写入时内容丢失),建议人工核处。
- **P2 闭环缺口 `aaed2af4`**:被 `d0a311d0` 明言取代但**仍 active 未标 invalid**(闭环操作没做完)→ 建议人工补标。Agent 正确归入「③应过期未过期」而非「④未决矛盾」(`d0a311d0` 明确承认 `aaed2af4` 曾正确现已过时,是闭环操作漏标不是认知矛盾)—— **这个区分很精确**。
- **P2 待 consolidate 8 条**:同主题(P2P scan radio work 泄漏)探索中间态卡 conf=0.35 但高 hits(7-8 次),建议合并到已闭环正确根因。
- **P3 取代链清理**:11 条 STALE + `7add3ff5` 漏标(被 `74c2aeea` 新版架构导览取代)。

**总体健康结论**:记忆库溯源质量中等偏好(多数 codebase_fact 带 evidence+sha);核心矛盾已闭环、纠正机制生效;主要遗留是 1 空卡 + 1 闭环缺口 + 一批待 consolidate 探索卡,均不影响当前 recall 正确性,只持续消耗检索噪声。改库是人的活(体检只读只建议,边界守对)。

## 改的文件(本轮)

- `config/opencode_hyperion.json`(commit `6f076d2`)—— 补 `hyperion-upstream-merge` agent block。
- `docs/archive/踩坑记录.md`(commit `bcf731d`)—— 记踩坑#21(测试仓 git 污染主仓)。
- `.claude/memory/MEMORY.md` + `pitfall-log.md`(commit `bcf731d`)—— 索引 + #21 摘要。

## 验证

- 三个 skill e2e 全绿(upstream-merge / onboarding / memory-health),opencode 在 Hyperion 根启动 + 给 agent 仓库绝对路径(踩坑#17/#21)。
- onboarding memorize `74c2aeea` 真 DB 查证(kind_detail=architecture + 7 evidence),非幻觉。

## 不做(YAGNI)

- 不用真实路径重索引 wpa 图(agent 已正确处理行号偏移:图作结构参考 + read 坐实;重索引是低优,等需求)。
- 不给 upstream-merge 建 `fetch_upstream_commit` 工具(踩坑#2:agent `git show` 等价)。

关联 [[e2e-validation-2026-08-13-handoff]] [[upstream-merge-handoff]] [[onboarding-skill-handoff]] [[correction-link-handoff]] [[backlog-fragments-handoff]] [[pitfall-log]](#21 测试仓 git 污染主仓) [[opencode-mcp-wiring]](#17 opencode cwd 根)。
