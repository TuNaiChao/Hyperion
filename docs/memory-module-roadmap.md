# 记忆模块改进路线图

> 这是把 Hyperion 记忆模块(P3)做成"突出点"的建议 + 分阶段实现规划。
> 配套文档:[记忆模块设计分析](memory-module-analysis.md)(当前实现的完整分析)。
> 调研基线:Graphiti / Zep、mem0 v3、Letta、Cognee + 学术 survey(2026)。

---

## 0. 现状一句话

基础架构已经是 2026 一线水平(bi-temporal + 纠正链 + 来源加权 + append-only + 四类 taxonomy + 三路融合),且在"代码库 bug-RCA"垂直生态位有通用 agent memory 没有的差异化王牌。

**短板只有一个:consolidate 太薄(只升级 mental_model),"持续学习"名不副实。**

改进分两类:**放大差异化**(讲清已有的王牌,基本零成本)+ **补真短板**(让 consolidate 名副其实)。下面按性价比排序。

---

## A. 放大差异化(已有的王牌,把故事讲出来)

### A1. 把"代码锚点溯源 + 三路融合"立成主打卖点

**这是什么**:这是**唯一别人没有**的东西。Graphiti / Zep / mem0 记的是"用户聊天里的事实";Hyperion 记的是"带 file:line + commit_sha + blast_radius 的代码结论",且 recall 时记忆、代码 chunk、结构图一起召回。

**为什么值**:零成本,纯叙事。代码已经在了,只是没在对外文档里把它放到该有的位置。

**怎么做**:在 README / 对外文档里把这写成记忆模块的头号差异化——一句话:"通用 agent memory 帮你记'用户说过什么';Hyperion 帮你记'这个 bug 在哪个 commit 哪一行为什么会犯,影响哪些文件,且和代码本身一起召回'"。

**状态**:文档级,本路线图 + 分析文档已落地这部分叙事。后续随 README 重组时归位。

### A2. 把"可审计的团队记忆"做成治理一等公民

**这是什么**:`KnowledgeItem` 字段天生带 provenance(confidence / source_tier / evidence / sha / bi-temporal / correction / access_count),memory-health-check skill 已经是这个方向。2026 治理型 agent memory(provenance / confidence / staleness / audit)是热点,Mem0 / Cognee 没有带溯源的团队记忆体检。

**怎么做**:体检 skill 再加"置信度衰减曲线可视化""stale 预警(长期未命中的高置信度条)"。原语都在,是展示层增强。

**状态**:低优先,排 Phase 3 之后(先把 consolidate 补厚,A2 是锦上添花)。

---

## B. 补真短板(让"持续学习"名副其实)—— 实现重点

2026 业界共识 consolidation = **keeps / merges / evicts**(留 / 合 / 逐)三件事。目前只做了 keeps 的一种(升级 mental_model)。B 类就是把 merges 和 evicts 补上。

### B1. 显式矛盾检测(consolidate 增强)—— Phase 1

**现状问题**:冲突靠 decay 隐式排序,但**没有显式检测两条 active 高置信度条目是否结论冲突**。memory-health-check e2e 已暴露过真实场景(同一个 P2P scan 泄漏 bug 存在"两派打架根因":A 派 abort-failure 4 条 vs B 派 scan-only 3 条),当时靠 agent 读 dump 人肉发现,不是系统自动标。

**要做什么**:在 `consolidate` 加一个"矛盾检测"pass——扫 active 高 conf 条目,用 `_same_subject` + `not _same_conclusion` 判定冲突对 → 标记待裁决(复用 `corrected_by` 降权链,或新建 `needs_review` 标签让 memory-health-check 聚焦提示)。

