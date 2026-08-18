---
name: l2-granularity-prior-handoff
description: 2026-08-18 L2 检索改进交接:重排池扩满+符号粒度先验(L1 mrr→1.000,L2 持平 0.700 但位次大前移);含三修 bug(撞 chunk id/幽灵行/gitignore)与「剩余 miss 是另一失败模式」的深挖定性
metadata:
  type: project
---

# L2 粒度先验 + 撞 chunk id 三修 交接(2026-08-18,commit 343d982 + a801ac6)

## 做了什么

1. **命名漂移修复**(commit 8e524de):SKILL×8 + README 工具名 dash→underscore(`rootrecall_memory_recall` 等真实 opencode 工具 id);agent block 名保持 dash(那才是真 id)。
2. **三修 bug**(commit 343d982,21 新测,311 全绿):
   - **撞 chunk id**:4 个测试函数各定义 `class _FakeGraph` → 旧 parser 只跟 class 栈,限定名全撞 → 一个 upsert 批 4 行同 id,LanceDB 拒("Ambiguous merge inserts")。修:parser 作用域栈**含函数**(函数局部类/嵌套函数带外层函数前缀);chunk_repo 尾部去重护栏(首见保留+警告);store.`delete_by_file`;index 增量重嵌前清「消失文件+重嵌文件」旧行(符号改名换 id 不留幽灵行,顺手还了 index.py:22 旧账)。
   - **iter_source_files 不走 gitignore**:以仓库根建索引时 clone 进来的参考仓(deer-flow/oh-my-pi)被全量扫入,**40 分钟嵌入烧账单**(758 chunk 的活干成 5000+)。修:git 仓走 `git ls-files` ∪ `ls-files --others --exclude-standard`,非 git 兜底 rglob。
   - **建索引根用错**:rootrecall 的索引/eval gold 根是 **`src/rootrecall`**(路径前缀 `services/...`),不是仓库根 —— 传错根 = gold 全对不上 + 参考仓混入双重事故。
3. **L2 检索改进**(commit a801ac6,4 离线单测):见下。

## L2 改进设计与实测

**两步**:① 重排池扩满 —— reranker 拿全部候选(`top_n=len(docs)`),旧 `top_n=top_k=5` 让 rank 6+ 的 gold 永远不可见;**远端 rerank 本就对送的每条文档打分,top_n 只裁返回,扩池零额外成本**。② 符号粒度先验 `_granularity_prior`:重排分×先验再排序 —— module 0.65(文件 docstring 逐字回响查询词,最大噪声源,实测 Q4 前三全是 module)/ 私有·嵌套 0.80 / 公共入口 1.0;**降而不剔**;`extra['rerank_score']` 留原始分,`apply_prior=False` 消融开关。

**实测**(rootrecall 18 条,758 chunk 干净索引):
- **L1 mrr 0.854→1.000、ndcg 0.891→1.000**,rprecision 保持 1.000 —— 符号查询全部压到第 1。
- **L2 top-line 持平 0.700**,但 gold 位次大前移(chunk_file 21→7、parse_file 24→6,module/私有压头全消)。

**Why(重要,防下次重挖)**:剩余 L2 miss 是**另一失败模式**,不是粒度错位 —— ① 同域**公共**符号 cross-encoder 平局(LanguageGrammar 0.71 / summarize_file 0.51 vs parse_file 0.49,分差 0.003~0.03,无结构信号可分;调乘数硬掰 = 过拟合 10 条查询,**已明确拒绝**);② `parse_repo` 不在 hybrid 召回 50 池(召回侧;`candidate_top_n=100` 试过,parse_repo 进视野@6 但无 top-5 变化且引新噪声,不采纳)。抽象基类 vs 实现(Embedder.embed_chunks 压 RemoteEmbedder.embed_chunks)也无干净结构信号,留 reranker。

**How to apply**:若未来再抬 L2,方向是 **rerank 文档表示**(结构卡 symbol+signature+docstring 替代 fts_text 词袋)或**更强 reranker**,不是调先验乘数;改前先跑 `eval/run_eval.py` 拿基线,改后 L1 必须 1.000 不回退。方法论依据:metadata boost 是 2025-26 建立模式(Milvus Boost/Weighted Ranker、Vectara Chain Reranker),符号粒度块精度优于模块块(arXiv 2605.04763 混合粒度)。

## 踩的坑(新)

- **后台长任务管道套 `| tail` = 零进度可见**,像卡死;长任务输出直落文件(run_in_background 不加管道)。
- **诊断卡死三件套**:`ps` 看 CPU、`ls --time-style` 看目录最近写入、`ss -tnp` 看是否有活着的 API 长连接(这次连接 ESTAB 到 DashScope = 在干活非死锁)。
- **gold 路径前缀 = 索引根的契约**:eval gold `services/...` 前缀锁死 corpus 根必须是 `src/rootrecall`;建索引传根前先对 manifest 键前缀。

关联 [[p1p2-high-priority-handoff]] / [[tier2-index-prerequisite-handoff]] / [[colleague-onboarding-toolset-handoff]]。
