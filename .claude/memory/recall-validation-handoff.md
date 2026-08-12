---
name: recall-validation-handoff
description: "backlog #59「recall→定位 价值」验证实验结论 —— ⚠️2026-08-12 N=2 修正:N=1 的\"recall 3-4× 提速\"未复现,不稳健,delegate 运行间噪声主导"
metadata: 
  node_type: memory
  type: project
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-08-12T08:12:03.385Z
---

**⚠️ 2026-08-12 N=2 复核修正:N=1 的"recall 类似 bug 教训 → 定位 3-4× 提速"结论被推翻。** N=2 两臂都 1 轮定位收敛,baseline 自身从 32 步掉到 17 步(臂内方差 = recall 边际效应),recall 反而 26 步 + verified=False(假阴性)。recall 对定位轮数有时帮有时不帮,gap 压不过 glm-5.2 运行间噪声。**原 N=1"3-4× 提速"表述作废**;recall 保留(无害+质量持平),B(P1 自动 query)降级(真上前需 N≥5 定性)。详见下方「N=2 修正」段。**2026-08-11 原 N=1 结论(作废,留档):recall 一个"类似 bug"的教训 → 定位 证实有增益。**

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

## 结果(N=1 对比表,⚠️ 2026-08-12 N=2 后此结论作废,见下方修正段)

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

## 结论(2026-08-12 N=2 复核后修正):#59 **不稳健**,N=1 的正向是被 glm-5.2 运行间噪声放大了

**⚠️ 修正:N=2 推翻了 N=1 的"recall 快 3-4×"结论。** recall 类似 bug 教训 → 定位**没有稳定增益**;两轮合看,delegate 运行间方差远大于 recall 的边际效应。原 N=1 的"3-4× 提速 / 1 轮收敛"表述**作废**。

### N=1 vs N=2 对照(同一 bug、同一 trigger/日志)

| 指标 | baseline N=1 | baseline N=2 | recall N=1 | recall N=2 |
|---|---|---|---|---|
| 定位轮 | 2 | **1** | 1 | 1 |
| 修复轮 | — | 1 | — | 2(未收敛 verified) |
| steps | 32 | **17** | 11 | **26** |
| tool_calls | 50 | 42 | 25 | 50 |
| tokens in | 390K | **298K** | 91K | **277K** |
| verified | True | True | True | **False(假阴性)** |
| 根因 | CORRECT | CORRECT | CORRECT | CORRECT |
| 补丁 | 两路守卫 | 两路守卫 | 中央兜底(类级) | 中央兜底(类级,同 N=1) |

### 三个诚实结论

1. **定位速度持平**:N=2 两臂**都是 1 轮定位收敛**。N=1 那"recall 1 轮 vs baseline 2 轮"的差距没复现——baseline N=2 自己也 1 轮了。recall 没把"1 轮收敛"这个优势**稳定**兑现。
2. **N=1 的 3-4× 提速主要是 baseline 那次跑得异常慢(32 步)**:baseline 自身 N=1→N=2 从 32 步掉到 17 步(几乎折半),这个**臂内方差**(15 步)跟 recall 的臂内方差(11→26,差 15 步)一样大。recall 的"增益"被 baseline 的噪声吃掉了。
3. **Round 2 recall 反而更慢 + verified=False**:修复阶段走了 2 轮(iter0/iter1 都 needs_fix,没收敛到 verified)。verified=False 是**假阴性**——落盘的 demo2_rec.patch(27 行,events.c scan_work_done 中央兜底)**有效且 `git apply --check` 干净通过**;报告判空是 verify-refine 某轮里 workspace_changes 观察 git diff 捕到了空(delegate 换策略中间态空)。根因和补丁**质量都判对**(补丁仍是更优的类级中央修法,跟 N=1 recall 同款)。

### 老矛盾不再成立

N=1 解释的"同 bug recall 偏负 vs 类似 bug recall 增益"——N=2 后**类似 bug recall 也不稳定增益**。更像:**recall 对定位轮数有时帮、有时不帮,gap 不足以压过 delegate 的运行间噪声**。

## 决策 + 局限(N=2 修正后)

