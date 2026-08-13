---
name: suggestion-d-memory-consolidation-handoff
description: 建议 D 落地:recall 后异步自动 consolidate(对标 Cognee self-improving)。更正 architecture-review「consolidate 无人调」误判 —— CLI 早 wire、recall 早 bump。
metadata:
  type: project
---

2026-08-13 落地 architecture-review §五 建议 D(记忆巩固自动触发)。

## 更正:architecture-review「无人调」是误判
本会话实测推翻了「`consolidate()` 已实现但无人调」的前提:
- ✅ `hyperion memory consolidate` CLI **早 wire**(cli.py:276-279)
- ✅ recall **早就在 bump**:recall() 默认 `bump=True` → `store.bump_access(h.item_id)`(recall.py:186-190)→ access_count+1(store.py:307)
- ✅ e2e 实测全链 GREEN(临时库):写 1 条 → recall 3 次 → access_count=3 → consolidate → promoted=1 → kind=mental_model

**真正缺的只是「自转」**:recall 不会顺手 consolidate,得人手动敲 CLI 才升。

## 改了啥
- **`NativeMemoryConfig.auto_consolidate: bool = True`**(config.py)—— 新开关。
- **`NativeMemoryService.recall`**(service.py):命中 memory 路条目(任意 `h.item_id` 非空)→ `asyncio.create_task(self._safe_consolidate(scope))` fire-and-forget。复用 `promote_access_count` 阈值(不造新阈值)。
- **`_safe_consolidate`**:try/except 吞异常,失败只记日志(consolidate 是优化,不是 recall 契约,绝不拖慢/崩 recall)。
- 3 单测:`test_recall_bump_consolidate_e2e`(真实 recall 链,区别于旧的 store.bump_access 手动模拟)+ `test_service_recall_auto_consolidates`(自转 promote)+ `..._disabled`(扩展口)。全记忆 37 绿。

## 设计要点(防回退踩坑)
1. **触发条件 = 命中 memory 路条目(任意 item_id 非空)**,不靠 access_count 值 —— RecallHit 没 access_count 字段(schema 只有 item_id);consolidate 自己判达不达标(没达标 promoted=0,只扫表不改,微秒级)。
2. **fire-and-forget + 同事件循环**:`asyncio.create_task` 的后台 task 绑在当前事件循环;测试里全程在**同一个 asyncio.run** 跑完 recall + sleep(多次 asyncio.run 会让前次的 task 被丢弃)。生产环境 agent 是长事件循环,无此问题。
3. **不挂 search()**:search 明确 `bump=False`(「不 bump」是契约),没 access_count 信号,挂了空跑。
4. **不每次 recall 都扫全表的顾虑被放大**:recall 在 Hyperion 不是高频(agent 一轮几次),扫几十/几百条 SQLite + 判 access_count 亚毫秒级。事件驱动(命中才触发)已够稀疏。

## 故意不做(YAGNI)
- 不加触发频率上限(token bucket)—— 事件驱动够稀疏;真机 recall 极高频再加,记 backlog。
- 不改 ABC 契约(manager.py)/ 不造新阈值 / 不进 yaml(对齐 turn_budget/建议 B 先例,ncfg 字段有默认)。
- 不做语义近邻去重 —— consolidate.py:7-9 已记 backlog(需 embedding 聚类);本建议只做「自转」。

相关:[[memory-design-review-2026-08-12]] recall N=2 复核;[[memory-append-only-directive]] 记忆只追加;[[suggestion-b-token-summarization-handoff]] 同批建议 B。
