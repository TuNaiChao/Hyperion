---
name: r3-memory-closure-handoff
description: "R3 收尾 P0 记忆闭环交付(commit d70b40d,未 push):②[a] node_report_memorize 填齐 BugLesson 4 字段 + ②[b] 确定性 recall 预注入节点 recall_lessons(6 节点图,svc.search memory-only,hybrid)+ 报告/补丁归档到 workspace。e2e GREEN(verified=True,根因准,patch apply);②[a] before/after 对照;②[b] 召回链+trigger→prepend 全验证。⚠️实测:delegate 产**次优补丁**(verified=True 但走 35s 超时兜底,非金标的落点立即释放)→ verify≠最优,需 R5 Tier1。③ opencode serve 拆出单独一轮。"
metadata:
  node_type: memory
  type: project
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-08-06T05:00:05.269Z
---

2026-08-06 R3 收尾 P0 记忆闭环交付(②[a]+②[b],**待 commit**)。闭环 [[similar-bug-recall-roadmap]] 的 P0。

## 做了啥
- **②[a]** [nodes.py](src/hyperion/workflows/bug_rca/nodes.py):`_resolve_commit_sha`(防御 git/非git)+ `node_report_memorize` 填 `symptom`(←problem_summary 或 trigger)/`fix_patch`(←patch)/`blast_radius_files`(←loc 或 evidence 去重)/`commit_sha`(←repo_root HEAD)。各归各位:ingest.py 那侧 symptom 留 R4.1.1。
- **顺带修(用户撞见)**:`node_report_memorize` 报告/补丁原只写全局 `data/bug_rca/{repo}-*`(按仓名覆盖 → 历史丢失)。改**同时写本次 workspace 的 report/patch**(manager.py 早预留的目录,之前空着)→ 每次 bug 一份归档不丢 + 全局留 latest。e2e 撞见后补的(之前跑的覆盖没了,无法恢复;今天这次已手工回填进它 workspace)。
- **②[b]**(你手敲核心 + 我补 log_hint/接线):`node_recall_lessons` 节点(`svc.search` memory-only top-K=3,失败/空→空段不阻断)+ `_build_localize_prompt(prior_lessons=)` 顶部插"先验非答案"段;[graph.py](src/hyperion/workflows/bug_rca/graph.py) 5→6 节点(`ingest→recall_lessons→delegate_localize_loop→…`);[state.py](src/hyperion/workflows/bug_rca/state.py) 加 `recalled_lessons_ctx`/`recalled_lessons`。
- **关键设计(与踩坑 #2 边界)**:②[b] **只翻记忆不定位**(opencode 仍全权自主定位),bug-rca-design §2 token取舍提示早就预留这条路;不算重造漏斗。`svc.search`(memory-only,不 bump)≠ `svc.recall`(全 4 路 + bump,MCP 工具路):被动预取 vs 主动召回语义分离。对标 deer-flow `DynamicContextMiddleware`(hybrid:预取 + 工具),但 Hyperion query 驱动 top-k 语义 > deer-flow 无 query 全量分桶。

## 验证(全 GREEN)
- **单测**:`pytest tests/` 144 passed(新增 ②[a] 4 测 + ②[b] 7 测;回归 verify-refine/report/memory 全绿);ruff 绿;6 节点图编译冒烟绿。
- **e2e**(demo2 wpa,glm-5.2 真 delegate):`verified=True`;**根因准确**(p2p_scan_work radio work 泄漏,跨层释放断裂,对得上金标);补丁 forward `git apply --check` 过;verify-refine 定位 1 轮/修复 2 轮。
- **②[a] before/after 对照鲜明**:本次 e2e 新记的 lesson 4 字段**全填**(symptom/fix_patch 4104字符/blast_radius 6文件/commit_sha 43eb8ca…);②[a] 之前的旧 lesson 全空 —— 直接证明生效。
- **②[b] 召回链**:`svc.search` 对 demo2 trigger 返 3 条相关先验(radio work 泄漏);`node_recall_lessons`(真 trigger+真 memory,非 mock)→ ctx 非空 → `_build_localize_prompt` 先验段在最前 + 含"不是答案"nudge。

## 一个观察(非 bug,设计如此 + 已在 roadmap)
**纯日志驱动 e2e(无 --trigger)→ trigger="" → `node_recall_lessons` 按设计跳过**(无线索可查,prompt 无先验段)。即 ②[b] 对"有文字线索"的 bug 立刻生效;对纯日志驱动需 **P1 自动 query 构造**(日志抽符号/现象 → query,见 [[similar-bug-recall-roadmap]] P1)才触发预注入。本次用"真 trigger + 真 memory 直接调 node"便宜补证了 trigger 非空路径(不另跑 30min e2e)。

## ⚠️ 重要实测发现:delegate 产**次优补丁**(verified=True ≠ 金标质量)
用户对比 `data/bug_rca/wpa.patch`(本次自动产出)vs `example/demo2/fix-p2p-scan-orphan-minimal.patch`(金标,已知有效):
- **根因两边判对**(一致):p2p-scan radio work 经 `scan_res_handler` 覆写误路由成孤儿,阻塞所有 STA 扫描。
- **修法点差别大**:金标在**误路由落点 `scan_only_handler`(scan.c:2458)立即释放** + `p2p_scan_res_handled` 收尾(取消 35s 超时+推进状态机),最小单点、症状根本不发生;自动产出走 **`p2p_scan_timeout`(35s 兜底超时)+ `wpas_find_stopped` abort 失败分支** —— 即 radio work **最多泄漏 35s 才回收**(这段时间扫描列表仍空),更绕、侵入更大,还有命名撞名(`p2p_scan_timeout` 回调 vs p2p.c 同名函数)/ double-release 隐患(abort 成功后再释放)/ 状态机收尾不全(`find_stopped` 分支没走 `p2p_scan_res_handled`)。
- **结论**:delegate 找到的是"**一个能 apply、方向对、但明显更弱更绕**"的修法,**不等价金标**。把永久故障降级成"每次 P2P 扫描后空 35 秒再自愈"。
- **为什么 verified=True 却次优**:`validate_patch` 只查 `git apply --check` + reverse(补丁**格式**合法),不查**语义正确/最优**。delegate 缺人类"在单一落点最小修"的洞察。这正是报告 METR 警示("verify 过 ≠ 对")+ **R5 Tier1(构建环境/repro/hwsim 测试)才能判别补丁好坏**的真实体现。
- **意义**:当前 bug-RCA 水平 = 能判准根因 + 产合法补丁,但**不保证最优**;逼近金标需 R5 Tier1 运行时验证 + 可能 Tier2 对抗审挑次优解。诚实标注而非假装达标。关联 [[pitfall-log]] [[multi-stage-delegate-decision]]。

## 改的文件(待 commit)
代码:nodes.py(②[a]+②[b] 核心+接线)、graph.py、state.py。
测试:tests/workflows/bug_rca/test_report_memorize.py(新)、test_recall_preinject.py(新)。
文档:bug-rca-design.md §1(5→6步)/§2(token提示标已落地)、踩坑记录.md #2(补注 recall_lessons 非重造漏斗)、workspace-design.md、runtime-harness-design.md、todo.md §8。
记忆:[[opencode-serve-persistent-research]](新)、backlog #55(升级)、本条、MEMORY.md。

## 下一步
已 commit `d70b40d`(2026-08-06);push 待用户拍。③ 单独一轮(地基已存)。关联:[[similar-bug-recall-roadmap]] [[r35-report-handoff]] [[delegate-already-localizes]] [[avoid-overengineering]]。