- **recall 保留**(无害:两轮都没让它判错根因,补丁质量持平甚至略优——类级中央修复两轮都选了);但**不再宣称"提速"**。
- **B(P1 自动 query)降级**:recall→定位 这条链的价值**未达"值得专建"的门槛**(N=2 没稳定兑现增益)。做 B 的依据从"证实有增益"降为"无害 + 有时帮 + 自动 query 本身有其他好处(定位后才有的 problem_summary 当 query)"。**真要做 B 前应先 N=3-5 定性**,别据 N=1 单轮就上。
- **局限**:① **N=2/臂仍小**——glm-5.2 非确定性 + verify-refine 多轮随机性大;要稳需 N≥5。② verified=False 假阴性暴露 **workspace_changes 观察 patch 有捕空 bug**(delegate 换策略中间态空 → 报告误判 patch 为空)——这是**真 bug**,值得单独排(踩坑#15 延伸:不只 LF marshalling,还有观察时机)。③ 仍只测 1 个类似 bug(Candidate A)。
- **不改产品代码**(纯验证)。Candidate A 本身是真实 wpa bug,两轮四臂都判对根因、补丁可单独提。

## 价值命题修正(2026-08-12 设计复核,详见 [[memory-design-review-2026-08-12]])

N=2 触发了对记忆设计的整体复核(voxos/mem0/arXiv 2025-2026 最佳实践背书)。**核心认知修正:recall 的成功指标从来就不该是 step-count/定位轮数**,那是错配的指标(N=2 证伪 + lost-in-the-middle 可解释:预注入先验塞进 delegate 长 prompt 中段被注意力稀释)。recall 的真价值是**定性**的:

1. **防重复误诊** —— 踩坑#11:glm-5.2 系统性把根因误诊成显眼日志行。一条"这行日志是红鲱鱼,真根因在更早的 scan_res_handler 误路由"的教训,能拦住下次再犯(评估口径 = 有没有拦住一次错判,不是 token 数)。
2. **带架构事实**(codebase_fact)+ **团队共享教训** —— 跨会话/跨人复用,这才是记忆相对 delegate 单次推理的差异化。

**复核同时发现一个真 bug 并已修(B)**:`memory_recall` MCP 工具调错了 `svc.recall()`(混合检索,会返 code_index 的代码 chunk),与其 docstring(翻长期记忆)矛盾 + 与 `search_codebase` 职责重叠(踩坑#2 变体)→ 改走 `svc.search()`(memory-only)。这本身也是 recall 信号被吃的一个来源(无关 code chunk 稀释记忆)。**注意:orchestrator 预注入节点早调 `svc.search()` 已对,C(注入方式)原担忧不存在 → C 并入 A 不立项。**

**后续评估 recall 价值,用定性(防误诊/带事实),别再据 step-count。**

## 2026-08-12 compare 正例:recall 增益取决于「注入层是否短路」(本条修正上方「recall 无稳定增益」的适用边界)

bug-RCA 测出「recall 无稳定 step-count 增益」后,在 **compare 跨版本对比 skill** 上测出了**反例 —— recall 有巨大增益(-90% 工具 / ~7× 提速)**。关键差异**不在 recall 本身,在注入层**:

- **bug-RCA(上面测的)**:recall 命中的是**线索**(类似 bug 教训),不能短路流程,只能辅助定位 → delegate 仍要自己读码推理 → step-count 增益被噪声吃掉。**结论「无稳定提速」对「线索型 recall」成立。**
- **compare**:recall 命中的是**结论**(完整的流程对比事实,带双源 file:line,可直接复用)→ **能短路**。但首次 e2e 暴露注入层 gap:SKILL 写成固定 7 步流水线,agent recall 命中后仍整轮重跑(42 工具/read×22,与冷路径几乎一样,agent 自述「按流程需要重新验证」)。**修法:SKILL + prompt 显式写「recall 命中→短路,不重跑」** → 改后 4 工具/read×0/~40s 出报告(质量不降,复用记忆)。

**核心教训(给所有带 memory_recall 的 skill)**:**「记忆召回」≠「记忆被用」**。recall 工具命中只是召回层工作;**注入层**(skill/prompt)必须显式写「命中→短路/复用」分支,否则 agent 把固定流程当流水线走完,记忆召回了但不影响行为。判据 = **recall 命中的是线索还是结论**:线索型(bug-RCA/backport/patch-review/upstream-merge 的同类历史)不能短路,只能当定位辅助,无 step-count 增益属正常;结论型(compare 的对比事实)能短路,增益巨大,但**必须显式写短路指令否则白搭**。详见 [[compare-skill-handoff]]。

## 不做(YAGNI)

- 不为实验建永久 harness/多 bug 数据集——一次定性够答 #59;要规模化另议。
- 不在实验里双变量改 recall 算法再测。
- 不抄 markdown-only 记忆(丢结构化/分区/溯源 = 丢 Hyperion 灵魂);不为"提速"重跑 N=3-5(指标错配)。

关联 [[similar-bug-recall-roadmap]] [[r3-memory-closure-handoff]] [[multi-stage-delegate-decision]] [[pitfall-log]](#11 误诊/锚定) [[avoid-overengineering]] [[memory-design-review-2026-08-12]]。
