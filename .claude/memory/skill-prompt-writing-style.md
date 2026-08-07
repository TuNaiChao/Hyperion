---
name: skill-prompt-writing-style
description: "写 SKILL.md / agent prompt 的风格铁律:面向模型(指令性),不要项目内部知识/面向小白/元数据叙事(2026-08-07 用户定)"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
---

2026-08-07 用户拍板:**SKILL.md 和 agent prompt 的受众是模型**(模型读它来决定怎么用工具、如何行动),**不是人**。写法铁律:

**Why:** 模型 context 窗口宝贵,叙事/教学/历史性内容是噪声,不指导行为反而烧 context;模型需要的是**可执行指令**。背书:Anthropic Agent Skills 最佳实践(platform.claude.com/docs "Skill authoring best practices" + agentskills.io "write as if talking to the model, not a human" + progressive disclosure)。

**How to apply:**
1. **指令性语言**(祈使 / "你负责…"/ "Use when…"),不教学 —— 不要"面向小白"开场、不要类比铺垫(如"手术刀+记忆")。
2. **不要项目内部知识**:踩坑编号(#11)、模型误诊史(glm-5.2 连续误诊)、e2e 复盘(e2e#5 里 agent…)、对标论文(POPPER/RepairAgent/METR)、commit 日期/版本/重写记录。这些**写进 `docs/踩坑记录.md` + `.claude/memory/`**(给人 / 跨会话),**不进 skill/prompt**(给模型)。
3. **description = 触发器**(what + when + 关键词),不是营销/介绍。模型靠它决定是否触发 skill。
4. **把教训提炼成可执行指令**(如"从更早切日志""做时序一致性检查"),**不要叙事包装**(如"踩坑 #11 教训是…")。
5. **精简**(progressive disclosure),去冗余重叠;SKILL.md 当入口/目录,细节按需。

**⚠️ 区别于代码注释风格**:[[comment-style-beginner-friendly]] 是**代码注释**面向小白(大白话+类比)—— 那是给人读源码用的。**skill/prompt 相反**,面向模型(指令性、去教学)。两者受众不同,别混:代码注释 = 给人(小白),skill/prompt = 给模型(指令)。

关联:[[comment-style-beginner-friendly]](对照)、[[bug-rca-skill-toolbox-hitl]](本次重写的产物)、[[pitfall-log]](项目内部知识该留的地方)。
