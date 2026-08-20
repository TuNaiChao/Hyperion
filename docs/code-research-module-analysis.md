# 代码调研模块设计分析

> 这是 RootRecall 三大支柱里 P1「代码仓深度调研」的当前实现分析。
> 源码真相在 `src/rootrecall/services/code_index/`(检索 + 结构图)与 `src/rootrecall/workflows/`(流程),本文档只做"讲清它在干什么、怎么设计的"。

---

## 0. 一句话:代码调研在干什么

面对一个陌生代码库——几十万行的 C 蓝牙协议栈、一个没接触过的开源项目——普通 coding agent 的读法是"打开目录从上往下翻"。

**比喻**:像陌生人进了大城市,没地图没导航,走到哪问到哪,token 花了上万还在城郊打转;下次再来(新会话),连上次问过的路都忘光重来。

RootRecall 的代码调研就是给代码库建一套**导航系统**,三层能力 + 一套导游路线:

| 层 | 比喻 | 对应 |
|---|---|---|
| 检索层 | **问路**:"处理扫描结果的函数在哪?" | code_index 混合检索 → `search_codebase` 工具 |
| 结图层 | **城市地图**:哪个区是商业区、哪条路是主干道、哪条是咽喉要道 | CodeGraph 结构图 → `repo_map` / `repo_overview` / `call_chain` / `blast_radius` |
| 流程层 | **导游路线**:按主题带逛一遍并写游记 | deep_research / patch_report workflow + onboarding / compare skill |

产出去两个地方:① **落盘的调研报告**(给人看,带 file:line 引用);② **沉淀成记忆**(P3 的 codebase_fact,带源码 commit 溯源)——下次同类问题先翻记忆秒答。这就是"调研一次、终身受益"的机制,详见[记忆模块分析](memory-module-analysis.md)。

**与记忆模块的分工(别混淆):** 检索层和结构图层回答"代码库**此刻**长什么样",是事实引擎;记忆回答"**之前**调研 / 修 bug 学到了什么",是知识层。前者是后者的地基之一。

---

## 1. 检索层:code_index —— 怎么"问路"

### 1.1 建索引:四步流水线

