---
name: similar-bug-recall-roadmap
description: "类似-bug 记忆检索该做(2025-2026 学界工业主流,非 YAGNI;区别于 exact-duplicate short-circuit 才是 YAGNI)。Hyperion 检索栈是参考仓最强但'喂法'弱(delegate 自觉调,3 gap)。P0(填 BugLesson 字段 + 确定性预注入)定 R3 收尾;R4.1 白捡。"
metadata:
  node_type: memory
  type: project
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-08-06T01:43:32.757Z
---

2026-08-06 深度调研结论(用户问"定位**类似**问题要不要记忆/检索机制")。

## 核心区分(别混淆)
- **类似问题(similar bug)检索 = 该做,主流方向**。迁移的是**模式/导航**(聚焦新定位),不是抄答案。这是 P3 记忆的核心价值。
- **同一问题(exact duplicate)short-circuit = YAGNI**。省的是重复劳动,罕见+正确性风险。两者表面像、本质相反(short-circuit 想跳过验证=危险;similar-recall 想聚焦验证=增益)。

## 前沿证据(2025-2026 均查实)
- [Improving Code Localization with Repository Memory](https://openreview.net/forum?id=8yjWLJy2eX)(OpenReview 引 13):agent 从非参数化记忆(历史 commit+issue)检索提升定位 —— 学界正名。
- [RepairAgent](https://software-lab.org/publications/icse2025_RepairAgent.pdf)(ICSE25):localize→fix 循环显式检索既有定位+复用历史修复(164 bug)。
- [MCP Tool:相似 bug 修改频次定位](https://ieeexplore.ieee.org/iel8/6287639/11323511/11397350.pdf)(IEEE):数相似 bug 文件修改频次锁嫌疑位,形态同 Hyperion MCP 工具。
- [𝑀𝑒𝑚ᵖ](https://arxiv.org/html/2508.06433v2)(arXiv 2508.06433):终身过程记忆,相似经验检索指导决策。
- [Redis:Retrieval vs Memory](https://redis.io/blog/ai-agent-memory-vs-retrieval/):agent 检索+记忆两者都要。综述 [Awesome-Memory-for-Agents](https://github.com/TsinghuaC3I/Awesome-Memory-for-Agents)。

## Hyperion 现状:检索栈最强,喂法最弱(半成品)
Explore agent 核实(file:line):
- **强(天花板最高)**:recall 4 路(BM25+向量+code+structural)+ RRF + cross-encoder rerank + 衰减 + 置信加权([recall.py:124-191](src/hyperion/services/memory/backends/native/recall.py#L124));每条带 evidence[file:line]+conf+bi-temporal;access_count 持续学习。对标:deer-flow 只 BM25+decay、mnemopi 无结构化检索(`searchable:false`)、CRG 无 bug 记忆。
- **3 个 gap(地板漏风)**:
  1. **缺确定性预注入**(最大):[nodes.py:99-100](src/hyperion/workflows/bug_rca/nodes.py#L99) 明确"不预喂历史教训",recall 全靠 delegate 自觉调 MCP 工具(mcp_memory.py:67-69 软引导);deer-flow/mnemopi 都确定性预注入。
  2. **BugLesson 写入字段不全**:[nodes.py:394-398](src/hyperion/workflows/bug_rca/nodes.py#L394) 只填 summary/root_cause/evidence,**没填 symptom/fix_patch/blast_radius_files/commit_sha** → 召回先验缺修复图景(对类似 bug 最有用)。⚠️ 跨切面 gap:R4.1 的 PatchIngestPipeline 也漏 symptom —— schema 定义了字段,生产者没填。
  3. 无自动 query 构造(memory_recall(query) 靠 delegate 自由文本)。

## e2e 实证(机制成立 + 安全)
demo2 wpa e2e:recall 命中 5 条同类 bug,delegate 当先验用,**还主动抓出旧记忆偏差**(falsification:"记忆说 STA 覆盖,但本日志 STA scan 没启动 → 对不上" → needs_revisit → iter1 精确化到 dbus_new_handlers.c:1447)。证明:① similar-recall 真起作用(聚焦搜索);② "先验≠抄答案"设计安全,verify-refine+证伪纪律拦得住盲信旧记忆 → 补强 recall 不引入"懒抄"风险。

## Roadmap(反过度设计:复用 memory_recall,别另造)
- **P0(定 R3 收尾,见下)**:① node_report_memorize 填 symptom/fix_patch/blast_radius/commit_sha(几行,连带填 R4.1 symptom gap 的 bug_rca 这一侧);② ingest 后加确定性 recall 预注入节点,把 top-k 相似 lesson 预进 localize prompt(0 决策 turn,对标 deer-flow DynamicContextMiddleware + Anthropic hybrid 预取+工具)。
- **P1(中)**:自动 query 构造(trigger/日志抽符号 → query,仿 CRG enrich.extract_pattern);跨 codebase 冷启动迁移(可选,scope 隔离站得住)。
- **不做(YAGNI)**:exact-duplicate cache-hit short-circuit;主动 staleness prune(衰减打分已够);另起相似 bug 库(复用 memory_recall)。

## 归属决策:P0 → R3 收尾,不放 R4.1
理由:① 两 P0 都是 bug_rca workflow 自己的改动,R3 管 bug-RCA,R4.1 是另一个 workflow(pr_review),塞进去=耦合;② e2e 已验证只是欠强度,现在补就闭环 R3 的 P3 卖点;③ R4.1 白捡(同 memory_recall);④ symptom gap 跨切面但代码按生产者各归各位(bug_rca→R3、ingest→R4.1.1、pr_review→R4.1 建时)。exact-duplicate short-circuit 不进任何阶段。关联 [[r35-report-handoff]] [[pr-review-design-decision]](symptom gap 同源) [[delegate-already-localizes]] [[avoid-overengineering]]。
