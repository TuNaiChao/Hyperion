# P1/P2 两支柱改进待办

> 来源:2026-08-14 对 P1(代码调研)/ P2(bug 定位)两支柱的全面分析(代码走读 + 2026 业界调研)。
> 配套的现状分析见[代码调研模块分析](code-research-module-analysis.md) / [bug 根因定位模块分析](bug-rca-module-analysis.md)。
> 排序原则:先小改大益,再真实缺口;全部严格在"三件事"(代码情报 / 记忆 / skill+工具)内,零编译零复现。

---

## 🔴 高优先(小改大益,多数 0-1 文件)

### 1. auto-query 记忆召回接回主路径(0 代码,改 SKILL)

**问题**:定位后用 `problem_summary` 自动召回历史修法(路线 #4 拍板的 B 方案)只活在已降级的老 workflow([nodes.py:125](../src/hyperion/workflows/bug_rca/nodes.py#L125)),skill 主路径全靠 agent 自觉——bug-rca SKILL 工具表里只有一行"定位前后调"。

**为什么重要**:这是"已设计、已验证"的机制,只差接回主路径。老 workflow 的实现还有现成依据:query 取值优先级 `problem_summary → root_cause → trigger`(真实 journalctl 无关服务 error 行会淹没真信号,用 delegate 已理解的 problem_summary 才是高质量 query——A1 方案被探针证伪的教训)。

**改法**:bug-rca SKILL.md 工具表 + 证伪纪律节硬化成步骤:"根因候选定稿前,必须用 problem_summary(或 root_cause)当 query 调一次 `memory_recall`,命中先验要对照本次证据复核再定论"。同步改 `config/opencode_hyperion.json` 的 hyperion-bug-rca agent prompt。

**验证**:opencode e2e(自跑,不等用户)。

### 2. bug-rca 加"多假设清单"纪律(0 代码,改 SKILL)

**问题**:当前证伪纪律是"立一个根因 → 证伪它"——单假设,锚定风险仍在。踩坑 #11(glm 系统性锚定显眼日志行)正是单假设锚定的病。

**业界依据**:[CogniGent](https://arxiv.org/html/2601.12522v2)(2026)的核心增益来自多候选假设并行 + 各自证伪打分(高 / 中 / 低置信),淘汰制而非认定制。

**改法**:证伪纪律节加一条——"定位阶段先列 2-3 个候选根因(按记忆先验 + 日志线索),逐个找证据 / 反证,按证据强度淘汰;全部候选被淘汰才回头扩大搜索"。同步 agent prompt。

**验证**:opencode e2e 对照金标(demo1/demo2)看根因判断是否稳定。

### 3. MCP 工具 8000 硬截断统一治理(踩坑 #19 同源病)

**问题**:`blast_radius` / `call_chain` / `cross_version_diff` / `merge_eval` / `repo_map` 五个工具都是 `body[:8000]` 静默截断且无分页——大结果丢尾,agent 无感知。与 memory_dump 上次"截断吞一半条目"是同一个病(踩坑 #19)。

**改法**(照抄已验证的方子,repo_overview / memory_dump 先例):按工具形态选一——列表型加 `limit/offset` 分页 + 显式翻页提示;混合型(body 是 JSON dump)加 `max_items` 类参数收缩 + 截断时 note 明说"截在哪、怎么补取"。

**验证**:单测(截断提示出现 / 分页翻全)+ 现有大 e2e 回归。

### 4. deep_research 的 CRG 建图补 try 降级(1 行级)

**问题**:[nodes.py:65](../src/hyperion/workflows/deep_research/nodes.py#L65) 裸调 `CodeGraph.build`,CRG 没装会崩掉整个 workflow——与 CLI `index` 子命令的降级标准(ImportError 非致命 + 提示装法)不一致,属于该降不降的真 bug。

**改法**:try/except 包住,失败 warning + 继续无图调研(检索层照常,报告的架构章降级为"图未建")。

**验证**:临时禁用 CRG 跑一次 deep_research e2e。

---

> **🔴 1–4 已全落地(2026-08-14,commit 7535527)**:#4 deep_research 建图/开图双 try 降级 + 2 单测;#1+#2 进 bug-rca SKILL 证伪纪律 + agent prompt(多假设清单 + 候选定稿前 `memory_recall(problem_summary)` 定向复核双时点);#3 五工具统一 `_honest_truncate`(超长才截 + note 明说截多少/怎么补取)+ 2 单测。**e2e 真机全绿**(deepseek-v4-flash-0731,24 步):根因与 demo2 金标逐点吻合(handler 覆盖误路由 → p2p_scan_work 泄漏 → 队列堵死);多假设清单明确生效(报告含「候选淘汰记录」,B/C 候选各有时序/证据淘汰);定向复核双重坐实(recall 双时点 09:57 发散 + 09:59:56 定稿前复核,且用先验 1588c403 反向排除双接口候选)。见 [backlog 高优先落地交接](../../.claude/memory/p1p2-high-priority-handoff.md)。

---

## 🟡 中优先(真实缺口,建议排期)

### 5. onboarding/compare 产出 AGENTS.md(新功能,对齐 2026 惯例)

> **✅ 已落地(2026-08-17)**:`export_report` 加 `agents_md: bool = False` 参数(opt-in)—— 传 True 时同源内容加生成头注释写 `<repo_path>/AGENTS.md`(仓根),**默认关**(不问自写入用户仓 = 越界);**已有 AGENTS.md 拒写不覆盖**(保护手写/别的工具产物)。onboarding/compare SKILL step6 + agent prompt 同步「用户显式要求才传 + 蒸馏 ≤60 行精简版(ETH Zurich 2026:冗长 AGENTS.md 拖累 agent);compare 只写用户指定的目标仓」。3 断言单测(默认关不碰仓根/opt-in 写+头注释/已有拒写)。


**场景**:onboarding 报告目前只落 report.md(给人看);[AGENTS.md](https://agents.md/) 是 2026 业界惯例(60k+ 仓采用),opencode / claude code / cursor 原生读取——"给 agent 看的 README"。

**价值**:把"调研结论"变成**任何 agent 下次开工自动注入的上下文**,和记忆系统形成"文件层 + DB 层"双保险。改动小(报告渲染加一个模板),差异卖点大(调研产物直接可被机器消费)。

**设计要点**:架构速览 + 核心入口 + 命名约定 + 已知坑,从报告同源数据渲染;**必须给 flag 控制**(如 `--agents-md`,默认关——别默认往用户仓里写文件);注意 2026-03 ETH Zurich 研究([arXiv 2601.20404](https://arxiv.org/html/2601.20404v2))提示 AGENTS.md 写得冗余反而拖累 agent,内容要精不要全。

**验证**:onboarding e2e 后检查生成的 AGENTS.md 内容与报告一致性。

### 6. merge_eval 升 `git merge-tree --write-tree`(触发 backlog #60)

> **✅ 已落地(2026-08-17,原 backlog #60)**:apply 检查优先 `git merge-tree --write-tree <fork_ref> <commit>`(git ≥ 2.38)——**对象库零 touch 判冲突,不依赖 worktree 状态**,三态不再押在「agent 先 checkout fork_ref + 保持干净」的调用姿势上;rc=0 干净 / rc=1 冲突 / rc>1 uncertain。merge-tree 不可用(老 git/跑挂)→ 回退老 `git apply --check` 对当前 worktree + note 明示「三态可能失真」。工具 docstring / upstream-merge SKILL step3+硬约束 / agent prompt 三处文案同步(step3 从「切 fork 干净态【硬门】」降为「rev-parse 可解析即可」)。新增脏树单测:停 main + a.py 脏 + 不 checkout fork,新判 recommend_merge 正确(老路此姿势必失真);既有三态/空范围测回归绿。探针先坐实 git 2.50 行为:merge-tree 冲突 rc=1(管道会吃 rc,`$?` 直读)、输出首行树 oid + 冲突文件在后。


**问题**(已修,留档):apply 检查曾对**当前工作树**跑,三态判定的正确性押在"agent 先 checkout fork_ref + 保持干净"的自觉上。backport e2e 已把姿势升级为 worktree 隔离,工具本身当时没修。

**改法**:`git merge-tree --write-tree`(git 2.38+)零 touch 判冲突——工具从"依赖调用姿势"变成"自洽正确"。已有的 backlog #60,此处触发。

**验证**:/tmp 三分支测试仓三态与金标吻合(复用 upstream-merge e2e 的夹具)。

### 7. `when_introduced` 工具——bug 引入 commit 定位(P2 唯一值得新增的 MCP 工具)

> **✅ 已落地(2026-08-17,第 16 个 MCP 工具)**:双锚点模式 —— `symbol`(pickaxe `git log -S`,短名配 `file` pathspec 收窄)或 `file+line[,line_end]`(行历史 `git log -L`,改名/行漂移跟随);候选表时间倒序带 added/removed 计数(pickaxe 只数含 symbol 的 ± 行 → 引入 commit 通常是最老 added>0/removed==0 那条,中间成对的多是重构搬移);`_honest_truncate` + note 明说裁决归 agent。真仓探针:hostap 上游 `scan_only_handler` → 唯一候选 `66fe0f70` "Add 'SCAN TYPE=ONLY' functionality"(demo2 金标 bug 机制的引入点,分毫不差);deepin bluez `sdp_extract_seqtype` → `ae4512f` Init commit + `c991dc26` 升级(浅史仓符合预期)。3 单测(pickaxe 引入者 / 行历史跟随 / 错误路径友好串)+ 全 mcp_tools 38 绿 + ruff clean。bug-rca SKILL 工具表 + 证伪纪律 + agent prompt 同步(候选难分胜负时查引入史,辅助证据非硬门;`memory_memorize` 的 `introduced_by` 参数按 backlog 原则没做——真需求再触发)。


**场景**:用户问"这个 bug 是哪个 commit 引入的"。[SZZ-Agent](https://www.researchgate.net/publication/403379901_How_and_Why_Agents_Can_Identify_Bug-Inducing_Commits)(2026)证实的路线:SZZ 出候选 + agent 语义裁决。

**双价值**:① 引入 commit 的 message / diff 常直接暴露根因意图(给假设循环加一路证据);② bug_lesson 记忆可带 `introduced_by` 溯源。

**设计**(确定性部分归工具,语义裁决归 agent——既有分工原则):
- 输入:根因锚定(file:line 或符号)+ repo_path。
- 确定性部分:`git log -S <符号>` / `git log -L <行区间>`(pickaxe / 行历史)出候选 commit 列表(时间倒序,封顶 ~20),每条带 message + 触达行摘要。
- 输出:候选表 + note("哪个 commit 真引入了缺陷逻辑 vs 只是重构搬移,是语义判断,交 agent / 人裁决")。
- 零 LLM、纯 git——第 16 个 MCP 工具,薄实现。
- 配套:bug-rca SKILL 加一步(假设循环的辅助证据);memory_memorize 考虑加 `introduced_by` 可选参数(按需,不抢跑)。

**验证**:wpa / bluez 真仓上对一个已知引入 commit 的 bug 跑,候选表里有金标。

### 8. patch_report 的 `deep=True`:删参或实现(建议先删)

> **✅ 已删(2026-08-17)**:五接触点(graph.run 签名+docstring / nodes 读 state / _analyze 形参+docstring / state.py 字段 / CLI `--deep` 参数+透传)全清,三处注释留痕「真需要逐 PR 深审时按 deep_research 子 agent 模式实现」。剩余 "deep" 全是 deepin 打包无关词。patch_report 16 测回归绿。


**问题**:docstring 自认"deep 留 stretch"([_analyze.py:44](../src/hyperion/workflows/patch_report/_analyze.py#L44)),参数透传但空壳——违背"诚实信号"原则。

**改法**:建议先删参数 + 注释留痕(真需求来了按 deep_research 的子 agent 模式实现);等 P-A 遗留的 deep 需求(Gerrit 逐 PR 深审)真出现再建。

---

## ⚪ 低优先 / 触发再做(记判断依据,不排期)

| 项 | 判断依据(什么时候做) |
|---|---|
| 老 workflow 资产抢救(报告渲染 / 结构化 memorize 成独立工具) | skill 主路径的 export_report / memorize 已覆盖;只有当某能力确认主路径缺了再做 |
| LSP 层归档说明 | 全套 clangd 封装零消费 + 硬依赖 compile_commands.json(与"不编译"冲突);建议文件头加归档注释防后来者误接 |
| 语言扩展(C++ / Rust / Go) | GRAMMARS 数据驱动表,加语言 = 加表项;**真有目标仓再加**,现在加是猜需求 |
| compare 跨版本函数配对半自动工具(签名相似度 + hub 排名预配对) | 先跑几次 compare e2e 统计"配对花掉多少步",痛了再建 |
| chunker 的 callers/callees 回填(P1.5 欠账) | 检索质量有实证短板(eval 指标掉)才触发 |
| bug-rca 候选根因自动打分(置信数值化,像 CogniGent) | 先落 #2 的定性版(清单 + 淘汰);数值化是否值得看 e2e 误诊率 |
| 查询类型 boosting(PascalCase → 类查询)与三级检索降级 | retrieval.py backlog 已记;等 eval 发现具体 query 形态掉分 |

## 明确不做(与两篇分析的"明确不做"节一致,此处汇总)

- 编译 / 测试 / 复现验证(用户封顶;correctness 只报 apply-based / reasoning-based)。
- 日志切片专用工具(filter_logs 已撤,opencode 的 read/grep/awk 等价且更灵活,踩坑 #2)。
- 平行定位管线(opencode 已会读码定位,建了就是重复建设)。
- delegate 多阶段 workflow 复活(skill 主路径已覆盖等价能力)。
- Neo4j 图数据库 / 跨仓联合图(单机零服务定位 + 独立仓无共同祖先,自动配对是伪确定性)。
