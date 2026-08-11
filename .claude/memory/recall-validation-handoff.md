---
name: recall-validation-handoff
description: backlog #59「recall→定位 价值」验证实验结论(2026-08-11)——正向:类似 bug recall 3-4× 提速收敛
metadata:
  type: project
---

**2026-08-11 backlog #59 验证完成:recall 一个"类似 bug"的教训 → 定位 证实有增益(N=1/臂)。结论与老 #59 的偏负实测不矛盾——区别在"类似 bug"(增益)vs"同 bug"(冗余/锚定)。代码完未 commit(纯验证,无产品代码改动)。**

## 为什么做(背景)

#59 自标 recall→定位 是"未证实的假设"(②[a] 填字段 + ②[b] 确定性预注入节点 ahead of validation 做出来了)。现有实测全在 **demo2 同一个 bug** 上:机制成立(recall 返回先验)但**没增益**(delegate 照样 26 read+17 grep、撞步数上限、产次优补丁)。同 bug 是 recall 价值**最弱**场景(先验=上次答案 → 冗余或锚定)。recall 真正卖点——**类似 bug** 模式迁移——**从没测过**。本实验补这一格。

## 第二个 bug:Candidate A(公平素材,已挖到)

demo2 同类、独立的真实代码实例:`interworking_select` 在 p2p-scan 在途时**无条件覆写** `scan_res_handler`(`wpa_supplicant/interworking.c:3157`)。
- **同类**:demo2 = D-Bus `Interface.Scan` 覆写成 `scan_only_handler`;Candidate A = `INTERWORKING_SELECT` 覆写成 `interworking_scan_res_handler`(`interworking.c:3137`)——两者都**不释放 `p2p_scan_work`** → 同一个 radio work 孤儿。
- **独立**:demo2 修在 `scan_only_handler`;Candidate A 要修在 `interworking_scan_res_handler` 或 `events.c` 中央派发——demo2 补丁**不覆盖**。现仓 Candidate A 未修(探针核实)。
- **公平**:真实代码/可达路径/症状;demo2 教训给的是**模式**(共享单槽→并发覆写→受害 p2p_scan_work 泄漏),不是答案(agent 仍要自己找 interworking 触发点 + 该 handler 不释放)。

**oracle**:根因 = INTERWORKING_SELECT 并发覆写 scan_res_handler → p2p 扫描完成事件误路由到 interworking_scan_res_handler → 不释放 p2p_scan_work → p2p-scan radio work 泄漏阻塞队列;正确补丁 = 释放 p2p_scan_work(interworking handler 末尾 或 events.c 中央派发)。

## 方法

- **隔离**:记忆硬按 `(owner, codebase)` 分区;bug-RCA 的 codebase = `Path(repo_root).name`。两臂 = 同一份 wpa 代码 `cp -r` 到 `data/exp/demo2_base`(空 scope)与 `data/exp/demo2_rec`(种教训);delegate 自召回 MCP 也读同 scope → baseline 自召回返空(混淆自洽)。`HYPERION_CODEBASE = workspace 目录名 split "__"`(核实无泄漏)。**勿用现成 `wpa` scope**(已有 47 条会污染)。
- **检索闸(Phase 0,先决)**:种教训到 demo2_rec → 用 Candidate A 的 problem_summary 跑 `svc.search(top_k=3)`:教训进 top-1,**score=0.0279**。**校准**:同 scope 下近 verbatim 查询也 = 0.0279(检索器上限),无关 TLS 查询 = 0.0139(2× 差距)→ 0.0279 不是"弱匹配"是上限,语义桥成立。闸过。
- **路径**:`hyperion bug-rca`(orchestrator,确定性 recall 节点 `recall_lessons`+`recall_for_repair` 硬注入 prompt)——干净测试床。自适应:每臂 1 轮,模糊才升 2 轮。
- **教训种法**:`hyperion memory add --kind bug_lesson --repo demo2_rec --summary/--root-cause`(手写高质量泛化版,捕捉模式)。

## 结果(对比表)

| 指标 | baseline(空记忆) | recall(有 demo2 教训) |
|---|---|---|
| 根因(评分表) | **CORRECT**(命中 interworking 覆写 + p2p_scan_work orphan;3 点证伪全过) | **CORRECT**(同样命中;证伪查 3 条 rescue 路径全否) |
| 补丁(评分表) | CORRECT——trigger 点预防式 `radio_remove_works("p2p-scan")`(interworking.c) | CORRECT——**中央派发兜底**释放 p2p_scan_work(events.c scan_work_done,**类级修复**,连 hs20 等同类都治,略胜) |
| verified | True(apply+自审) | True |
| 定位 loop | **2 轮** | **1 轮**(直接收敛) |
| steps(step_start) | 32 | 11(**3×↓**) |
| tool_calls | 50(定位类 48) | 25(定位类 23)(**2×↓**) |
| tokens in / out | 390K / 9.6K | 91K / 5.6K(**4×↓**) |
| 自召回 hyperion_memory_recall | 1(空 scope→空操作) | 1(拿到教训) |

报告/补丁落点(本地参考,`data/` gitignored):`data/bug_rca/demo2_base-rca.md`、`demo2_base.patch`;`demo2_rec-rca.md`、`demo2_rec.patch`。解析脚本 `/tmp/parse_metrics.py`(扔的)。

## 结论:#59 证实(正向)

**recall 类似 bug 教训 → 定位有增益**:不是帮"做对"(baseline 读代码自己就读对了),而是帮**快**——3–4× 省 token/步数,1 轮收敛(baseline 2 轮),质量持平甚至略胜(recall 臂选了类级中央修复)。打到 recall 真正卖点(类似 bug 模式迁移),解释了老矛盾:**同 bug recall 偏负**(先验=上次答案→冗余/锚定)vs **类似 bug recall 增益**。

## 决策 + 局限

- **recall 保留并继续投**;B(P1 自动 query)**值得做**(recall→定位 这条链证实有价值,自动构造 query 解锁"定位后才有的 problem_summary 当 query"的增益)。
- **局限(诚实)**:① **N=1/臂**——delegate 非确定性,3–4× gap 够大方向对,但单轮。**建议真上 B 前补 1 轮确认**(再跑 baseline+recall 各 1,看臂内一致性)。② **教训是手写高质量泛化版**——真实场景来自上次 bug-RCA 的 BugLesson(②[a] 填 symptom/fix_patch/blast_radius/commit_sha),质量可能差;但检索闸证召回成立,内容有代表性。③ 只测了 1 个"类似 bug"(Candidate A);跨 bug 类型/跨仓的泛化未测。
- **不改产品代码**(纯验证)。Candidate A 本身是个**真实的 wpa bug**(interworking_select 无 p2p 守卫 → p2p-scan 泄漏),补丁可单独提(两臂都给了有效修法)。

## 不做(YAGNI)

- 不为实验建永久 harness/多 bug 数据集——一次定性够答 #59;要规模化另议。
- 不在实验里双变量改 recall 算法再测。

关联 [[similar-bug-recall-roadmap]] [[r3-memory-closure-handoff]] [[multi-stage-delegate-decision]] [[pitfall-log]](#11 误诊/锚定) [[avoid-overengineering]]。