**为什么性价比高**:原语都在(`_same_subject` / `_same_conclusion` 已实现 [memorize.py:79-94](../src/hyperion/services/memory/backends/native/memorize.py#L79-L94)),只是目前没在 consolidate 里调。

**对标**:mem0 v3 Dream 的 "resolves contradictions";Zep 矛盾处理 benchmark 63.8%。

### B2. 语义近邻去重(consolidate 增强)—— Phase 1

**现状问题**:`make_id` 只按精确 content_key 去重——同根因、不同措辞 → 不同 id → 重复入库。两份不同人写的同根因报告,summary 措辞不同,会落两条。

**要做什么**:在 `consolidate` 加"语义聚类"pass——同 scope + 同 kind 的条目里,embedding cosine 超阈值的 → 判定语义近邻 → 提示合并(Bayes 累加置信度 + evidence 并集,复用 `_merge_on_remention` 的合并逻辑)。

**为什么性价比高**:embedding 已经在每条 KI 上(memorize 时算的),cosine 计算已有(`search_vector` 的 loop 路)。补的是"离线扫全量聚类",不是新检索能力。

**对标**:mem0 v3 Dream 的 "merges duplicates"。

### B3. 自动失效:补丁合入上游 → invalidate —— Phase 2

**现状问题**:`bug_lesson` 有天然保质期——补丁合入后该 bug 不存在了,旧记忆应失效。现在只能手动 `invalidate`。

**要做什么**:`consolidate` 里加一步(或新 skill),对带 `commit_sha` 的 bug_lesson,接 `git log` / upstream-merge 判定补丁是否已合入上游 → 合入了则 `set_invalid`。

**为什么值**:垂直场景刚需,且有 `commit_sha` 锚点 + 已有的 `merge_eval` 工具(patch-id 三态判定),接起来不难。

**对标**:mem0 Dream 的 "prunes stale";Zep 的自动 fact invalidation。

> **✅ 已成(偏离记录)**:原案"合入 → set_invalid"在实现时被否,落成**只标不删**:打 `merged_upstream` 标签 + confidence×discount(默认 0.5)。理由:① `invalid_at` 语义是"知识错了",不是"bug 修了"——考古查询("X 时点在不在")要靠这条记录;② reverse-apply 只证"改动在树里",可能是等价修复(非本补丁)→ 留人在环,确认后可手动 `invalidate`。判定用 `git apply --check --reverse`(带踩坑 #15 的 LF 归一化),不用 merge_eval 的 patch-id(那是上游两 ref 对比,这里是仓 vs 补丁,问题形状不同)。`repo_path` 只在显式 consolidate(CLI `--repo-path`)时给;recall 自转路径不知道仓在哪,不猜。

### B4. 长期未命中降权(evict 的一种,非物理删)—— Phase 2

**现状问题**:recall 的 decay 是"检索时按时间衰减打分",但没有"长期没人翻的高置信度条 → 主动降级"的巩固动作。记忆只增不减权重。

**要做什么**:`consolidate` 里,对 `last_recalled` 远早于 halflife 且 access_count=0 的高 conf 条目,下调 confidence(或打 `stale` 标签让体检预警)。**不物理删**(bi-temporal 铁律)。

**对标**:SCM 论文的 algorithmic forgetting;mem0 eviction(降级非删除)。

> **✅ 已成(偏离记录)**:落成**只标不降权**——`last_recalled`(或 `created_at`,取较晚)超 `stale_after_days`(默认 365)→ 打 `stale` 标签。原案"下调 confidence"被否:recall 打分已有 exp 时间衰减,consolidate 再降是双杀(同一条被两处扣分)。标签供 memory-health-check 预警 + agent 注入提示"这条很久没人验证过了"。

---

## C. 明确不做(YAGNI)

这些是调研里看着诱人但对本项目不值,记下来防止未来跑偏:

- **迁 Neo4j 知识图(Graphiti 式)**:重依赖(Neo4j),SQLite + sqlite-vec 够用且零外部服务。记忆量级(单库几百到几千条)SQLite 完全 hold 住。
- **工作记忆 / 情景记忆分层(OpenHands 式随 workflow state)**:随 workflow state 走不另建,已定。
- **物理删除 / eviction**:bi-temporal 软删是 2026 正确做法(Graphiti / mem0 都这趋势),别开倒车。
- **CJK BM25 分词(jieba)**:~~wpa / bluez 是 C 代码英文,影响小;但 domain_knowledge 是中文协议知识会受影响——中等优先,排 Phase 2 之后。~~ **✅ Phase 3 已做**(原判"低影响"低估了:真库 e2e 显示 wpa 52 条记忆的 summary/detail 大段中文,纯中文查询 BM25 路此前完全失明。jieba 两侧分词 + FTS standalone 化,见 Phase 3 落地记录)。

---

## 实现规划(分阶段)

### Phase 1:consolidate 增强(B1 + B2)—— 当前要做

**目标**:把 consolidate 从"只升级 mental_model"一件事,扩到"升级 + 矛盾检测 + 语义去重"三件事。让"持续学习"名副其实。

**改什么**:
- [consolidate.py](../src/hyperion/services/memory/backends/native/consolidate.py) 的 `consolidate()` 加两个 pass:
  - `_detect_contradictions`:扫 active 条目,`_same_subject` + `not _same_conclusion` → 标记矛盾对(暂只检测 + 统计上报,不自动纠正——纠正需要语义判断谁对,留给 agent / 体检 skill;检测是确定性原语)。
  - `_cluster_semantic_duplicates`:同 scope + 同 kind,embedding cosine 超阈值 → 候选合并对(暂只报候选,不自动合——自动合并语义上危险,可能误合近义不同 bug;留 agent 裁决。但提供"高置信度自动合并"开关可选)。
- 返回统计 dict 扩字段:`{scanned, promoted, contradictions, duplicate_clusters}`。

**关键设计决策**:
- **矛盾检测只标不裁**:谁是正确根因是语义判断(踩坑 #11 同源教训:apply 过 ≠ 根因对),系统不该自动选边。检测出矛盾 → 打 `needs_review` 标签 + 进统计 → memory-health-check skill 聚焦提示用户 / agent 裁决。这和纠正链(`corrects`/`corrected_by`)的"显式声明才纠正"哲学一致。
- **语义去重保守**:默认只报候选不自动合;只有"cosine 极高(如 >0.95)+ 同 kind + 同 subject 判定"才自动合。宁漏不错(误合两个不同 bug 比留两条重复更糟)。
- 复用现有 helper:`_same_subject` / `_same_conclusion` / `_merge_on_remention` 的合并逻辑,不重造。

**测试**:在 `tests/services/memory/test_memory_native.py` 加测:
- 矛盾检测:同 subject 不同 conclusion 两条 active 高 conf → consolidate 报 contradictions=1。
- 语义去重:两条高 cosine 同 kind 条目 → consolidate 报 duplicate_clusters=1(或自动合并后 count 减少)。
- domain_knowledge / mental_model 不参与矛盾检测的边界。
- 不破坏现有 `test_consolidate_promotes_mental_model`。

**验证**:`uv run pytest tests/services/memory/ -q` 全绿 + `uv run ruff check`。不跑真模型(用户自验铁律)。

### Phase 2:自动失效 + 降权(B3 + B4)—— ✅ 已成

**目标**:bug_lesson 随补丁生命周期自动失效;长期未命中条目主动降级。

**依赖**:Phase 1 的 consolidate 框架(多 pass 结构)。B3 需要 git / merge_eval 工具协同,触发式(不每次 consolidate 都跑 git,按需或定时)。

**状态**:✅ 已落地(实现与原案的两处偏离见 B3/B4 小节的"偏离记录";e2e 真 DB + 真 git 仓验证,抓到 2 真 bug 已修)。

### Phase 3+:治理展示(A2)+ CJK(B/C 类)—— ✅ 已成

**目标**:体检 skill 可视化(置信度曲线 / stale 预警);CJK BM25 分词(若 domain_knowledge 中文量起来)。

**状态**:✅ 已落地(2026-08-14):
- **A2 治理展示**:`memory_dump` 溯源卡渲染 `[tags]`(needs_review / merged_upstream / stale 逐条可见)+ header 健康概要行(标签聚合计数,无标签不输出噪音)+ memory-health-check SKILL 升级为双层读法(consolidate 自动标 → agent 语义读)。原案的"置信度曲线可视化"没做——MCP 工具输出是文本,画曲线是展示端的事,标签 + 计数已够体检用(YAGNI)。
- **CJK BM25 分词**:jieba 两侧分词(索引侧入 FTS 前切、查询侧 `_fts_query` 前切,同一分词器)+ FTS 从 external-content 触发器同步改 standalone(upsert 同事务维护——触发器在 SQL 层调不了 Python)+ 幂等 migration(老库打开即检测 `content=` 重建 + 全量重灌,失败降级不崩)。调研取舍:trigram 要 ≥3 字查询(溢出/死锁是 2 字)不合身;ICU 多数 Python sqlite3 构建没编译;jieba 零 C 扩展。**e2e 真 DB**:79 条全量重灌,纯中文 "扫描 阻塞" BM25 路召回 wpa 真实中文记忆 top-3(embedder=None 也活,减少对向量 API 依赖)。

---

## Phase 1 验收标准

1. `consolidate()` 返回 `{scanned, promoted, contradictions, duplicate_clusters}`。
2. 矛盾检测能发现"同 subject 不同 conclusion"的 active 对(确定性,基于已实现的 `_same_subject`/`_same_conclusion`)。
3. 语义去重能基于 embedding cosine 报候选(或保守自动合并)。
4. 现有测试全绿 + 新测试覆盖三 pass。
5. ruff clean。
6. domain_knowledge / mental_model 的边界守对(前者不参与任何升级 / 合并;后者已是终态)。
