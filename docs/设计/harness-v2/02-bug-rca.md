# 02 · bug-RCA 设计(harness 转向后)

> 取代:`docs/设计/bug-rca-design.md` 的 orchestrator(六节点固定管线)设计 —— 那是 pre-pivot 产物,
> 已降级为**参考实现**(代码留 [workflows/bug_rca/](../../../src/hyperion/workflows/bug_rca/),docstring 标 post-pivot 参考,
> CLI `hyperion bug-rca` 留兼容 + deprecate 提示)。本文档是 bug-RCA 的**当前主路径设计**。

## 主路径:opencode + bug-rca skill + 6 个 hyperion 工具,agent 自驱

不再走固定六节点管线(`ingest→recall_lessons→delegate_localize_loop→assemble_repair→
delegate_repair_loop→report_memorize`)。改成:**opencode 自己驱动**,Hyperion 只递工具(记忆/代码情报/
日志/影响面/补丁验证)+ 菜谱(skill)。agent 干它擅长的(读码推理 + 改代码),Hyperion 不重复定位(踩坑 #2)。

### skill:[bug-rca/SKILL.md](../../../.claude/skills/bug-rca/SKILL.md)(8 步,3 硬门)
1. **先 recall** `hyperion_memory_recall(线索)` —— 翻历史同类 bug 教训。**先验非答案**,与证据矛盾以证据为准。
2. **语义搜入口** `hyperion_search_codebase(概念)` —— 用概念别盲 grep;拿真实 file:symbol:line 锚点再精读。
3. **过滤日志** `hyperion_filter_logs(path, since/until=故障窗)` —— 别读全量。
4. **立假设 + 证伪** —— 根因(why 句 + file:line)+ 主动找推翻自己的反例。
5. **查 blast-radius** `hyperion_blast_radius(要改的文件)` —— 动手前看连带影响。
6. **改代码 + validate【硬门】** `hyperion_validate_patch(补丁, repo_path)` —— apply 不了不算修复。
7. **落盘补丁【硬门】** `hyperion_export_patch(repo_path)` —— 写成 `data/bug_rca/<repo>.patch`(没产文件=没交付)。
8. **memorize【硬门】** `hyperion_memory_memorize(kind=bug_lesson, ...)` —— 沉淀教训(不记=白干)。

advisory(灵活自纠)但 **mandate 三道硬门**(validate / export_patch / memorize)。确定性靠 **skill 强制步骤 + 工具硬门**,不靠固定图。

### agent enforcement:[hyperion-bug-rca](../../../config/opencode_hyperion.json)(opencode 专用 agent)
skill 是 advisory,glm-5.2 不一定严格照走(实测见下)。所以用 opencode 专用 agent 做 enforcement:把 8 步 playbook **烙进 system prompt**(常驻 > 等 skill 自动触发)+ `steps=25`(给够走完)+ 把 validate/export_patch/memorize 写成**"不做完不算完成 + 步数将尽优先做这三步"**。permission:`edit/bash/read/grep/glob/list/hyperion*/skill` allow。

> skill(方法论,可移植到任何 agent)与 agent(enforcement,opencode 专属)正交。两者协同:skill 是源,agent 是 opencode 上的强制壳。

### 工具(7 共享,见 [01-architecture.md](01-architecture.md) §工具目录)
recall / search_codebase / filter_logs / blast_radius / validate_patch / export_patch / memory_memorize。

## e2e 实证(2026-08-06,demo2 wpa_supplicant P2P scan 孤儿 radio work 泄漏)

**两轮,证明 enforcement 必要:**

- **e2e #1**(默认 opencode agent + 自动发现 skill,无 enforcement):**机制成立** —— skill 加载(第 2 次尝试;第 1 次 error)、调了 recall+filter_logs、edit 改代码、产合理补丁(dbus_new_handlers.c 误路由入口加守卫)。**但 advisory 没被严格走完**:跳了 search_codebase(用 grep 代)/ blast_radius / **validate_patch** / **memory_memorize**;撞默认 agent 步数上限没出最终报告。→ 证明:pivot 机制对(不走 orchestrator 也能 RCA),但 advisory skill 需 enforcement。

- **e2e #2**(`hyperion-bug-rca` agent,硬门 enforcement):**干净成功**。recall → search_codebase → blast_radius(error=图没建,优雅降级)→ edit → **validate_patch ×2**(第 1 次 applies=False `p2p_supplicant.c:2448` context 不匹配 → agent 修正补丁 → 第 2 次 ✅ strict 通过)→ **memory_memorize**(id=b448561a kind=bug_lesson 入库)。**validate 硬门抓到真缺陷逼修正 = 门控有价值的实证**。最终报告:根因 = P2P StopFind → `wpas_abort_ongoing_scan` 失败(ret=-2)时不释放 `p2p_scan_work` → radio work 永久泄漏阻塞所有 STA scan,证据 `p2p_supplicant.c:2451-2452`,带证伪(**还 recall 出旧记忆 conf=0.35 "scan_res_handler 覆盖竞态" 并用日志证伪 = 先验→证伪闭环生效**)。

**结论**:① bug-RCA 主路径(不走 orchestrator)成立;② validate/memorize 硬门靠 enforcement agent 可靠触发,且 validate 能抓补丁缺陷逼修正;③ recall→证伪闭环真起作用(P3 记忆价值实证)。

## 验证封顶(诚实边界,用户定)

bug-RCA 补丁质量:apply 过(Tier 0,validate_patch)= "**plausible**"(合法、方向对),**非"最优/包对"**。e2e #2 的补丁落点是 abort-fail 路径(同 R3 老 orchestrator 的次优路径),非金标的"误路由入口立即释放"。**不跑测试、不复现**(系统软件测试/复现环境太重,用户定封顶)。逼近金标需更强的验证(已封顶不做)+ 可能对抗审。

> 转向换得的是**流程灵活 + 自纠 + 不脆弱**(根除踩坑 #7/#8/#9),不是补丁最优 —— 诚实标注,不假装达标。

## 已知 gap
- **✅ 补丁落盘(2026-08-06 解决)**:加了第 7 个 MCP 工具 `hyperion_export_patch`(wrap `git add -A && git diff --cached` → 写 `data/bug_rca/<repo>.patch` + 空 diff 自检),进 SKILL.md 第⑦步 + `hyperion-bug-rca` agent 第 8 步硬门。格式 unified diff(对齐 validate/ingest/report 整条管线;**不污染 repo**——无需建 commit)。**认识兑现**:memorize 存根因+修法(可检索),export_patch 补上给人/CI 看的 `.patch` 文件。
- **报告不落盘(仍开)**:agent 给聊天总结,不写 `report.md`。下轮可让 agent 收尾把总结写 `data/bug_rca/<repo>-rca.md`(对齐老 workflow 的 render_report 输出位)。

### "硬门"的诚实边界(2026 前沿调研结论)
skill/playbook 文字指令是 **soft(~95%),非确定性硬门**——Anthropic 自己文档承认("use hooks to enforce behavior deterministically")。三层 enforcement:**text skill(软)< 结构化 tool(更硬)< 事后交付验证(deer-flow 用 RunJournal 比对"产出 vs present",不匹配 = run ERROR)**。Hyperion post-pivot 是 **opencode 驱动**,Hyperion 不驱动模型 → 拿不到 `tool_choice` 强制 / Stop hook 那层确定性(那需要 Hyperion 自己跑 lead agent)。所以这里三道"硬门" = **确定性 tool(真 git/DB/文件操作)+ prompt 强制必调(软,靠 `hyperion-bug-rca` 专用 agent + steps + "步数将尽优先"语言,e2e #2 实证可靠)**。要真·确定性门,要么 Hyperion 自己跑 lead(违背转向),要么 opencode 出 Stop-hook 等价物(待观察)。本轮 export_patch 走"结构化 tool"层(比纯 bash `git diff > file` 硬:bash 会静默吞空 diff/改错树)。

### 生产级迭代(留 backlog,本轮不做)
- **`git format-patch`(mbox)+ `Assisted-by:` AI 标签**:kernel/wpa/bluez 上游贡献用 mbox(可 `git am` / `git send-email` / checkpatch),kernel 2025-8 落地 AI 代码政策要 `Assisted-by: Hyperion: <model> [bug-rca] [validate_patch]`(**绝不**让 AI 签 `Signed-off-by`,DCO 只人类签)。mbox 需建 commit(污染 repo,post-pivot 无 workspace 时麻烦),留 P-A/生产级迭代做(配 workspace commit 策略)。本轮 unified diff 够用(SWE-bench/OpenHands/SWE-agent 的 agent 产物也都是 unified diff,`git apply` 链直接吃)。
- **provenance sidecar**(SLSA/in-toto 轻量 manifest:`base_commit`/`source_repo`/`bug_id`/`model`/`validated`)+ **per-bug workspace 归档**(保历史,不被同仓 clobber):留生产级。
