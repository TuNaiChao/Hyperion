# PR 批量分析与聚合报告 — 设计文档(R4.1 · P1 PR 跟踪支柱)

> 状态:**设计完成,待施工(post-R3)**。2026-08-04 调研 + 设计落地。
> 上位:[architecture.md](architecture.md);姊妹篇:[deep-research-design.md](deep-research-design.md)(报告/Verifier 复用)、[memory-design.md](memory-design.md) §6.5(ingest 复用)。

## 0. 这是什么(面向小白)

给 Hyperion 一组 GitHub PR 链接(几十到几百条),它能:

1. **抓** 每个 PR 的补丁(diff)+ 元数据(title/labels/commit message)。
2. **分析** 每条补丁:根因、改了哪些文件/符号、影响面、是否安全相关 → 沉淀成**带溯源的记忆**(去重,重复抓自动合并)。
3. **聚合** 跨所有 PR:这些改动集中在哪几个**模块**?对**安全/功能**有什么影响?谁动了**高风险代码**(hub/bridge)?
4. **出报告** 一篇准确无误的 Markdown(每条结论锚 `PR#:file:line`,Verifier 防幻觉)。

**比喻**:就像给一个代码仓做"年度体检报告"——把这一年合进来的几百个 PR(每一张都是一次小手术)汇总,告诉你"今年主要在动哪个器官(module)、有没有动到要害(hub/安全代码)、整体健康趋势是变好还是变差"。单条 PR 的"病历"是 R3.4 已经会做的;R4.1 新做的是**把几百份病历汇总成体检报告**。

**差异化**:记忆 + 溯源 + 持续学习(每次跑都增量沉淀,下次跑能"想起来"上次的分析)。这正是 Hyperion 三支柱里 P3(记忆)与 P1(调研)的交汇。

## 1. 前沿对齐(2025–2026,已核验)

