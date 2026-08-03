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
  - Verifier 逐符号 `parse_file` 核验(现只查「文件存在」;逐符号 symbol@line 更严)。
  - LoopDetection 频次层 + consume_stop_reason + run_id 作用域(现单层 hash + thread_id)。
