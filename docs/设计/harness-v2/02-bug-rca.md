# 02 · bug-RCA 设计(harness 转向后)

> 取代:`docs/设计/bug-rca-design.md` 的 orchestrator(六节点固定管线)设计 —— pre-pivot 产物,
> 已降级为**参考实现**(代码留 [workflows/bug_rca/](../../../src/hyperion/workflows/bug_rca/),docstring 标 post-pivot 参考,
> CLI `hyperion bug-rca` 留兼容 + deprecate 提示)。本文档是 bug-RCA 的**当前主路径设计**。
>
> **2026-08-07 二次演化**:bug-RCA skill 从固定流水线(9 步 4 硬门)转为**工具箱 + 人在环迭代**(踩坑 #11/#12/#13)。本文同步。

## 主路径:opencode + bug-rca skill + 8 个 hyperion 工具,agent 自驱 + 人在环

opencode 自己驱动,Hyperion 只递工具(记忆/代码情报/日志/影响面/补丁验证)+ 菜谱(skill)。agent 干它擅长的(读码推理 + 改代码),Hyperion 不重复定位(踩坑 #2)。

**这不是固定流水线,是工具箱 + 人在环迭代**(对标 Anthropic "Build Skills Not Agents" + POPPER 迭代证伪 + RepairAgent 补丁-验证循环,详见踩坑 #12):
- 根因很少一次猜中、补丁很少一次到位 → **迭代**,不是一次走完。
- `validate_patch` 只验 apply,不验修对;**真机/人是 oracle**。
- memorize/export_report 是**验证通过后**的收尾,不是单 session 硬门。

### skill:[bug-rca/SKILL.md](../../../.claude/skills/bug-rca/SKILL.md)(工具箱 + 人在环)

SKILL.md 是**工具箱**(按需取用,无固定顺序)+ 三条原则:
1. **迭代假设-证伪**:recall/search_codebase/filter_logs 取证,每轮主动证伪(含时序一致性检查,踩坑 #11);经住证伪才定论。
2. **补丁-验证循环**:edit → validate_patch(每版验 apply)→ export_patch(每版落盘)→ 人/真机验证 → 没修对再改再 export。
3. **验证通过才沉淀**:memorize + export_report 验证后才调;未验证就 memorize = 污染记忆。

工具表(8 把,按需调):见 [01-architecture.md](01-architecture.md) §工具目录(recall / search_codebase / filter_logs / blast_radius / validate_patch / export_patch / memorize / export_report)。

> SKILL.md 写给**模型**(指令性,非教学/叙事),踩坑 #13。项目内部知识(踩坑编号/误诊史/对标论文)留本文 + [踩坑记录.md](../踩坑记录.md) + `.claude/memory/`,不进 skill/prompt。

### agent enforcement:[hyperion-bug-rca](../../../config/opencode_hyperion.json)(opencode 专用 agent)

skill 是 advisory;用 opencode 专用 agent 做 enforcement:把"迭代 + 人在环"烙进 system prompt(工具箱非流水线、验证后才 memorize、步数将尽优先 validate+export_patch 落盘交人)。permission:`edit/bash/read/grep/glob/list/hyperion*/skill` allow。

> skill(方法论,可移植)与 agent(enforcement,opencode 专属)正交。

## e2e 实证(demo2 wpa_supplicant P2P scan 孤儿 radio work 泄漏)

- **e2e #1**(默认 agent,无 enforcement):机制成立(skill 加载 + recall/filter/edit + 合理补丁),但 advisory 没走完(跳 search/blast/validate/memorize)→ 证 enforcement 必要。
- **e2e #2**(hyperion-bug-rca agent,旧 9 步硬门):干净成功,validate×2 抓补丁缺陷逼修正,memorize 入库。**但根因落点 = abort-fail**(非金标的误路由入口)。
- **e2e #4**(加 export_report):8 工具全原生触发,export_report 写 -rca.md。**但仍 abort-fail 误诊**(第 3 次)。
- **e2e #5**(加时序证伪 soft 指令):**第 4 次 abort-fail 误诊**。证 soft 层(SKILL/prompt)纠不过模型确认偏差(踩坑 #11);filter_logs 被 agent 设错时间窗起点放大偏差(方案 A 修:边界提醒,commit `e774f1b`)。

**e2e 方法学(新范式)**:单 session 跑到 `export_patch`(交人验证);memorize/report 等真机验证后(可能跨 session)。旧 e2e(#1/#2)"走完"是旧范式产物;新范式 e2e 待重设(单 session 验"到 export_patch + 不未验证 memorize + filter_logs 前推窗")。

## 验证封顶(诚实边界,用户定)

bug-RCA 补丁质量:apply 过(Tier 0)= "**plausible**"(合法、方向对),**非"最优/包对"**。e2e#2/#4/#5 补丁落点均为 abort-fail(非金标的误路由入口释放)。**不跑测试、不复现**(系统软件测试/复现环境太重,用户定封顶)。**真正的 oracle 是真机/人**;逼近金标需人在环迭代验证 + 可能对抗审(踩坑 #11/#12)。

> 转向换得的是**流程灵活 + 自纠 + 不脆弱**(根除踩坑 #7/#8/#9),不是补丁最优 —— 诚实标注,不假装达标。

## 已知 gap

- **✅ 补丁落盘(2026-08-06)**:`hyperion_export_patch`(git add -A && diff --cached → `data/bug_rca/<repo>.patch` + 空 diff 自检)。
- **✅ 报告落盘(2026-08-06)**:`hyperion_export_report(content, repo_path)`(agent 传报告 → `data/bug_rca/<repo>-rca.md` + 空内容自检)。
- **🔄 自动 RCA 偏差(踩坑 #11,2026-08-07 部分治)**:glm-5.2 系统性抓显眼日志行误诊(连续 4 次 abort-fail)。短期(SKILL 时序证伪 soft)验证无效(e2e#5);方案 A(filter_logs 边界提醒,确定性)已落地;中期工具层(强制注入因果起点)/ 长期 R5 运行时验证待办。
- **🔄 真机验证未接入**:工具箱+人在环的 oracle 是真机/人,但 e2e 自动化还没接入真机(待 R5 沙箱/设备 farm,或定义人工验证流程)。

### "硬门"的诚实边界(2026 前沿调研)

skill/playbook 文字指令是 **soft(~95%)**,非确定性硬门(Anthropic 文档承认 "use hooks to enforce deterministically")。三层 enforcement:text skill(软)< 结构化 tool(更硬)< 事后交付验证。Hyperion post-pivot 是 opencode 驱动,拿不到 tool_choice/Stop-hook 那层确定性。所以 bug-RCA 的"硬"靠:**确定性 tool(真 git/DB/文件操作)+ agent prompt 强制(软)**。**新范式下 memorize/report 从"单 session 硬门"降为"验证后收尾"**(踩坑 #12)—— 更诚实(不假装未验证的产物是定论)。

### 生产级迭代(留 backlog)
- **`git format-patch`(mbox)+ `Assisted-by:` AI 标签**:kernel/wpa/bluez 上游贡献用 mbox;kernel 2025-8 AI 代码政策要 `Assisted-by: Hyperion: <model>`(**绝不**让 AI 签 `Signed-off-by`)。留 P-A/生产级。
- **provenance sidecar**(SLSA/in-toto 轻量 manifest)+ per-bug workspace 归档:留生产级。
- **真机验证接入**(工具箱+人在环的 oracle 环):待 R5 沙箱/设备 farm 或人工流程定义。