| 方向 | 先例 | 对 R4.1 的启发 |
|---|---|---|
| **批量变更聚合报告** | SmartNote(arXiv [2505.17977](https://arxiv.org/abs/2505.17977),已 WebFetch 核验) | 管线 = 聚合变更 → 用 code/commit/PR 细节喂 LLM 描述总结 → **分类(categorise)→ 打分(score)→ 结构化按优先级报告**。R4.1 报告层的范式 |
| **PR 级影响分析** | PR-based change impact analysis(Springer EMSE,[link](https://link.springer.com/article/10.1007/s10664-024-10600-2);call-graph 依赖 + 历史挖掘,PR 粒度) | "改了哪些模块 + 影响面"的学术名。本地 CRG 已实现 |
| **跨 N 条聚合** | map-reduce + 聚类(LangChain 标准模式) | 按模块/主题分桶 → 每桶 LLM 聚合 → 合并。不用从零造 |
| **抓 PR diff** | `gh pr diff` / URL 加 `.patch` / `gh api --paginate`([GitHub REST](https://docs.github.com/en/rest/pulls/pulls)、[gh 手册](https://cli.github.com/manual/gh_pr_diff)) | 5000 req/h 认证额度,ETag 304 不计;别一条一个进程 |
| **安全相关性判别** | [vulnerability-spoiler-alert-action](https://github.com/spaceraccoon/vulnerability-spoiler-alert-action)(diff+msg 送 LLM)、[CommitShield](https://arxiv.org/abs/2501.03626)、"很多安全 fix 从不打 CVE 藏在 commit 里" | 分层:关键词/risk 预筛 → 命中子集才 LLM 深分类 |

> 核验笔记:搜索给的 "ToM arXiv 2511.004490"(Tree-oriented MapReduce)经核验**实际是脑瘤分割论文,张冠李戴** → 不引(map-reduce 是公认常识,不需借这篇)。引 arXiv 前先 WebFetch 核验的铁律又一次拦住错误引用。

## 2. 架构:三块新拼图 + 全量复用

```
PR URLs / 文件 / gh api
  │
  ▼ ① GitHub 抓取层(新 · 唯一硬缺口)
  services/github/pr_fetcher.py  —— 借 deer-flow github_api.py:51(token/重试/速率骨架)
       + robomp github_backend.py:28(接口形状)+ issue_index.py:102(watermark 断点续传)
  → [(pr_meta, diff_text), ...]
  │
  ▼ ② 批量编排(半新 · 套 deep_research 五件套)
  workflows/pr_review/  —— graph/state/fan-out/report/verify 全照 deep_research
  每 PR(并发 Semaphore=3,对 GitHub API 友好):
       PatchIngestPipeline.run()  (直用 ingest.py:274,diff→retrieve→LLM→KI)
       + CRG analyze_changes(changed_files → risk_score/impact)   ← 新接
       → svc.memorize  (直用,去重/合并/related 全就位)
  │
  ▼ ③ 聚合 + 报告层(新)
  按 module(CRG community_id)+ theme(安全/功能/重构/性能)分桶
       安全子集:CRG risk_score + SECURITY_KEYWORDS 预筛 → 命中才 LLM 深分类(CWE/taint)
       每桶 map-reduce 聚合 summary
  → render_report(§4 模板,cited-reporter 锚 PR#:file:line)
  → _verify_report_citations 逐条核验(虚假引用 = 0 才合格)
  → 聚合结论抽 codebase_fact 入记忆(P1→P3 闭环)
```

### 复用面(别重造,均已核实 file:line)

| 能力 | 位置 | 复用方式 |
|---|---|---|
| 单补丁→记忆 | `ingest.py:274` PatchIngestPipeline(解析 :160 / retrieve :329 / 抽根因 :366 / 组装 KI :391 / **id 按 diff 算** :415) | 直用,补 `pr_url`/`pr_number`/`symptom` 字段 |
| 记忆去重/合并 | `memorize.py`:Bayes 合并 :33 / related 连边 :61 / supersede :109 / 出口 :135 | 直用;**补 symptom 让 `_same_subject` :79 跨 PR 去重生效(现 gap)** |
| 批量并发 | `deep_research/_research.py:290` `Semaphore(3)`+`gather`,单条失败不连坐 | 直用 |
| 报告 + 防幻觉 | `deep_research/report.py:14` 渲染 + `_verify.py:89` Existence@Line | 套结构、换维度 |
| 检索 | `code_index/retrieval.py:236` retrieve | 直用(repo 需先 `hyperion index`) |
| CRG 影响面/模块/风险 | `get_impact_radius` graph.py:742 / `analyze_changes` changes.py:381 / `compute_risk_score` :312(**内置 SECURITY_KEYWORDS +0.20**)/ `get_community_ids_by_qualified_names` graph.py:1227 / `find_hub/bridge` analysis.py:14/58 / `get_architecture_overview` communities.py:1020 | 扩 `structural.py:59` 接入(现只用了最浅的 callers/callees) |

## 3. 关键设计决策

- **D1 数据模型 —— 不加 kind、不加 scope 维度**。PR 标识塞进 `tags=["patch_insight","pr:<o>/<r>#<n>","module:<x>"]` + `source`=PR URL + `commit_sha`=merge commit;`SourceTier.imported`(0.6)直用。Scope 是"记忆空间隔离=(人,库)"(schema.py:71),PR 号是单条知识属性不是隔离维度,不该进 Scope。kind 用现有 `bug_lesson`(单条 PR 教训)+ `codebase_fact`(聚合结论),不新增(ingest.py 顶部已论证加 kind 要动 schema/extract/consolidate/FTS,不值)。**但必须补 `symptom` 字段**(bug_lesson 专用字段 schema.py:144-147 已有,只是 PatchIngestPipeline 没填)—— 否则 `_same_subject`(memorize.py:79)对补丁 KI 返 False → 跨 PR 修同根因不会触发 supersede。这是 R4.1.1 要修的真 gap。
- **D2 去重 —— id 按 diff 内容算**(R3.4 已验证:同 patch 重抓 → 同 id → Bayes 合并,conf 0.30→0.43)。同 PR 重抓自动合并。**跨 PR 语义近邻去重(不同 PR 修同一 bug)→ backlog**,需 embedding 聚类,不在 R4.1 首发。
- **D3 影响面 —— 扩 `CrgStructuralBackend`**(`structural.py:59`)接 `get_impact_radius` + `analyze_changes`。现在手握每个 PR 的 `changed_files`,正是 structural.py:44 注释里挂了半年的 backlog("get_impact_radius 吃 changed_files 不吃自然语言,放 R3")的天然入口。全图预算 `find_hub_nodes`/`find_bridge_nodes`/`get_all_community_ids`(graph.py:1274)跨 PR 复用(只算一次)。
- **D4 模块归属** —— `get_community_ids_by_qualified_names`(graph.py:1227,450 条一批)把每个 PR 的 changed 节点归到模块;`get_architecture_overview`(communities.py:1020)的 `cross_community_edges` = **模块耦合热力图**(一个 PR 改多个社区 = 高耦合改动,架构级信号)。
- **D5 安全分析 —— 分层省 token**(对齐"执行信号分层"哲学)。① CRG `compute_risk_score`(六因子,**名字命中 `SECURITY_KEYWORDS` → +0.20**,changes.py:312)给每条 PR 一个 risk_score;② 关键词/risk 预筛出"安全相关"子集;③ **只对子集**送 LLM 深分类(CWE 类别 / 引入还是修复漏洞 / taint 路径,仿 vulnerability-spoiler-alert-action)。不全量 LLM —— 几百条 PR 才烧得起。
- **D6 报告防幻觉** —— 每条结论锚 `PR#:file:line`(cited-reporter 理念,deep_research `_research.py:73`),`_verify_report_citations`(`_verify.py:89`)逐条回查,虚假引用 = 0 才合格(沿用 R3.2 硬闸)。报告里所有数字(N 条 PR、M 个模块、X 条安全相关)都从结构化聚合来,不让 LLM 凭空报数。

## 4. per-PR 分析深度:分层(用户已拍板)

| 档 | 触发 | 干啥 | 成本 |
|---|---|---|---|
| **轻量(默认)** | 所有 PR | `PatchIngestPipeline.run()`(diff→retrieve→1 次 LLM→KI)+ CRG `analyze_changes`(risk_score/impact,纯图算不调 LLM) | ~1 次 LLM/PR |
| **深度(自动升级)** | risk_score 高 / 安全相关 / 命中 hub/bridge 的子集 | 套 deep_research `_research_one_module`(:223):ReAct 子 agent + TurnBudget + 撞墙强制收尾,能读相关代码 + 交叉 CRG + 看 commit history | 数十次 LLM/PR,但只跑子集 |

分层的好处:几百条 PR 里通常只有一小撮是"重点"(安全相关 / 动了 hub / 大 blast radius),把它们挑出来深挖,其余走轻量 —— 既省 token 又保重点。这跟 Hyperion 一贯的"执行信号分层"一致(硬门控 > 对抗审 > 自评;这里是:CRG 图算 > 轻量 LLM > 深度子 agent)。

## 5. 报告模板(先用这个,格式之后再议)

```
1. 元数据          仓库 / PR 数 / 时间范围 / scope(owner, codebase) / 生成时间
2. TL;DR           N 条 PR、M 个模块被触及、X 条安全相关、Y 条改了 hub/bridge、整体风险评级
3. 改动全景(图驱动) CRG 社区/模块分布热力图 + cross_community_edges 耦合;按模块的 PR 数/改动量
4. 按模块深挖       每模块:PR 列表 + 共同主题(map-reduce 聚合) + blast radius + 风险分
5. 安全影响         安全子集逐条:CWE 类别 / 引入还是修复漏洞 / 影响面;高危单列
6. 功能影响         按域聚类(新功能 / bug 修复 / 重构 / 性能) map-reduce 汇总
7. 高风险改动       命中 hub/bridge 的 PR + 大 blast radius + 跨多社区高耦合 PR
8. 合入建议         优先 review 哪些 / 哪些模块在腐化(churn 高) / 潜在回归风险
9. 来源与核验       每条结论锚 PR#:file:line + Verifier fact-check 结果(虚假引用数 = 0)
```

## 6. 分阶段计划(R4.1)

定位:CLAUDE.md 路线 R4「PR 跟踪」支柱的第一步,post-R3。每步独立可验。

| 步 | 内容 | 复用 / 新 | 退出标准 |
|---|---|---|---|
| **R4.1.0** | `services/github/pr_fetcher.py`:URL/文件/`gh api` → `[(pr_meta, diff_text)]`;watermark 断点续传(几百条挂了能续);GITHUB_TOKEN 走 .env | 新(借 deer-flow `github_api.py:51` + robomp `github_backend.py:28` + `issue_index.py:102`) | mock gh 单测 + 真仓小批量抓取成功率 |
| **R4.1.1** | PatchIngestPipeline 直用 + 补 `pr_url`/`pr_number`/`symptom` 字段 + tags 带 `pr:`/`module:` 键 | 扩 `ingest.py:391` `_assemble_ki` | 同 PR 重抓合并(id 不重复);跨 PR 修同根因触发 supersede(symptom 修复) |
| **R4.1.2** | 扩 `CrgStructuralBackend`(`structural.py:59`)接 `get_impact_radius`+`analyze_changes`+全图预算 hub/bridge/community 索引(跨 PR 复用) | 扩 structural.py | 单 PR changed_files → 模块归属 + blast radius + risk_score 对得上人工 |
| **R4.1.3** | 聚合层:按 module+theme 分桶 + CRG risk_score/keyword 预筛安全子集→LLM 深分类 + 每桶 map-reduce summary | 新 | 桩 KI 聚合单测;安全子集只命中预期那几条 |
| **R4.1.4** | `workflows/pr_review/`(套 deep_research 五件套 graph/state/fan-out/report/verify)+ §5 模板 + cited-reporter + Verifier + 聚合结论抽 codebase_fact 入记忆 | 新(套模板) | 报告渲染 + 虚假引用=0 + 记忆 recall 命中 |
| **R4.1.5** | CLI `hyperion pr-review --urls <file\|-> --repo <path> --codebase <name> [--owner] [--concurrency 3] [--deep]`;`--deep` 强制全深度(默认分层) | 新(照 `cmd_research` cli.py:347) | e2e:真仓几十条 PR → 报告 + 0 虚假 + recall 命中 |

### 验证闸(诚实,跑测时正文打印"测了啥+期望+实际")
- 抓取成功率 / 分析成功率(单条坏 PR 降级不连坐)。
- 同 PR 重抓 → 合并(id 不重复,R3.4 机制延伸到 PR)。
- 报告 Verifier 虚假引用 = 0。
- 模块分布对得上人工抽查。
- 记忆 recall 命中聚合结论(P1→P3 闭环)。
- METR 式诚实标注:报告里的"安全影响"是**启发式 + LLM 判断,非形式化验证**,高危结论标"建议人工复核"(不假装 100% 准)。

## 7. 风险与对策

| 风险 | 对策 |
|---|---|
| GitHub 速率限制(5000 req/h) | `gh api --paginate` + ETag 304 + watermark 断点续传 + Semaphore(3);diff 走 `.patch` 端点省请求 |
| 几百条 PR 的 token 成本 | 分层深度(默认轻量 ~1 LLM/PR,只子集深挖);安全分类只跑预筛子集;聚合 map-reduce 而非全量塞一个 context |
| CRG 图未建/iggraph 缺 | 前置 `hyperion index` + `CodeGraph.full_build`;igraph 缺则社区降级文件聚类(deep_research 已处理,F9);文档标注 |
| LLM 聚合幻觉(编 PR 不存在的改动) | cited-reporter 锚 PR#:file:line + `_verify_report_citations` 硬闸;数字从结构化聚合来不让 LLM 报 |
| 跨 PR 语义重复(不同 PR 修同 bug) | D2:id 按 diff 去重只管同 patch;语义近邻去重进 backlog(embedding 聚类),首发不做、文档标清 |
| 私有仓鉴权 | GITHUB_TOKEN 走 .env(gitignore);只做布尔/非空检查,永不打印值 |

## 8. backlog(pull-by-need,踩到痛点再补)

- 跨 PR 语义近邻去重(embedding 聚类,"不同 PR 修同一 bug"合并)。
- PR review 评论 / CI 状态接入(robomp `github_backend.py` 的 `list_pr_reviews`/`list_review_comments` 接口形状已备)。
- "PR 一开就自动分析"的 webhook 触发(deer-flow `backend/app/gateway/github/` 是单 PR 事件路由范式,参考价值低,按需)。
- changelog 风格章节(oh-my-pi `scripts/ci-release-notes.ts` 是 TS,逻辑可瞄)。
- 图快照对比(`graph_diff.py` take_snapshot/diff_snapshots):PR 前后建两图对比符号级变更(比 blast radius 更细,按需)。
- 多仓 / 团队共享(R4 租户隔离落地后)。

---

> 关联记忆:[[research-deerflow-first]] [[avoid-overengineering]] [[delegate-already-localizes]](别重造,先复用 PatchIngestPipeline) [[r34-ingest-handoff]](单补丁→记忆已就绪) [[verify-arxiv-cites-before-commit]](ToM 张冠李戴已拦)。
