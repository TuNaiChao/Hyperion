---
name: memory-consolidation-phase1-handoff
description: 2026-08-14 记忆模块分析文档 + Phase 1 consolidate 三 pass 化(矛盾检测+语义去重)+ e2e 抓的 symptom 空回退 bug。对标 2026 业界 keeps/merges/evicts。
metadata: 
  node_type: memory
  type: project
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-08-14T03:34:59.549Z
---

**2026-08-14 落地**:记忆模块(P3)做了三件事:① 写全景分析文档 ② Phase 1 consolidate 三 pass 化 ③ e2e 抓真 bug 修复。三个 commit:`112820b`(功能)+ 文档 commit + `713c3e1`(e2e 修复)。

## 1. 两篇文档(新风格 [[doc-writing-style]])

- `docs/memory-module-analysis.md`:记忆模块当前实现全景分析。KnowledgeItem 7 组字段(身份/内容/出处/可信度/时间/关系/纠正)、五大生命周期(管/增/删/查/冲突)、2026 业界对照表(Graphiti/Zep/mem0 v3/Letta)。面向小白+比喻(实习生/图书馆索引卡/四侦探翻档案柜),去时间戳噪音+本项目化。
- `docs/memory-module-roadmap.md`:改进建议(A 放大差异化:代码锚点溯源+三路融合是独有王牌 / B 补短板:consolidate 太薄 / C 明确不做:不迁 Neo4j)+ 分阶段实现规划(Phase 1 已成/2/3)。

**调研坐实(4 轮 WebSearch)**:Graphiti bi-temporal 领先(矛盾处理 63.8%);mem0 v3 ADD-only+Dream consolidation;业界共识 consolidation=keeps/merges/evicts。Hyperion 已是一线水平(bi-temporal+纠正链+来源加权+append-only+四类 taxonomy+三路融合),短板只在 consolidate 太薄。

## 2. Phase 1:consolidate 三 pass 化

对标 keeps/merges/evicts,原 consolidate 只做升级 mental_model(keeps 一种),补上:

- **矛盾检测 `_detect_contradictions`**(merges 的一种):同主题不同结论的 active 高 conf 对 → 打 `needs_review` 标签。**只标不裁**(谁是正确根因是语义判断,踩坑#11,系统不选边)→ 留 memory-health-check skill/人裁决。确定性(`_same_subject`+`not _same_conclusion`)。
- **语义去重 `_detect_semantic_duplicates`**:同 kind+embedding cosine≥0.92 → 报候选簇。**只报不合**(自动合并语义危险,误合近义不同 bug;宁漏不错)。并查集聚簇(拆模块级 `_union_find`/`_count_duplicate_clusters` 避 ruff B023)。domain_knowledge 跳过(领域知识天然语义近邻正常)。
- 返回扩成 `{scanned, promoted, contradictions, duplicate_clusters}`。
- store.py 加 `set_tags()`。

## 3. e2e 抓的真 bug(核心价值,印证 [[new-feature-run-opencode-e2e]])

e2e(真 SQLite + 真 Qwen3 embedding)第一跑矛盾对=0(期望 2)→ 抓到:`_same_subject` 对 bug_lesson 硬依赖 symptom 非空,但 CLI/MCP/extract 写入路径 symptom 常空(默认只填 root_cause)→ 矛盾漏判。
**修复**:bug_lesson symptom 任一空时回退比 evidence 文件(同文件=同主题)。+ 单测覆盖回退。
**这就是「新功能要自跑 e2e」的价值**:单测全绿但 e2e 暴露真实写入路径的盲区。

## 验证

- 单测:51 passed(26 native 含+2 矛盾/语义/边界/回退;extract;ingest)+ ruff clean。
- e2e:矛盾对精准(同 scan.c 不同根因→判矛盾;无关 auth.c→不误报)+ 重复簇命中(近义 codebase_fact)+ needs_review 标签落真 DB + 真向量(4096 字节 Qwen3)。种子数据用隔离 codebase=e2e_consolidate_test,验完清干净(残留 0,不污染 wpa 真记忆)。

## Phase 2 待做(见 roadmap)

- 自动失效(补丁合入上游→invalidate,接 git/merge_eval)。
- 长期未命中主动降 confidence(evict,非物理删)。

关联 [[doc-writing-style]] [[new-feature-run-opencode-e2e]] [[opencode-mcp-wiring]] [[pitfall-log]](#11 误诊→矛盾只标不裁;#5 LLM schema 不守→回退鲁棒) [[align-to-deerflow-production-grade]]。
