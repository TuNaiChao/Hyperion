---
name: pr-review-design-decision
description: "R4.1 PR 批量分析+聚合报告设计完成(post-R3,待施工)。给一组 GitHub PR 链接→抓 diff→分析→去重记忆→跨 PR 聚合(模块/安全/功能)→报告。复用 ingest/deep_research/CRG,新东西仅三块(GitHub 抓取/批量编排/聚合报告)。"
metadata: 
  node_type: memory
  type: project
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-08-04T07:16:07.946Z
---

2026-08-04 设计完成,待施工(post-R3)。**用户拍板两岔路**:① per-PR 分析深度 = **分层**(默认轻量 PatchIngestPipeline,对 risk_score 高/安全相关子集自动升级深度 ReAct 子 agent);② 定位 = **R4.1**(CLAUDE.md 路线 R4「PR 跟踪」支柱的具体化,post-R3 做)。完整设计 `docs/设计/pr-review-design.md`;CLAUDE.md 路线已更新指向它。

**核心判断(别回退):这功能不该从头造** —— 它是 R3.4(ingest 单补丁→记忆)+ deep_research(报告/Verifier)+ CRG(影响面)三块已建能力的新编排。真正的新拼图只有三块:① GitHub 抓取层(全仓零实现,借 deer-flow `github_api.py:51` + robomp `github_backend.py:28` + `issue_index.py:102` watermark);② 批量编排(套 deep_research 五件套 graph/state/fan-out/report/verify);③ 聚合+报告层(按 module+theme 分桶 + map-reduce)。

**关键非显然决策:**
- **不加 kind / 不加 scope 维度**:PR 标识塞进 `tags=["patch_insight","pr:<o>/<r>#<n>","module:<x>"]` + `source`=URL + `commit_sha`=merge commit;`SourceTier.imported`。Scope=(人,库)是隔离维度,PR 号是属性不该进 Scope。见 [[delegate-already-localizes]](别重造,先复用)。
- **必须补 `symptom` 字段(真 gap)**:PatchIngestPipeline 现在只填 `root_cause`/`summary`,没填 `symptom` → `_same_subject`(memorize.py:79)对补丁 KI 返 False → 跨 PR 修同根因不会触发 supersede。R4.1.1 要修。
- **CRG 影响面接入点 = 扩 `structural.py:59`**:接 `get_impact_radius`(graph.py:742)+ `analyze_changes`(changes.py:381)+ `compute_risk_score`(:312 **内置 SECURITY_KEYWORDS → +0.20**,安全相关性一半答案白送)。现在手握每个 PR 的 changed_files,正是 structural.py:44 挂了半年的 backlog 的天然入口。全图预算 hub/bridge/community 索引跨 PR 复用(只算一次)。
- **安全分析分层省 token**:CRG risk_score + 关键词预筛 → 只对命中子集送 LLM 深分类(CWE/taint),不全量 LLM。几百条 PR 才烧得起。
- **模块归属**:`get_community_ids_by_qualified_names`(graph.py:1227,450 批)+ `get_architecture_overview` 的 `cross_community_edges` = 耦合热力图。
- **跨 PR 语义近邻去重(不同 PR 修同一 bug)→ backlog**:id 按 diff 去重只管同 patch;语义去重需 embedding 聚类,首发不做。

**调研核验**:SmartNote(arXiv 2505.17977)已 WebFetch 核验(聚合变更→分类→打分→结构化报告,范式同构);搜索给的 "ToM arXiv 2511.004490" 经核验**实际是脑瘤分割论文**,张冠李戴已拦(见 [[verify-arxiv-cites-before-commit]])。PR 级 change-impact(Springer EMSE)+ map-reduce + gh CLI/vulnerability-spoiler-alert-action 均查实。

**R4.1 六步**:R4.1.0 抓取层 → .1 PatchIngestPipeline 补字段(symptom 修复)→ .2 扩 CRG structural → .3 聚合层 → .4 workflow+报告+Verifier → .5 CLI `hyperion pr-review`。验证闸:抓取/分析成功率、同 PR 重抓合并、报告虚假引用=0、模块分布对得上人工、recall 命中。关联 [[r34-ingest-handoff]] [[align-to-deerflow-production-grade]]。
