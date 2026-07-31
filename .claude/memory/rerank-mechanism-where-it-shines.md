---
name: rerank-mechanism-where-it-shines
description: "patch 投票 rerank 已于 2026-07-31 整体移除 —— 无 oracle 时投票平凡白烧 token;现代 SOTA 转单轨迹+执行验证;有 oracle 再评估,不预建"
metadata: 
  node_type: memory
  type: project
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-07-31T03:10:09.722Z
---

**2026-07-31 决定:patch 多候选投票 rerank(Agentless majority voting)整体移除。** 删了 `workflows/bug_rca/rerank.py`(整文件)+ `_rerank_fallback` + `RerankConfig` + `delegate.rerank` + state `candidates`/`rerank_summary` + 相关测试。bug-RCA 主路径 = [[multi-stage-delegate-decision]] 的迭代 verify-refine(B),**不再有多采样投票兜底**(连"默认关"的开关都删了)。

**Why(调研三铁据 + 用户拍板):** ① 投票有效需(二选一)**廉价可靠 oracle**(测试/repro)或**样本多样 + 归一化**(self-consistency);wpa/bluez **无测试套件** + C 补丁形态发散 + glm-5.2 近确定性 → N 样本雷同 → 投票平凡 + N× token 白烧。② 现代 SOTA(OpenHands critic+执行反馈 / Aider 单轨迹+lint·test / R2E-Gym)已转**单轨迹 + 执行验证**,非文本投票;我们的 B 正是此路线。③ Agentless 投票原文就是 "after test filtering"(filter+vote 命门是测试当 oracle),无测试则 filter 做不了。METR:~半数 test-passing PR 不会被合 → 测试本就是弱 oracle。

**How to apply:** 别再把 patch 多候选投票塞回 bug-RCA。**检索 rerank(`memory.native.rerank` cross-encoder)是不同机制,保留**。未来若真有 oracle(hwsim/bluez 测试套件就绪 / #50 repro 落地),再按 Agentless filter+vote 重写(~40 行,git 史可查),**不预建**(YAGNI)。需要投票时先问「有 oracle 吗?样本会多样吗?」;两否 → 走迭代 refine,别采样。完整分析见 `docs/设计/bug-rca-design.md` §7.6。