一条命令 `rootrecall index <repo_path>` 把四步跑完([cli.py:46-109](../src/rootrecall/cli.py#L46-L109)):

1. **解析(parser)**:tree-sitter(一个"语法显微镜")逐文件读出符号"名片"——叫什么、在哪行、签名长啥样(`Symbol` dataclass,[parser.py:258-332](../src/rootrecall/services/code_index/parser.py#L258-L332))。认 Python + C 两门语言,语言表是数据驱动的(`GRAMMARS` 注册表,[parser.py:189-214](../src/rootrecall/services/code_index/parser.py#L189-L214)),加语言只改表不动核心。读不了的文件返回空不崩。
2. **切块(chunker)**:按**符号边界**切——一个函数一块,一个类一块。**比喻**:给房子拍照片是**按房间拍**,不是每 10 米盲拍一张把墙拍成两半。超长符号(>16000 字符,`MAX_CHUNK_CHARS` [chunker.py:163](../src/rootrecall/services/code_index/chunker.py#L163))再按行区间贪心二次切,防止单块撑爆嵌入接口;模块级的 import / 全局常量由兜底块收走,保证所有块拼起来能还原原文件——**100% 文件覆盖,零漏网**。
3. **向量化(embed)**:每块算一个"语义指纹"(embedding)。嵌之前给块拼一行注释头(`# file: scan.c · symbol: sdp_extract_seqtype · kind: function`)——指纹带上地址,这是 Anthropic Contextual Retrieval 的轻量版([embed.py:87-97](../src/rootrecall/services/code_index/embed.py#L87-L97))。远端走 OpenAI 兼容接口(默认 DashScope Qwen3),也能切本地 sentence-transformers,配置即换。
4. **存储(store)**:进 LanceDB 嵌入式向量库(单机零服务,**一个目录就是一个库**),每个仓一张表物理隔离(`data/code_index/<repo>/lancedb/`)。

### 1.2 查询:两段式(海选 + 精排)

`retrieve`([retrieval.py:274-321](../src/rootrecall/services/code_index/retrieval.py#L274-L321)):

```
混合召回(Stage 1):BM25 关键词 + 向量语义,RRF 融合,取前 50
        ↓
精排(Stage 2):cross-encoder reranker 对全部候选逐个细看(不是只看前 k 个)
        ↓
粒度先验(Stage 3):按符号级别微调分数 → 排序 → 取 top-k
```

**比喻**:招聘先海选 50 份简历(BM25 看"简历关键词",向量看"这人气质像不像"),再让资深面试官(reranker)**把 50 份全读一遍**逐份打分,最后 HR 拿着打分表做一道"级别校正"——整本部门介绍(相当于 module 块)和内部草稿(私有/嵌套函数)往后挪一挪,对外发布的正式岗位职责(公共入口符号)往前放一放——取前 5。

**为什么要 Stage 2 看全部、Stage 3 再微调**:
- 面试官本来就对送来的每份简历都打分,只取回前 5 份是白扔信息——真命候选人若排第 6 就永远被埋(实测概念查询的正解曾被挤到第 21、24 位)。扩池取回**零额外成本**。
- 概念查询的实测病根:整文件的词袋(module 块)常把查询词原样复述一遍,双高分类似"答案",把真正的入口符号挤出前排。先验只做**降权不剔除**(module ×0.65、私有/嵌套 ×0.80、公共入口 ×1.0,[retrieval.py:220-245](../src/rootrecall/services/code_index/retrieval.py#L220-L245)):问"哪个函数管这事"时入口符号上得来,问"这个文件整体干嘛"时 module 块分数够高照样回前排。原始重排分保留在 `extra['rerank_score']`,排序可解释。

- BM25 侧专门为代码调过参:FTS 索引 `stem=False / remove_stop_words=False`,不然 `malloc` / `int` / `void` 会被词干化或当停用词删掉([store.py:147-161](../src/rootrecall/services/code_index/store.py#L147-L161))。
- 全文侧喂的不是裸代码,是**词袋**:标识符拆词(`scan_res_handler` → scan / res / handler)、符号名和 docstring 重复加权([chunker.py:198-230](../src/rootrecall/services/code_index/chunker.py#L198-L230))——让"处理扫描结果"也能命中 `scan_res_handler`。
- **降级链全程可观测**:`out_mode` 四态 `hybrid+rerank / hybrid / rerank-failed:hybrid / empty`——reranker 挂了自动退海选顺序,不装死也不谎报。

### 1.3 工程细节(增量与原子)

- **增量更新**:索引带"清单"(manifest)——每文件的 sha256、嵌入模型指纹、schema 版本。重跑时按清单对账,只重嵌改过的文件;行级用 content_hash 判定,`merge_insert` 条件更新,**没变的块零重写**([store.py:196-199](../src/rootrecall/services/code_index/store.py#L196-L199))。重嵌前先把该文件(以及已从仓库消失的文件)的旧行整批清掉([store.py:207-223](../src/rootrecall/services/code_index/store.py#L207-L223))——块的主键是「文件:限定名」,符号改名会换主键,不清旧行会留内容重复的幽灵。
- **只编 git 认账的文件**:文件遍历尊重 `.gitignore`(git 仓走 `git ls-files` 已跟踪 ∪ 未跟踪未忽略 两路并集,非 git 仓兜底纯遍历,[parser.py:377-446](../src/rootrecall/services/code_index/parser.py#L377-L446))。工作区里 clone 进来的参考仓若已 ignore,不会被扫进索引——否则几千个无关文件全送去嵌入,账单爆炸还污染检索。
- **限定名带完整"户口"**:解析时作用域栈把函数也压进去,函数体内定义的类/函数带外层函数前缀(如 `test_a._FakeGraph`)——保证同文件内主键唯一,否则多个函数各自定义同名局部类会撞主键,增量写入被数据库直接拒绝。
- **原子重建**:全量重建写影子目录,建完 `os.replace` 原子交换,建一半崩了旧索引不脏、下次能恢复([index.py:183-227](../src/rootrecall/services/code_index/index.py#L183-L227))。
- **模型指纹换版自动全量**:换 embedding 模型 → 指纹变 → 自动全量重建,不会出现新模型向量混旧模型向量的暗病。

---

## 2. 结图层:CodeGraph —— 怎么画"城市地图"

检索回答"X 在哪",但回答不了"这个系统的**形状**是什么":哪些是核心模块、改一处会波及谁、哪个函数是全仓枢纽。这需要**图**。

外部引入 code-review-graph(可选 extra)把全仓解析成一张**函数调用图**(谁调用谁、谁继承谁)存 SQLite(`data/structgraph/<repo>/graph.db`),RootRecall 在 [code_graph.py](../src/rootrecall/services/code_index/code_graph.py) 上包出查询面。图上跑三类经典算法:

| 算法 | 干什么 | 比喻 |
|---|---|---|
| **PageRank** | 算符号"声望":被越多重要函数调用越重要 | 朋友圈影响力——被大 V 关注的人本身也不简单 |
| **Leiden 社区检测** | 按调用密度自动聚出"模块" | 按道路密度自动划城区,不需要人工标注哪个文件属于哪个模块 |
| **betweenness(介数)** | 找"咽喉":多社区之间的必经之路 | 跨区主干道——堵一条全线瘫 |

### 七个 MCP 工具(全部纯图 / 纯 git 查询,零 LLM)

| 工具 | 回答什么问题 | 机制 |
|---|---|---|
| `repo_map` | "这仓最重要的符号是谁?" | CALLS 子图整图 PageRank,按 token 预算贪心装填成树状地图([code_graph.py:748-823](../src/rootrecall/services/code_index/code_graph.py#L748-L823)) |
| `repo_overview` | "这仓整体架构怎么组织?" | 聚合四个图查询:社区边界 + hub(度最高,商业中心)+ bridge(介数最高,咽喉)+ 跨社区耦合告警 |
| `call_chain` | "X 的上下游调用链?" | 符号种子 + 有界 BFS(深度封顶 5 防大图爆炸)+ PageRank 排序 |
| `blast_radius` | "改这些文件会波及谁?" | 图上 BFS 波及面;带路径解析容错(agent 给相对路径也能对上图里的绝对路径,否则静默返空) |
| `cross_version_diff` | "同仓两个版本间改了啥?" | 纯 git:提交清单 + patch 等价性 + 关注文件 diff,**零 LLM 确定性事实** |
| `merge_eval` | "上游这批 commit 该不该合进 fork?" | patch-id 等价判"已修" / merge-tree 对象库试合并判"能合 / 冲突"(零 touch),三态判定(P2 详述) |
| `when_introduced` | "这段缺陷逻辑哪个 commit 带进来的?" | 纯 git 双锚点:pickaxe(`-S` 符号)或行历史(`-L`,改名跟随)出候选表带 added/removed 计数,引入者裁决归 agent(SZZ 式分工,P2 详述) |

**分层检索**是刻意的:`repo_map` 管符号层(哪个函数重要),`repo_overview` 管架构层(模块边界 + 枢纽 + 耦合告警),`call_chain` 管路径层(端到端怎么走)。问哪层用哪个工具,不混。

**图是可选的**:CRG 没装 / 图没建,检索层照常工作,工具返回可操作的提示(怎么装、怎么建)而不是报错——`rootrecall index` 同样把"图建失败"降级为非致命(向量索引不受影响)。

---

## 3. 流程层:导游路线(四个调研场景)

工具是散装弹药,流程教"什么场景按什么顺序开火"。两条 workflow(一条命令跑完)+ 两个 skill(给 coding agent 的菜谱):

### deep_research workflow —— "给我写一份这仓的调研报告"

六节点([graph.py:34-50](../src/rootrecall/workflows/deep_research/graph.py#L34-L50)):建索引 → 规划 → 调研 → 报告 → 记忆。

- **规划**:社区检测聚出的模块按规模取 top-8,每模块配"人话名 + 调研视角"(基础事实 always-on,再从五个视角种子挑 1-2 个)——LLM 失败降级通用视角,单条降级不全盘丢。
- **调研**:每模块一个 ReAct 子 agent,**并发帽 3**,各持 grep / read / 检索三件套去"采访";轮数用 TurnBudget 封顶防跑飞。产出契约是**带引用的 JSON**:每条结论必须附 file:line。
- **报告**:六章渲染(架构章由结构图驱动、非 LLM 编造),过四档 Verifier 核验引用。
- **记忆**:结论抽成 codebase_fact 入库(带 commit SHA 溯源)——P1 产出直接喂 P3,闭环。

### patch_report workflow —— "给我分析这一批 PR"

六节点:抓取(Gerrit / GitHub 按 URL 自动分流,Gerrit 私仓带凭据)→ 逐个分析(apply 门 + 图风险分 + LLM 引用式总结 + 安全分层)→ 跨 PR 聚合(确定性分桶 + 同主题去重——**只标注重复组不删底层记录**)→ 报告(引用回查)→ 记忆。

### onboarding skill —— "给新人讲讲这仓的架构"

七步菜谱:先 recall 探底(记忆里已有同仓导览 → 直接复用秒答)→ 结构快照(repo_overview + repo_map 俯瞰)→ **挑一条主旅程端到端走**(默认 hub 排第一的枢纽函数当入口,call_chain 展开,逐节点读完整函数体)→ 聚结论 → 报告落盘 → memorize。方法论对应业界 onboarding 循环:先看项目形状,再 trace 一条真实旅程。

### compare skill —— "v20 和 v25 的连接流程有什么差异?"

三阶段对比法:锚定两版流程入口 → 语义配对函数(改名 / 拆分 / 合并是语义判断)→ 逐节点读函数体对照。**跨两个独立仓**时刻意不用 `cross_version_diff`(那工具只支持同仓两个 ref)。

两个 skill 共同的骨架:**recall-first 短路(命中不重跑)→ 冷路径调研 → 读码即记**。调研是纯读码事实,记下就放心用;bug 修复的教训则是 apply 过即记但标 `unverified`(真机后升级)—— 记忆条目永远带着看得见的验证状态,这是和 P2 各 skill 最大的行为差异。

---

## 4. 防幻觉体系 —— 引用与验证

调研报告最大的风险不是写不出来,是**写得太顺——编的**。三层防御:

1. **检索工具只回真实存在的东西**:`search_codebase` 只返回索引里真实解析出的符号,不会编路径。
2. **cited-reporter 契约**:子 agent 的产出格式强制"每条结论 + file:line 引用",只允许断言工具真实返回过的符号。
3. **Verifier 回查**:报告落盘前逐条核验引用,四档判级([_verify.py:32-38](../src/rootrecall/workflows/deep_research/_verify.py#L32-L38))——strict(文件 + 符号 + 行号全对)/ near(±5 行容差)/ file(只查到文件)/ bad(疑似编造,标红列出)。产出 Existence@Line 比例当报告质量指标。

**诚实截断**:工具返回大结果时明说"截在哪、怎么补取",不静默丢尾。两级手段:结构化收口(repo_overview 大仓社区按 size 取前 30、成员只留计数 + 样本,从源头控制体积)+ 真超长才截并带 note(五个列表型工具统一 `_honest_truncate`);列表型工具(blast_radius / call_chain / cross_version_diff / merge_eval / repo_map)可调参数收缩重取。

---

## 5. 对照 2026 业界:当前在什么位置

| 业界实践 | 本项目现状 |
|---|---|
| [Aider repomap](https://aider.chat/2023/10/22/repomap.html)(tree-sitter 符号 + PageRank + token 预算;2025-2026 被大量复刻为 MCP server) | ✅ `repo_map` 同款算法,且叠加社区检测 / hub / bridge(Aider 没有);连 scipy 缺失都有纯 Python PageRank 降级 |
| [Microsoft Code Researcher](https://www.marktechpost.com/2025/06/14/microsoft-ai-introduces-code-researcher-a-deep-research-agent-for-large-systems-code-and-commit-history/)(深度调研 agent,RL 训练代码图多跳遍历策略) | 🟡 方向一致(call_chain 多跳 + 引用式报告),遍历策略靠 skill 指令而非训练——单机工具的现实取舍 |
| [Tree-sitter 知识图谱 MCP(arXiv 2026)](https://arxiv.org/html/2603.27277v1)(结论:最优架构 = 图检索 + RepoMap/PageRank 混合) | ✅ 正是检索层 + 结构图层的双路设计 |
| 混合检索(BM25 + 向量 + RRF)→ cross-encoder 精排 | ✅ 工业标准两段式完整,带四级降级可观测;精排后再叠**粒度先验**(module/内部符号降权、公共入口不动)——metadata boost 是各家 reranker 的通行做法,这里按代码符号的"级别"自制同款 |
| [AGENTS.md](https://agents.md/) 惯例(60k+ 仓采用,agent 开工自动读的"README") | ✅ `export_report(agents_md=True)` opt-in 产出——默认关(不问自写用户仓 = 越界)、已有拒写不覆盖、内容是 agent 蒸馏的 ≤60 行精简版(冗长反而拖累 agent)而非死模板 |

**结论**:检索与结构图的**架构选型都在 2026 主流线上**(hybrid + rerank、PageRank 地图、社区检测、引用防幻觉),叠加"沉淀进记忆"是通用代码情报工具没有的闭环。截断治理(诚实截断)与 AGENTS.md 产出已补齐;检索侧的粒度先验(全量重排 + 符号级别降权)已落地——自评测里符号直查全部第 1 名命中,概念查询的正解位次大幅前移但前排命中率未再抬(剩余 miss 是同域公共符号的打分平局,属语义模型天花板,不是粒度问题),再抬的方向记 CLAUDE.md「低优 backlog」。

---

## 6. 明确不做(YAGNI,防未来跑偏)

- **不迁 Neo4j 图数据库**:业界不少 code-graph MCP 用图数据库,但单机场景 SQLite 结构图 + LanceDB 向量已够,引外部服务违背"单机零服务"的轻量定位。
- **语言扩展按需再加**:`GRAMMARS` 是数据驱动表,加语言 = 加表项 + 装 grammar 包。没有目标仓就不加,猜需求只会白养索引。
- **LSP 层不接主管线**:clangd 封装已写好(lsp.py),但硬依赖 compile_commands.json——与"不编译"路线冲突,且 opencode 自己会读码。保留代码,归档不推广。
- **chunker 的 callers/callees 字段暂不回填**(P1.5 欠账):图 enrich 检索是正经方向,但当前检索质量没有实证短板,按评测触发。
- **不做"自动跨仓联合图"**:compare 的函数配对刻意留给 agent 语义判断——两个独立仓没有共同祖先,任何自动配对都是伪确定性。
