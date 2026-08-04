# 代码仓深度调研工作流 — 设计文档(P1)

> 状态:**R3.2 ✅ 代码全完(2026-08-03)** — 六节点 workflow + CLI 已实现,单测全绿、#58 权威 index 回归绿;**只剩 research e2e 待跑**(退出闸见 §7)· 实现阶段:R3.2(PR 跟踪子项 R4)
> 上位文档:[architecture.md §3/§7](architecture.md) · 参考:**DocAgent**(多 agent 代码文档生成)+ gpt-researcher(行内引用)+ code-review-graph(结构图)+ Aider repo-map + storm(多视角提问)
>
> **R3.2 起点**:复用 bug-RCA 已验的 **verify-refine 双循环骨架** + `memory.memorize` + 共享底座(code_index/CRG/MCP 工具),不重起炉灶。⚠️ ~~复用 localize 漏斗~~ —— bug-RCA 2026-07-31 已砍 Hyperion 侧漏斗(opencode 自定位),深度调研不复用它(各有各的 workflow,见 §1)。**事实一致性(原"rerank B 档")**:research 阶段采 N 条独立轨迹 → 事实 de-dup + 出现频次作置信度。⚠️ 原计划复用 `bug_rca/rerank.py`,但 **patch 投票 rerank 已于 2026-07-31 整体移除**;R3.2 若要事实一致性投票,**届时自建小原语,不预借已删的 rerank.py**(YAGNI;调研轨迹多样性高、与 patch 投票不同,到时按需评估)。
>
> ⚠️ **2026-07-30 审核纠正**:旧版引用「deer-flow Reporter(cited Markdown)」。实测本地 deer-flow 经全仓 grep 确认**无 Reporter/Researcher/Planner/Coordinator、无 `src/graph/`**(经典管线已重构为单 agent + 中间件链,只剩 prompt skill)。**故 cited-reporter 由 Hyperion 自建**,借鉴 DocAgent(代码域 + 对代码库 fact-check)+ gpt-researcher 行内引用。

---

## 0. 这是什么(面向小白)

**给 Hyperion 一个代码仓库(git 链接或本地路径),它产出一份"这个库怎么读"的详细文档**——设计架构、关键模块怎么实现的、入口和执行流在哪、哪里是高风险/难维护的地方。让人(或 agent)接手陌生大库时不用从零啃。

**与 bug-RCA 的关系:** 同一套底座(记忆 + code_index + code-review-graph + 委托)。调研产出的"代码库知识"沉淀进记忆,**正好喂给后续的 bug-RCA** 用——两者形成闭环(P1 调研给 P2 RCA 铺地基)。

**多语言:** 先 Python + C(bluez/wpa 是 C);C parser 已有(R2 为 wpa 补);code-review-graph 经 tree-sitter 已多语言;`c.tags.scm`(repomap 用)— **repomap 改 backlog(CRG 为主,见 §2 决策)**。

---

## 1. 工作流(R3.2 ✅ 已实现 — 自有六步;复用 bug-RCA 的 MCP 工具底座 + memorize)

> **2026-08-03 落地状态**:六节点线性 workflow 已实现(`workflows/deep_research/{state,graph,nodes,report,_research,_verify}.py`),CLI `hyperion research --repo <path> --codebase <name>`。两处关键决策(均用户拍板):
> ① **架构地图 = CRG 为主**(`code_graph.py`:detect_communities + architecture_overview + hub/bridge;repomap 列 backlog,见 §2);
> ② **research = 每模块 ReAct 子 agent**(`create_hyperionagent` + 默认中间件 [Summarization+LoopDetection+TokenBudget] + 闭包 nav 工具,asyncio.gather 并发帽 3),**cited-reporter** 强制每结论锚 file:line(emit-concept 防幻觉),**Verifier** 写后回查文件存在。
> 剩余中间件 / SubagentExecutor / 多轨迹一致性 / 逐符号 Verifier 均 pull-by-need(见 §8)。

```
START → 1.ingest(git clone / 本地路径,注册 scope)
     → 2.index(code_index 建语义索引 + code-review-graph 建结构图 + repomap)
     → 3.plan(按报告骨架拆子问题/子模块;可选 delegate 协助)
     → 4.research(并行:每子模块用 自有工具 + 可选 delegate 深挖,带 file:line 证据)
     → 5.report(渲染架构/模块文档,§5 骨架)
     → 6.memorize(抽 CodebaseFact 入记忆)→ END
```

