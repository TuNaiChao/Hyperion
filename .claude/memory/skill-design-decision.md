---
name: skill-design-decision
description: "Skill 子系统设计(2026-08-04 调研完成,待 post-R3 实施):opencode 原生吃 SKILL.md,Hyperion 只当仓库+物化器,不自建激活中间件。"
metadata: 
  node_type: memory
  type: project
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-08-07T09:03:36.727Z
---

**Skill 子系统设计已定(2026-08-04),待 R3 全套收尾后实施。** 完整设计见 `docs/设计/skill-design.md`。关联 [[delegate-already-localizes]](同根:委托型别重造 delegate 已有能力) [[avoid-overengineering]]。

**关键调研结论**:opencode 有**一等公民 skill 原生支持**([opencode.ai/docs/skills](https://opencode.ai/docs/skills/))—— 自动发现 `.opencode/skills/<name>/SKILL.md`(从 cwd 走到 git 根)+ 原生 `skill({name})` 工具(progressive disclosure,省 token)+ `permission.skill` 权限。**所以 Hyperion 不自建激活**(踩坑 #2 同款教训),只当**仓库 + 选择器 + 物化器**:把选中 skill 写进委托工作区的 `.opencode/skills/`,opencode 原生接管。

**范围决策(防过度设计)**:不建 deer-flow 式 `SkillActivationMiddleware`/`ToolPolicy`/`Slash`/`Catalog`/`Describe`(5+ 模块,那是给 deer-flow 自己的 agent 用的;Hyperion lead agent 做调度不吃 skill,消费者是 opencode)。`allowed-tools` frontmatter 是 Claude Code 私有,opencode 忽略 → 权限走 opencode 的 `permission.skill`。

**分阶段(S1-S5)**:
- **S1** skill 仓库 + loader(借 deer-flow `parser.py`/`types.py`)+ CLI `hyperion skill list/add/remove/show`。仓库落 `skills/<name>/SKILL.md`(项目级 git 跟踪;per-user 留 R4)。
- **S2** delegate 物化:`OpencodeDelegate.run(skills=[...])` → 写 `<cwd>/.opencode/skills/<name>/SKILL.md` + workspace 级 opencode config 加 `permission.skill` allow。
- **S3** bug_rca 透传:`hyperion bug-rca ... --skill <name>`(可重复)→ workflow state → delegate。
- **S4** 发货 `skills/cve-patch/SKILL.md` 样例(用户 CVE 修复场景,端到端可演示)。
- **S5**(pull-by-need)Hyperion lead agent 自带激活 —— 仅当将来 Hyperion 自己的 agent 要吃 skill 才做。

**frontmatter 硬约束(写 loader 要守)**:`name` 正则 `^[a-z0-9]+(-[a-z0-9]+)*$`、1-64 字符、须与目录名一致;`description` 1-1024 字符;未知字段忽略。

**触发实施的信号**:R3.3/R3.4 收尾后,或用户明确要 CVE 自动修复场景上线。开工前先核 opencode 本机版本有 `skill` 工具(v1.18.3 已确认有)。

---

## ⚠️ 2026-08-07 pivot 后评估:S1–S5 暂缓(YAGNI)

harness 转向后,**bug-rca / patch-review skill 放 `.claude/skills/` 已被 opencode 原生发现并工作**(e2e 验证 7-8 工具全原生触发)。在此事实下,S1(loader/CLI/物化器)的边际价值 = 仅**跨 agent 适配**(codex/claude code 可能不吃 `.claude/skills/`)+ **参数化物化**(skill 需注入 codebase/bugid 时)。当前只用 opencode + 静态 skill → **S1–S5 暂缓(YAGNI,对齐 [[avoid-overengineering]])**,等真要跨 agent 或 skill 需参数化时再建。触发信号追加一条:**要支持非 opencode agent**。
