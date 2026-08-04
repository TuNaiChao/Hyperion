---
name: r34-ingest-handoff
description: R3.4 文档摄取(ingest)交付:补丁 retrieve-then-summarize + 长文分块 + CLI;补丁 id 按 diff 内容算(非 LLM summary);e2e GREEN;commit ee8b07a(已 push origin/main)。
metadata: 
  node_type: memory
  type: project
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-08-04T06:51:53.647Z
---

2026-08-04 R3.4 交付(commit `ee8b07a`,**已 push origin/main**):`hyperion memory ingest <path>` 把外部 bug 报告/调研报告(.md/.txt/.pdf)/补丁(.patch/.diff)沉淀成记忆。核心 `src/hyperion/services/memory/ingest.py`。

**关键非显然决策(别回退):**
- **补丁 KI 的 id 按 diff 内容算**(`make_id(scope,"bug_lesson",diff_text)`),**不按 LLM summary** —— LLM 总结每次措辞不同,按 summary 算 id 会让同一 .patch 摄取两次落两个 id(重复入库)。按 diff 算 → 同 patch→同 id→bayesian 合并(e2e 实测 conf 0.30→0.43)。语义近邻去重(不同 patch 修同一 bug)是 backlog,需 embedding 聚类。
- **不新增 kind**:补丁用 `bug_lesson`(fix_patch 字段已存在)+ `tags=["patch_insight"]` + `SourceTier.imported`。加 kind 要动 schema/extract/consolidate/FTS,不值。
- **retrieve 降级**:repo 未索引 → `_gather_context` 返 "" → 只喂 diff 给 LLM(不阻塞,同 Verifier 降级哲学)。

**e2e 活捉的 R1 脆弱点(已修 `b9bfa84`,已 push):** extract.py `_ExtractionResult.model_validate` 原是**整批校验** —— LLM 偶把 `kind` 值(`bug_lesson`)错塞进 `kind_detail`(只认 module/symbol/architecture,踩坑 #5)→ 整批丢、该次 ingest 写 0(降级不崩,report 重摄取时偶现)。已改逐条 `_ExtractedItem.model_validate` + skip 坏条留好条(严格更优;7 单测覆盖)。补丁路不受影响(PatchIngestPipeline 自组 KI)。

**复用(零新存储):** parse_issue(loader)/memorize_report/extract_items/_extract_json_object/retrieve/make_id 全就位。Cognee 的 Extract→Cognify→Load 是从零造整条管线;Hyperion 只补三块拼图:文档入口 / 长文分块 / 补丁 retrieve-then-summarize。deer-flow/mnemopi 都没有文档摄入(记忆源仅限对话)—— 这块是 Hyperion 新东西。

**Codeant mis-cite 订正:** 计划旧稿引「Codeant/ICSE2026 arXiv:2503.15223」讲 retrieve-then-summarize —— 该 id 实为 SWE-bench correctness 论文,Codeant 是商业产品非论文。换 PATCH(ACM25)/SpecRover(ICSE25)/What-Do-They-Fix(NDSS2026),均 WebFetch 核验。见 [[verify-arxiv-cites-before-commit]]。

**下一步候选:** R3.5(opencode serve #55 + bug-rca report 精修 #46)/ skill S1-S4 / extract.py 逐条容错(backlog)。关联 [[multi-stage-delegate-decision]] [[align-to-deerflow-production-grade]]。