| 步 | 动作 | 关键点 |
|---|---|---|
| **1 ingest** | `git clone` 或本地路径;注册 `(owner, codebase)` scope | 复用 code_index.index 的原子/增量建索引 |
| **2 index** | code_index(语义)+ **code-review-graph**(结构图:函数/类/调用/继承/测试 + 社区 + hub/bridge)+ **repomap**(Aider 式) | 三张图是调研的证据来源 |
| **3 plan** | 按报告骨架(§5)拆子模块/子问题;**storm 式多视角提问**生成深挖大纲(安全/性能/维护者视角) | 决定"每个模块要回答哪些问题" |
| **4 research** | 并行深挖每子模块;可用 delegate(omp/opencode)读代码 + Hyperion 自有 nav 工具;**gpt-researcher 式行内引用纪律** | 每个结论锚 file:line |
| **5 report** | 渲染架构/模块文档(§5) | code-review-graph 的架构地图作为一等章节(图驱动,非 LLM 瞎编) |
| **6 memorize** | 抽 `CodebaseFact`(module/symbol/architecture,**结构化事实**:模块职责 + 公开签名 + 调用边,非裸 chunk 重述)入记忆,带 commit SHA | 闭环:喂给后续 bug-RCA(structured facts > chunks,RepoGraph 实证 identifier-EM 翻倍) |

---

## 2. Aider repo-map(原 R3 新增 → **2026-08-03 改 backlog,CRG 为主**)

> ⚠️ **2026-08-03 决策(用户拍板)**:本节原计划 R3.2 移植 aider repomap。核查发现 **aider `c-tags.scm` 没有 C 的引用查询**(只有定义)→ PageRank 对 wpa/bluez 这种 C 仓会因缺 ref 边而失真;而 **CRG 对 C 抽了 `call_expression` 调用边** + 自带 `detect_communities`/`get_architecture_overview`/`find_hub_nodes`/`find_bridge_nodes`,且 2026 趋势是持久图(CRG)非每次重算 map。故 **R3.2 架构地图改以 CRG 为主**(`services/code_index/code_graph.py` 已落地),repomap 移植列 backlog(planner 真需要"整仓一屏符号树"再评估)。下方原设计保留作 backlog 参考。

