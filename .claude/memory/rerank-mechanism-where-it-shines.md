---
name: rerank-mechanism-where-it-shines
description: "rerank/majority-voting 的适用边界 —— 需 oracle 或样本多样性,否则平凡;bug-RCA patch rerank 默认关,复用到 localize 文件投票/调研事实一致性/有 oracle 的 patch"
metadata: 
  node_type: memory
  type: project
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-07-31T01:33:57.686Z
---

「多采样 + 归一化 + 投票」(Agentless majority voting,`workflows/bug_rca/rerank.py`)机制本身没错,前提是(二选一):① 有廉价可靠 **oracle**(测试套件 / repro);② 样本**多样**且能归一化到同一规范解(self-consistency)。**两条都不沾时投票平凡**(白烧 N× token)——这正是 wpa/bluez 的 bug-RCA patch 选择(无测试 + C 补丁形态发散 + glm-5.2 近确定性)。

**决策(2026-07-31,#54-rework):** bug-RCA patch rerank **降为兜底**(`config delegate.rerank.enabled` 默认关;仅 repair loop 耗尽 + enabled 才 fan-out),主路径改 [[multi-stage-delegate-decision]] 的迭代 verify-refine(B)。`majority_vote` 原语**换地方用**(复用同一 Counter/票数/首现/简洁度模式,换归一化函数):
- **A. localize 文件投票**(R3.1 方案A):文件名归一化平凡、无需 oracle —— 性价比最高的归宿。
- **B. 深度调研多视角 + 事实一致性**(R3.2):N 条轨迹,事实出现频次作置信度。
- **C. 有 oracle 的 patch rerank**(R5 / 有测试套件模块):filter+vote 才有效;`enabled: auto` 检测到 oracle 才开。

**How to apply:** 别再把 patch 多候选投票塞回 bug-RCA 主路径(默认关)。需要投票时优先想「这有 oracle 吗?样本会多样吗?」;两否 → 走迭代 refine,别采样。完整分析(含面向小白 oracle/filter+vote 解释)见 `docs/设计/bug-rca-design.md` §7.6。
