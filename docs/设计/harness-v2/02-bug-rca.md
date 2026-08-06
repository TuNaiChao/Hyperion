# 02 · bug-RCA 设计(harness 转向后)

> 取代:`docs/设计/bug-rca-design.md` 的 orchestrator(六节点固定管线)设计 —— 那是 pre-pivot 产物,
> 已降级为**参考实现**(代码留 [workflows/bug_rca/](../../../src/hyperion/workflows/bug_rca/),docstring 标 post-pivot 参考,
> CLI `hyperion bug-rca` 留兼容 + deprecate 提示)。本文档是 bug-RCA 的**当前主路径设计**。

## 主路径:opencode + bug-rca skill + 6 个 hyperion 工具,agent 自驱

不再走固定六节点管线(`ingest→recall_lessons→delegate_localize_loop→assemble_repair→
delegate_repair_loop→report_memorize`)。改成:**opencode 自己驱动**,Hyperion 只递工具(记忆/代码情报/
日志/影响面/补丁验证)+ 菜谱(skill)。agent 干它擅长的(读码推理 + 改代码),Hyperion 不重复定位(踩坑 #2)。

### skill:[bug-rca/SKILL.md](../../../.claude/skills/bug-rca/SKILL.md)(7 步,2 硬门)
1. **先 recall** `hyperion_memory_recall(线索)` —— 翻历史同类 bug 教训。**先验非答案**,与证据矛盾以证据为准。
2. **语义搜入口** `hyperion_search_codebase(概念)` —— 用概念别盲 grep;拿真实 file:symbol:line 锚点再精读。
3. **过滤日志** `hyperion_filter_logs(path, since/until=故障窗)` —— 别读全量。
4. **立假设 + 证伪** —— 根因(why 句 + file:line)+ 主动找推翻自己的反例。
5. **查 blast-radius** `hyperion_blast_radius(要改的文件)` —— 动手前看连带影响。
6. **改代码 + validate【硬门】** `hyperion_validate_patch(补丁, repo_path)` —— apply 不了不算修复。
7. **memorize【硬门】** `hyperion_memory_memorize(kind=bug_lesson, ...)` —— 沉淀教训(不记=白干)。

advisory(灵活自纠)但 **mandate 两道硬门**(validate / memorize)。确定性靠 **skill 强制步骤 + 工具硬门**,不靠固定图。

### agent enforcement:[hyperion-bug-rca](../../../config/opencode_hyperion.json)(opencode 专用 agent)
skill 是 advisory,glm-5.2 不一定严格照走(实测见下)。所以用 opencode 专用 agent 做 enforcement:把 7 步 playbook **烙进 system prompt**(常驻 > 等 skill 自动触发)+ `steps=25`(给够走完)+ 把 validate/memorize 写成**"不做完不算完成 + 步数将尽优先做这两步"**。permission:`edit/bash/read/grep/glob/list/hyperion*/skill` allow。

> skill(方法论,可移植到任何 agent)与 agent(enforcement,opencode 专属)正交。两者协同:skill 是源,agent 是 opencode 上的强制壳。

### 工具(6 共享,见 [01-architecture.md](01-architecture.md) §工具目录)
recall / search_codebase / filter_logs / blast_radius / validate_patch / memory_memorize。

## e2e 实证(2026-08-06,demo2 wpa_supplicant P2P scan 孤儿 radio work 泄漏)

**两轮,证明 enforcement 必要:**

- **e2e #1**(默认 opencode agent + 自动发现 skill,无 enforcement):**机制成立** —— skill 加载(第 2 次尝试;第 1 次 error)、调了 recall+filter_logs、edit 改代码、产合理补丁(dbus_new_handlers.c 误路由入口加守卫)。**但 advisory 没被严格走完**:跳了 search_codebase(用 grep 代)/ blast_radius / **validate_patch** / **memory_memorize**;撞默认 agent 步数上限没出最终报告。→ 证明:pivot 机制对(不走 orchestrator 也能 RCA),但 advisory skill 需 enforcement。

- **e2e #2**(`hyperion-bug-rca` agent,硬门 enforcement):**干净成功**。recall → search_codebase → blast_radius(error=图没建,优雅降级)→ edit → **validate_patch ×2**(第 1 次 applies=False `p2p_supplicant.c:2448` context 不匹配 → agent 修正补丁 → 第 2 次 ✅ strict 通过)→ **memory_memorize**(id=b448561a kind=bug_lesson 入库)。**validate 硬门抓到真缺陷逼修正 = 门控有价值的实证**。最终报告:根因 = P2P StopFind → `wpas_abort_ongoing_scan` 失败(ret=-2)时不释放 `p2p_scan_work` → radio work 永久泄漏阻塞所有 STA scan,证据 `p2p_supplicant.c:2451-2452`,带证伪(**还 recall 出旧记忆 conf=0.35 "scan_res_handler 覆盖竞态" 并用日志证伪 = 先验→证伪闭环生效**)。

**结论**:① bug-RCA 主路径(不走 orchestrator)成立;② validate/memorize 硬门靠 enforcement agent 可靠触发,且 validate 能抓补丁缺陷逼修正;③ recall→证伪闭环真起作用(P3 记忆价值实证)。

## 验证封顶(诚实边界,用户定)

bug-RCA 补丁质量:apply 过(Tier 0,validate_patch)= "**plausible**"(合法、方向对),**非"最优/包对"**。e2e #2 的补丁落点是 abort-fail 路径(同 R3 老 orchestrator 的次优路径),非金标的"误路由入口立即释放"。**不跑测试、不复现**(系统软件测试/复现环境太重,用户定封顶)。逼近金标需更强的验证(已封顶不做)+ 可能对抗审。

> 转向换得的是**流程灵活 + 自纠 + 不脆弱**(根除踩坑 #7/#8/#9),不是补丁最优 —— 诚实标注,不假装达标。

## 已知 gap(下轮处理)
- **补丁/报告不落盘**:e2e agent 只 edit 代码 + 聊天回复 + memorize,不写 `.patch`/`.md`。下轮加一道硬门(agent 收尾 `git -C <repo> diff > <out>.patch`)。**认识:memorize 已把根因+修法存进记忆库(queryable,P3 持久资产),真正缺的只是给人看的 patch 文件**。详见 [harness-pivot-handoff] 记忆 + todo.md。