> 借自 [Aider-AI/aider](https://github.com/Aider-AI/aider)(~48k,Apache-2.0)的 `aider/repomap.py` + `queries/<lang>/tags.scm`。这是 P1 调研的**最高杠杆单点借鉴**。

**是什么:** 用 tree-sitter 的 `tags.scm`(查询文件)抽出全仓所有"定义 + 引用",建一张**符号引用图**,对它跑 **PageRank**,得到"全仓最重要的 N 个符号",再按 **token 预算**裁剪成一张"仓库地图"。一眼看出这库的核心在哪。

**怎么落到 Hyperion:** 新增 `services/code_index/repomap.py`,**叠在已有 `parser.py` 上**(parser 已用 tree-sitter 抽符号,repomap 复用它的符号抽取 + 加 references 边 + PageRank):

```
parser.py(已抽 defs)→ + tags.scm 抽 refs → networkx 建图 → PageRank
  → get_repo_map(repo, map_tokens=1024) → 排序后的"最重要符号"地图
```

- 抄 Aider 的 `tags.scm`(各语言);**补 `c.tags.scm`** 供 bluez/wpa 用。
- 这张地图既是调研报告"系统架构/关键模块"章节的骨架,也是 bug-RCA 委托前组装上下文的"全局视角"补充(给 delegate 看全仓重点)。

> Aider repo-map 在 v0.1 架构里被标"延后";v2 因 P1 调研支柱而**提前到 R3**(并继续服务 bug-RCA)。

---

## 3. code-review-graph 集成(结构侧引擎)

> [tirth8205/code-review-graph](https://github.com/tirth8205/code-review-graph)(~26.5k,MIT):Tree-sitter → SQLite 图(函数/类/调用/继承/测试)+ Leiden 社区 + blast-radius + 30 MCP 工具。

**Hyperion 用它做调研的"结构真相源":**
- `get_architecture_overview` → 社区图 + 耦合告警 → 报告"系统架构"章节(自动生成,非 LLM 想象)。
- hub/bridge 节点(betweenness centrality)→ "结构风险"章节(架构瓶颈点)。
- 意外跨社区边 / 测试盲区 → "知识缺口"。
- `list_flows`/`get_affected_flows` → "入口与关键执行流"章节。

**接入方式:** clone 进仓库(已在 .gitignore),Hyperion 经 MCP 或直接 import 调它的工具。它也是记忆核心 native 后端的"结构检索"那条腿(见 [memory-design.md §4](memory-design.md))。⚠️ `detect_communities`/`get_architecture_overview` 需装 igraph extra(`uv add code-review-graph[communities]`),否则静默降级文件聚类(F9)。

---

## 4. 调研方法论(综合三方)

| 借鉴 | 来源 | 用在哪 |
|---|---|---|
| **多 agent 代码文档生成(Reader/Searcher/Writer/Verifier + 对代码库 fact-check)** | [DocAgent](https://arxiv.org/abs/2504.08725)(ACL 2025)+ gpt-researcher 行内引用 | report 步:cited MD + 每结论锚 file:line + 写完对代码库核验(deer-flow harness 无 Reporter,见文首纠正) |
| **行内引用纪律** + 并行执行 | [gpt-researcher](https://github.com/assafelovic/gpt-researcher) | 每结论 → source file:line |
| **多视角提问**生成大纲 | [storm](https://github.com/stanford-oval/storm) | plan 步:模拟 安全/性能/维护者 视角问"这模块该深挖啥" |
| **图驱动架构地图** | code-review-graph | report 的"系统架构"章节 |
| **多视角生成 + 事实一致性** | storm 多视角 | N 条轨迹事实 de-dup,出现频次作置信度(共识 = 可靠);投票原语届时自建(`rerank.py` 已移除) |

---

## 5. 报告骨架(代码仓调研报告,Markdown,带溯源)

```
1. 元数据 + 溯源(repo, commit SHA, 生成日期, 模型/provider, 图统计 节点/边/社区, token 预算)
2. TL;DR(3-5 句:这系统是啥、干啥用的、最关键的架构洞察)
3. 系统架构(分层/组件图 + code-review-graph 社区图 + 耦合告警 + 语言构成)
4. 入口与关键执行流(按关键性排序的调用链)
5. 关键模块深挖(每个模块一节)
   └ 用途 / 公开面(导出函数类型)/ 关键内部类型 / 依赖(进出)/ 数据与控制流 / 持久化/状态 / 值得注意的设计决策
6. 横切关注点(错误处理 / 配置 / 日志可观测 / 安全鉴权 / 并发模型)
7. 结构风险(hub 瓶颈 / bridge 节点 / 意外跨社区耦合 / 测试盲区)
8. 未决问题 / 后续调研目标
9. 来源(每条结论的 file:line,锚到 commit SHA)
```

> 调研报告同时整体存为 `ResearchReport` 文档,并抽取 `CodebaseFact` 入记忆。

---

## 6. PR 持续跟踪 + 合入建议(R4 子项)

> v0.1 工作流③,cron + Send map-reduce。v2 放 **R4**(团队/多库之后)。

```
cron(每天) → GraphQL 增量拉上游 bluez/wpa 近期 PR(updatedAt 过滤 + cursor 分页)
           → Send→ PR-reviewer ×N(并行,每 PR 独立上下文)
               ├ fetch diff
               ├ 影响面(blame + 调用图 + 依赖,复用 code_index + code-review-graph)
               ├ 与本地分支冲突评估
               └ 输出决策卡(JSON)
           → reduce 汇总 → "合入/cherry-pick/观望/跳过" 清单 → memorize
```

**决策卡**(借鉴 PR-Agent/CodeRabbit):`recommendation`(合入/cherry-pick/观望/跳过)+ `confidence` + 多维评分(safety/compatibility/dependencies/test_coverage/upstream_stability)+ `conflict` + `action_items`。GraphQL 轮询(一次拿全 PR+reviews+comments+files,省额度)。

---

## 7. R3 退出标准(可验证)

1. `uv run hyperion research --repo <wpa 或 bluez 路径>` → 产出一份架构/模块文档 `.md`。
2. **CRG 覆盖率门槛 + 人工抽查**:系统架构章节的社区/hub 来自 code-review-graph 自动产出(覆盖率 ≥ X%);辅以人工抽查模块深挖小节锚 file:line、覆盖关键执行流(F10:把主观抽查升级为可验证)。
3. `repomap` 产出的"最重要符号"地图合理(核心模块在前)。
4. 产出抽成 `CodebaseFact` 入记忆;后续 bug-RCA 能 `recall` 命中这些事实(验证 P1→P2 闭环)。

## 8. 待办(记 backlog)

- ~~`tree-sitter-c` 接入 parser~~(✅ 已有,R2 为 wpa 补);`c.tags.scm`(repomap 用)— **repomap 改 backlog(CRG 为主,见 §2 决策)**。
- repomap 的 token 预算 / PageRank 阻尼参数调优(若 backlog 启动)。
- 多语言 grammar 扩展(Python+C 之外按需)。
- PR tracker 的 GraphQL 增量 + 决策卡(R4)。
- 调研报告"多视角提问"的视角集固化。
- **R3.2 pull-by-need(2026-08-03 定,踩到痛点再补)**:
  - 中间件:DynamicContext(注入日期 + 记忆 recall)/ DurableContext(summary 投影)/ ToolErrorHandling / LLMErrorHandling(factory.py:40-56 路线图已留槽位)。
  - SubagentExecutor 精简版(research 现用 asyncio.gather + semaphore 最小 fan-out;可观测要求上来再升级成并发帽 + token rollup + registry)。
  - 多轨迹事实一致性 rerank(N 条独立轨迹 → 出现频次作置信度;现 MVP 单轨迹 per module + Verifier fact-check 够防幻觉)。
  - CRG 社区 → 文件覆盖率(现 Verifier 报「模块覆盖率」= 带引用的模块占比;社区级需成员节点 → 文件映射)。
  - Verifier 逐符号 `parse_file` 核验(现只查「文件存在」;逐符号 symbol@line 更严)→ **归入 R3.2.x P3**。
  - LoopDetection 频次层 + consume_stop_reason + run_id 作用域(现单层 hash + thread_id)。

- **R3.2.x 纵向加深(路线 A,2026-08-03 规划)** —— 把已 GREEN 的 deep_research 做深做强,与 R3.3/R3.4 正交。**只 P1 治痛点,P2-P4 数据驱动/pull-by-need。** 详见 todo.md「R3.2.x」段。
  - **P1 · `TurnBudgetMiddleware`** ✅ 已完成+pushed(commit 3dc7299/0843af2;原标"★ 痛点,高价值低成本"):现状 research 子 agent 8/8 撞 `recursion_limit` 硬墙 → `astream+catch+裸模型` 降级(finding 质量次)。**调研纠正**:核 deer-flow 源码确认其 turn 轴**也是 catch `GraphRecursionError`**(`executor.py::_aexecute`),strip 只用于 token/loop 两轴(这俩 Hyperion 已有:`loop_detection.py` / `token_budget.py`)。**真缺口 = 没有中间件管 turn 数**(LoopDetection 只管相同 tool_call 重复、TokenBudget 只管 token;良性探索两个都不触发)。**做法**:新 `platform/runtime/middlewares/turn_budget.py`,把 `TokenBudgetMiddleware` 的 warn+hard 两段模式搬到 turn 轴 —— `after_model` 计数(=ReAct 轮数)→ 第 `max_turns-1` 轮排队 warn("立刻输出 JSON 收尾")→ 第 `max_turns` 轮 strip `tool_calls` + `finish_reason=stop`。常规情形模型自收尾(比裸模型重述连贯),`GraphRecursionError` catch 降为**永不触发的兜底**。**recursion_limit 按真实中间件数动态算**(踩坑 #9:中间件 hook 是独立图节点,每轮 ReAct 消耗 `1(model)+N(after_model 链)+1(tools)=N+2` superstep,不是 2;固定 `(max_turns+2)*2` 会撞墙),`_research_one_module` 里 `recursion_limit = max_turns×(len(middleware)+2)+2×len(middleware)+20`。分桶键用 `thread_id`(同 LoopDetection;`runtime.context` 无 `run_id`、`id(runtime)` 每 superstep 变,见踩坑 #9)。比 deer-flow(catch+抢残文)和现状(catch+裸模型)都优。接入 `build_default_middlewares`(默认宽 `max_turns=50`;research 子 agent 经 `turn_budget` 配置传紧值 `_MAX_TURNS`)。**编辑模式**:middleware 核心 窗口展示·用户手敲;factory/_research cfg/test 我改。
  - **P2 · plan LLM 命名 + STORM 多视角 focus** ✅ 已实现(2026-08-04,详见下文 R3.3.1;原标"中价值低成本,可选"):`node_plan`(nodes.py:108)现所有模块共用一个 focus 字符串、社区名可能是 "community-3"。改:一次 LLM batch 调用(喂全部候选社区的 member_files+key_symbols)为每社区产 人话模块名 + 2-3 针对性子问题(STORM:核心数据结构?对外接口?主调用链?)。失败降级通用 focus。**边际改进非痛点**,P1 后看 finding 质量再定。
  - **P3 · Verifier symbol@line(中价值中成本,可选)**:现 `_verify.py:31` 只查 `fp.exists()`。改核 **state 结构化 citations**(`findings[].citations[]` 有 {file,line,symbol,claim})而非 regex 抠报告 → `parse_file(fp)` 验 symbol 存在且 `[start,end]` 含 line。stats 细化 verified_symbols。**先量"文件真 symbol 假"实际比例,>5% 再做**(cited-reporter 已约束,文件存在性已抓最硬幻觉)。
  - **P4 · 增量调研(高价值高成本,pull-by-need)**:存上次 commit SHA → `git diff` → CRG 增量更新图(需核实 CRG `update_build` API 边界)→ 重算受影响社区 → 只重研究这些模块。**deer-flow/GPT-Researcher 都没此能力**(无结构图基线)= Hyperion 独特壁垒。排 R3.4 之后。

### R3.3 深度调研加深(2026-08-04 调研驱动 — P2+P3 实施 / P4 顺延 backlog)

> 4 个深挖 agent(STORM / DocAgent / 增量 / 防幻觉)+ deer-flow `deep-research`/`github-deep-research` SKILL.md。用户拍板范围 = **P2+P3 质量加深**;P4 增量顺延。原 R3.2.x P1(TurnBudget)已完成,算前置稳定化。原 R3.3(opencode serve + report 精修)顺延为 R3.5。
>
> **进度(2026-08-04)**:✅ R3.3.1(P2 plan LLM + STORM)已实现 + 6 测绿。⚠ R3.3.2(P3)/ P4 段 arXiv 已逐一核验(4 篇全真,非编造):2512.12117 数字订正(100%→92% accuracy)、2607.00895「§4.3 引用接地」系张冠李戴已换 LSPRAG(2510.22210);2605.06635 / 2604.26523 精确吻合。

**R3.3.1 = P2 plan 加深** ✅ 已实现(2026-08-04,`_plan.py` 核心 + `node_plan` 接线 + 6 测绿):
- **① LLM plan batch(★本期核心)**:一次 batch 调用,喂每社区(member_files + key_symbols)→ 产 人话模块名 + 2-3 STORM 子问题。**p0 基础事实视角 always-on**(职责/公开面/入口,保 §5 骨架覆盖;STORM p0 模式)+ 1-2 附加视角(安全/性能/维护者,按模块类型选;**视角去重**——security/robustness 易重叠)。失败降级通用 focus。新 `_plan.py`(窗口展示·用户手敲)。
- **② DocAgent 拓扑序 → Tier 2(pull-by-need)**:核实现状——CRG `architecture_overview` 只暴露 `warnings`(**无向**高耦合对告警),**无有向跨社区边**;真做拓扑序需新建 CRG 跨社区边聚合 + wave 执行传上游 digest 才有 DocAgent ~8pt truthfulness 收益(**价值依赖传播**,纯排序价值低;中等成本)。按 YAGNI(踩坑 #1/#2 先量再建):先 ship ① 量 finding 质量,跨模块不一致真出现再建。
- **编辑模式**:plan LLM prompt + batch 解析 **窗口展示·用户手敲**(`_plan.py`);node_plan wiring(`nodes.py:80-117`)/ config / test 我改。
- **验证**:`uv run hyperion research --repo example/demo2/wpa --codebase wpa` → 模块名是人话、focus 按社区定制(p0 + 视角);单测 batch LLM(桩)+ 降级路径。

**R3.3.2 = P3 Verifier 硬化** ✅ P3.1 已实现(2026-08-04,commit 2e5747b:逐符号@行 + Existence@Line Ratio;P3.2 LSP fallback / P3.3 打分块 pull-by-need)。原现状:`_verify.py:31-38` 只查 `exists()`,不验 symbol@line;但 cited-reporter 把结构化 citations 存 state `findings[].citations[]`{file,line,symbol,claim},parse_file API 子 agent 已在用 `_research.py:155`):
- **① 机械引用门控(核心 ~20 行)**([arXiv 2512.12117](https://arxiv.org/html/2512.12117v1),Citation-Grounded Code Comprehension):regex 抽 `[file:start-end]` + 区间重叠检查 → **92% citation accuracy、0 幻觉**(原文实测,非"100% precision")。Hyperion 落地:核 **state 结构化 citations**(非报告 regex)—— `parse_file(fp)` 验 symbol 存在且 `line ∈ [symbol.start, symbol.end]`。
- **② 引用接地先于判假**([LSPRAG arXiv 2510.22210](https://arxiv.org/abs/2510.22210),LSP 后端实时给精确符号定义/引用;原引 2607.00895「§4.3」经核验对不上——该文实为 span-level 幻觉检测 benchmark,非"引用接地",故换):symbol 在 file:line 找不到时,**先 parse_file 全文件搜 / LSP go-to-def** 解析(防 pitfall #6:符号真但行偏移、或定义在头文件)再判假。降 false positive。
- **③ Existence Ratio 指标**([DocAgent §3.3](https://arxiv.org/abs/2504.08725)):抽报告每个 function/struct/macro/file 提及 → 对 CRG 节点集 + code_index symbols 核验 → `verified_symbols / extracted_symbols`,替粗粒度 `module_coverage`(`_verify.py:42`)。能机械抓 pitfall #6 红鲱鱼。
- **④ 影子模式先量**:新门控与旧 exists() 并行跑,量「文件真 symbol 假」实际比例;**>5% 才转硬门控**(strip/标红),否则留透明统计(cited-reporter 已约束,可能虚惊)。
- **⚠ 警示**([arXiv 2605.06635](https://arxiv.org/html/2605.06635),Cited but Not Verified):Fact Check 随检索量 2→150 反降 ~42%;Link/Relevance 保持。R3.2「80 引用」成功指标 **necessary-but-not-sufficient**,要补 Fact-Check 对应项——**不追更多引用,要每条都真**。
- **编辑模式**:symbol 核验 + 接地逻辑 **窗口展示·用户手敲**;`_verify.py` wiring / Existence Ratio metrics / report Verifier 段 / test 我改。
- **验证**:Verifier 章节 zero 虚假 symbol;Existence Ratio 打印;影子模式量出「文件真 symbol 假」比例。

**R3.3 pull-by-need**(数据驱动,踩到痛点再补):P3 = span-level 语义 verifier(LettuceDetect prompt 版 LLM judge,[2607.00895](https://arxiv.org/abs/2607.00895);catch wrong-value/plausible-fake-identifier 机械门控抓不到的)/ 对比式 claim 标签(`[grounded]`/`[inferred]`/`[NOT FOUND]`)/ decompose-then-NLI。P2 = STORM related-repo outline survey(拉相似仓 outline 当视角种子)/ draft-then-refine 两遍 outline。

**P4 增量调研(顺延 backlog,R3.5+,调研已给强架构)**:[RepoDoc](https://arxiv.org/abs/2604.26523)(arXiv 2604.26523):KG + doc + commit diff → **双向影响传播** → 选择性重生成(-73% 时间 / -77% token / +10.2% recall)。CRG = RepoKG;CRG 已有 SHA-256 增量 re-parse(`build`=full/`update`=增量)+ `get_impact_radius`(`code_graph.py:137-142`,`CRG_MAX_IMPACT_DEPTH=2`)。要点:① 存 `last_researched_sha` per codebase(MemoryService)② report 维护 `section ↔ graph-node` 溯源图(`report.meta.json`)③ git diff → impact_radius → 过期 section 集合 → 选择性重研究+拼接(未动 section verbatim)④ Leiden 社区 diff 触发架构章节重渲。**⚠ 坑**:CRG impact_radius recall 是**循环自证**(ground truth 来自同款边),需独立 ground truth(人标过期 section)才能宣称准确率;小 diff 下结构 JSON 可能比裸读文件还费 token。
