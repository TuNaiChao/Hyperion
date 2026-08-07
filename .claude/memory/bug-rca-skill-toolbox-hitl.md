---
name: bug-rca-skill-toolbox-hitl
description: "2026-08-07 bug-RCA skill 二次演化:从固定流水线(9步4硬门单session)转为工具箱+人在环迭代(Anthropic/POPPER/RepairAgent 背书);memorize/export_report 降为验证后收尾"
metadata:
  node_type: memory
  type: project
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
---

2026-08-07 用户拍板 + commit `deeab6c`(已 push origin/main):bug-RCA skill 从**固定流水线**(9 步必走完、单 session 4 硬门 validate/export_patch/memorize/export_report)转向**工具箱 + 人在环迭代**。

## 为什么转(用户洞察 + 调研背书)
用户指出:补丁/报告不一定准,不该强求一次走完;每步应作单独工具按需调用,补丁去真机验证、通过才 memorize/report。调研前沿坐实方向:
- **Anthropic "Don't Build Agents, Build Skills"**(Barry Zhang talk):skill = 可组合**工具箱**(按需加载),非"预载全工具的固定流水线"。旧 9 步流水线正是它警告的 fixed pipeline。
- **POPPER(ICML 2025, Stanford)**:RCA = 自动化"假设 → 设计**证伪**实验 → 修正"循环,非一次猜中。
- **RepairAgent(ICSE 2025)**:generate → apply → **test** 补丁-验证循环。
- **METR**:test-passing PR 约一半不 merge → 自动验证不够,真机/人是 oracle。

## 改了啥(SKILL.md + hyperion-bug-rca agent,commit deeab6c)
1. **去固定顺序**:9 步流水线 → 三条原则(迭代假设-证伪 / 补丁-验证循环 / 验证通过才沉淀)+ 工具表(按需取用,无固定顺序)。
2. **memorize/export_report 降级**:从"单 session 硬门"→"真机/人验证通过后的收尾(可跨 session,`--continue` 续)"。没验证就 memorize = 污染记忆(接 [[pitfall-log]] #11 记忆反噬)。
3. **validate_patch 诚实定位**:只验 apply 不验修对;系统软件无单测,真机是 oracle。
4. **保留踩坑 #11 对抗**:时序一致性检查 + filter_logs 边界提醒(防确认偏差)。

## e2e 方法学随之变
- **旧**:单 session 跑完 9 步(validate/export/memorize/report 全过)= e2e#4/#5 的跑法。
- **新**:单 session 跑到 `export_patch`(交人验证);memorize/report 等真机验证后(可能跨 session)。e2e#4/#5 是旧范式产物,新范式下要重设 e2e(单 session 验"到 export_patch + 不在未验证就 memorize";完整 RCA 验证跨 session + 人在环)。

## 待办
- **02-bug-rca.md 同步**:仍描述 9 步 4 硬门 skill + 单 session agent enforcement,待改成工具箱+人在环(文档暂时落后于 SKILL/agent 代码)。
- **e2e#6 重新定位**:旧计划(验证 filter_logs 边界提醒纠偏)在新范式下,单 session 仍可验"agent 是否前推窗 + 是否不在未验证就 memorize",但完整纠偏验证要跨 session + 人在环。

关联:[[harness-pivot-handoff]](post-pivot 主交接)、[[pitfall-log]] #11(glm-5.2 连续误诊,促成本次反思)、[[skill-design-decision]](skill 子系统设计)、[[similar-bug-recall-roadmap]](recall→证伪闭环)。
