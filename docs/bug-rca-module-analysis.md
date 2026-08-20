# bug 根因定位模块设计分析

> 这是 RootRecall 三大支柱里 P2「bug 根因定位」(★MVP)的当前实现分析。
> 源码真相在 `.claude/skills/`(菜谱)、`src/rootrecall/tools/mcp_memory.py`(硬门工具)、`src/rootrecall/services/workspace/`(验证),本文档只做"讲清它在干什么、怎么设计的"。

---

## 0. 一句话:bug 定位在干什么

输入:源码仓 + 一段故障现场(日志 / 漏洞报告 / crash 栈)。输出:**根因 + 补丁 + 分析报告**。

**比喻**:这是刑警破案,不是流水线质检。普通 agent 拿到日志容易犯的错,是**锚定最响的那声警报**(满屏 `ERROR` / `abort failed` 里挑一行显眼的当"根因")——就像警察只抓喊得最响的人,而真凶早在两小时前就离开了现场。本项目实测踩过这个坑:模型把"abort failed"当根因,实际它是"扫描早完成了、状态没清"的**后果**(症状)。

RootRecall 的 P2 就是把"老刑警的办案纪律"固化成 agent 可执行的菜谱(skill)+ 一组确定性"取证工具"(MCP):

```
假设 ↔ 证伪循环            补丁 ↔ 验证循环             收尾
(找线索立嫌疑人,           (改代码 → 验 apply →         (报告落盘 + 教训早记
 每轮主动找反证)            落盘补丁交人)                [标"未真机验证"];
                                                        真机通过后同补丁重提
                                                        一次,升级记忆)
```

**核心分工**:重活(读码 / 改代码)归成熟的 coding agent(opencode / claude code),RootRecall 负责它不会的三件事——**历史同类案件的记忆召回、确定性验证工具、办案纪律**。

**两个铁律**(全模块设计的地基):

1. **`validate_patch` 过 ≠ 修对**。工具只验补丁"贴得上"(git apply),不验"修得好"。系统软件没有像样的单元测试,**真正的 oracle 是人 / 真机复现原故障**。
2. **验证状态必须显式**。没真机验证过的教训**可以早记**(先验对下一个案子有价值),但工具层强制打 `unverified` 标 + 置信封顶 0.5,recall 渲染带「(未真机验证)」—— 记忆的价值全在可信,而可信靠"看得见的验证状态",不靠"憋着不记"。真机通过后同一补丁重提一次(`verification: real_machine`),同 id 合并升级、洗掉标记。

---

## 1. 主菜谱:bug-rca skill —— 两个循环 + 一个收尾

skill(`.claude/skills/bug-rca/SKILL.md`)不是分步流水线,是教 agent **按需调工具的纪律**。这套设计演化过两代:初版是固定流水线(召回 → 定位 → 修复 → 报告一条道走到黑),实测发现**一次走完的假设是错的**——根因判错时流水线只会坚定地错到底;二代改成"工具箱 + 人在环":工具按需调、循环走到收敛、人在关键门拍板。

### 假设 ↔ 证伪循环(定位)

- **取证**:`memory_recall`(翻这个仓的历史同类案件——"以前办过的案子")+ `search_codebase`(找嫌疑函数,只回真实存在的符号)。
- **大日志用 grep/awk 自己切**:按故障时间窗(HH:MM:SS)+ **日志词汇**关键词筛——注意是日志里的话术(scan / result / timeout),**别用代码符号**(日志是散文,`scan_res_handler` 这种标识符子串匹配不上);封顶行数,别一次 read 全量(1.6 万行日志直接撑爆上下文)。
- **切窗是线索不是答案**:根因形态多样——可能在窗口上游更早、在很久以前的持久化状态 / 配置、在别的日志源、在源码逻辑里。窗口只见现象没看到因时,逐步扩大窗口 / 换日志源 / 查源码,**别锚定窗口里最响的行**。
- **每轮主动证伪**:立了根因先找**推翻它的证据**,找不到再定论。

### 补丁 ↔ 验证循环(修复)

`edit` 改码 → `validate_patch` 验 apply(硬门)→ `export_patch` 落盘 → 没修对就再来一轮。**每出一版补丁就落盘一版**——步数将尽时优先把当前版落盘交人,别烂在锅里。

### 收尾(报告落盘 + 教训分级入册)

补丁过 `validate_patch` 并 `export_patch` 落盘后:`export_report` 出报告,`memorize`(kind=bug_lesson,`verification: apply_only`)早记教训 —— 自动带 `unverified` 标、置信封顶 0.5。**人停在哪儿**:export 之后 —— agent 把推理链和补丁摆好,真机验证是人拍板;验证通过后同补丁重提一次 `real_machine`,条目原地升级。

### 证伪纪律(对抗误诊,从真踩坑提炼)

- **多假设清单**:定位先列 2-3 个候选根因(按记忆先验 + 日志线索),逐个找证据 / 反证,按证据强度**淘汰制**而非认定制——单候选思维 = 锚定。全部候选被淘汰才回头扩大搜索。e2e 实证:报告里出现「候选淘汰记录」,B / C 候选各有时序 / 证据被淘汰,根因与金标逐点吻合。
- **时序检查**:现象不得早于 purported 根因。早于 = 抓到的大概率是症状,回去往更早查。
- **别用残缺证据证伪先验**:日志切片可能漏了更早事件,不能断言"X 没发生过"。
- **日志是线索,代码是确定答案**:真根因(状态机 / 分支逻辑 / 持久化状态)用 `search_codebase` 在源码里确定,重心放代码。
- **候选难分胜负时查引入史**:根因锚定到符号 / file:line 后 `when_introduced` 出引入 commit 候选表,引入 commit 的 message / diff 常直接暴露缺陷意图——辅助证据路,不是硬门。

这套纪律对应 2026 业界的 hypothesis-testing 流向([CogniGent](https://arxiv.org/html/2601.12522v2):多 agent 假设检验定位;[DoVer](https://huggingface.co/papers):假设生成 + 主动验证)——方向一致,RootRecall 用"菜谱纪律"而非"多 agent 编排"实现,是单机 harness 的现实取舍。

---

## 2. 确定性工具:两个"取证实验室"

全模块只有两处做硬判定,都是**零 LLM、可复现**的:

### validate_patch —— apply 门

`validate_patch(patch, repo_path)`([validate.py:19](../src/rootrecall/services/workspace/validate.py#L19))验补丁能否干净地打到目标仓:

- **验证梯子**(从严到宽三级):strict `git apply --recount --check` → `--3way --check`(容 context 漂移)→ `patch -p1 --dry-run`(非 git 经典补丁)。返回实际停在哪一级(strict / 3way / patch),**梯子本身就是补丁质量信号**——要降级才能过的补丁,context 已经漂了。
- **LF 归一化**:agent 传补丁常丢末尾换行 / 带错行尾,先归一化再验——治"git 报第 N 行损坏"的实证坑。
- 诚实边界(docstring 明写):只验 apply,不验修对;编译 / 复现测试**永不做**(用户封顶:系统软件环境重、信号歧义,不值),真机归用户。

### merge_eval —— 合入三态判定

`merge_eval(upstream_base, upstream_head, fork_ref, repo_path)`([code_graph.py:281](../src/rootrecall/services/code_index/code_graph.py#L281))回答"上游一批 commit 该不该合进 fork":

- **already_fixed**:用 `git log --cherry-pick --right-only` 对称差(底层 patch-id 等价)取反集——范围内**不在**"还没等价进 fork"名单里的 commit 就是已修。比逐个算 patch-id 高效,不用扫 fork 全史。
- **recommend_merge / conflict**:逐 commit `git merge-tree --write-tree <fork_ref> <commit>` **在对象库里试合并**(rc=0 干净 / rc=1 冲突),不碰工作树、不需要 checkout——三态不再押在"agent 先切到 fork 且树干净"的调用姿势上;git < 2.38 或 merge-tree 跑挂 → 回退老路(commit diff 跑 `git apply --check`,对当前工作树,note 明示"三态可能失真")。
- **uncertain**:取不到父 commit 等异常。

### 配套小工具

`export_patch`(从工作树 `git add -A && git diff --cached` 观察**真实改动**——不信 agent 嘴里说的 diff,**拒绝写空 diff**,治"改错树 / 假装改完";quilt 源码仓的 `.pc/` 构建产物自动排除,检出带 bug 号时另归档一份到 `<bug号>/`)、`export_report`(报告落盘,同款归档)、`fetch_patch`(拉 PR / Gerrit 补丁)、`ensure_repo`(确认仓在)。

---

## 3. 四个姊妹 skill —— 补丁生命周期的其余场景

主菜谱管"从故障到修复",四个姊妹各管一段:

| skill | 场景 | 一句话 | 硬门 | memorize 时机 |
|---|---|---|---|---|
| `bug-rca` | 故障 → 修复 | 定位 + 修复 + 迭代 | `validate_patch`(apply) | apply 后记 unverified,真机后升级 |
| `patch-review` | 鉴定一个补丁 / PR | 干啥 / 贴得上吗 / 波及谁 / 该不该合 | `validate_patch` | apply 后记 unverified,真机后升级 |
| `upstream-merge` | 上游一批 commit 合不合进 fork | `merge_eval` 三态表 + 相关性判断 | 三态表(fork_ref rev-parse 可解析即可,零 touch) | apply 后记 unverified,真机后升级 |
| `backport` | v25 已修 → 改独立发行版线 v20 | 跨版本回移植 | **判 v20 有无同一 bug**(纯语义)+ `validate_patch` | apply 后记 unverified,真机后升级 |

**backport 的特殊性**值得单独说:两条独立发行版线**没有共同 git 祖先**,patch-id 等价判定失效,所以刻意**不用** merge_eval——判"v20 有没有同一个 bug"是纯语义判断,靠 grep 定位 v20 对应函数 + read 函数体对照 v25 的 fix-point。这是全模块唯一"核心判定零工具"的场景,也是人在环最重的地方。

**继承同一套纪律**:"apply 过 ≠ 修对"写进每个 skill 的正文、硬约束、"不要"清单三处;验证状态走结构化标注(`verification` 参数 → unverified 标 + 置信封顶,真机后同补丁重提升级)—— 早期"验证前憋着不记"的禁令在真 e2e 里被证明守不住(agent 会记),改为让标记替纪律站岗。

---

## 4. 记忆联动 —— 办案时翻旧案卷

P2 的差异化在于**每个环节都接记忆**(详细机制见[记忆模块分析](memory-module-analysis.md)):

- **定位前**:`memory_recall(trigger)` 翻历史同类案件——"这模式之前见过,这是当时的修法"。先验是线索不是答案,以本次证据为准。
- **定位后**:根因候选定稿前用 `problem_summary` 当 query 再召回一次历史修法(把已设计的机制用足)。
- **验证后**:`memorize` 沉淀 bug_lesson(根因 / 修法 / 影响面 / 补丁 / commit 溯源;apply 过即记、标 unverified,真机后同补丁重提升级)——下次同类 bug 的先验。
- **召回质量有治理**:四路召回(BM25 + 向量 + 代码检索 + 结构图)RRF 融合 → 时间衰减 → 置信度加权 → **被纠正条目降权 0.3×**(旧误诊排后面但保留可考古);consolidate 巩固会检测"补丁已合入上游"(标 `merged_upstream` 打折)和"同主题打架根因"(标 `needs_review` 待裁决)。

**闭环**:这次修的 bug 是下次的先验;修错了会被纠正链降权——记忆随办案越来越准,这正是"持续学习"在 P2 的体现。

---

## 5. 工作区与补丁观察 —— 不信嘴,信现场

老 workflow 时代的 workspace 机制(每 bug 一个隔离目录 + AGENTS.md 契约 + delegate 日志归档)降级后,主路径的补丁观察收敛成一个原则:**不信 agent 报告的 diff,信 git 现场的 diff**。

- `export_patch` 从目标仓工作树 `git add -A && git diff --cached` 取**真实改动**,空 diff 直接拒绝——agent 说"我改好了"但树里没动静,当场戳穿。
- backport e2e 把验证姿势升级为 **git worktree 隔离**(pristine 树上 strict 验 apply,不动工作树)——验证行为本身也不能污染现场。
- 报告强制 file:line 溯源,证据行号防御解析(拿到的行号可能是字符串,coerce 成 int 再用)。

---

## 6. 对照 2026 业界:当前在什么位置

| 业界实践 | 本项目现状 |
|---|---|
| [CogniGent](https://arxiv.org/html/2601.12522v2)(多 agent 假设检验定位,2026) | ✅ 核心增益(多候选假设并行 + 各自证伪淘汰)已以"菜谱纪律"形态内建——多假设清单 + 淘汰制,e2e 实证候选淘汰记录生效 |
| [AgentFL](https://www.alphaxiv.org/overview/2403.16362v1) / [LLM4FL](https://openreview.net/forum?id=z91EvZbSI1)(项目级定位:Context → Debugger → Verification) | ✅ 殊途同归——delegate 多阶段(localize → repair)曾做过,pivot 后 Debugger 角色归 opencode,RootRecall 保留取证工具 + 纪律 |
| [CrashFixer](https://arxiv.org/html/2504.20412v1)(Linux kernel crash → 修复)、[AMD RGD MCP](https://gpuopen.com/learn/post-mortem-gpu-crash-debugging-with-llms/)(crash 工具链接 MCP) | ✅ 同一定位(系统软件 crash / 日志 → 根因),RootRecall 多出的 **带记忆 + 带溯源** 是这批工作没有的 |
| [SZZ-Agent](https://www.researchgate.net/publication/403379901_How_and_Why_Agents_Can_Identify_Bug-Inducing_Commits)(SZZ 出候选 + agent 裁决"哪个 commit 引入了 bug",2026) | ✅ 同款分工已落地——`when_introduced` 双锚点(pickaxe 符号 / `-L` 行历史)出候选表,引入者裁决归 agent;真仓探针:hostap `scan_only_handler` → 唯一候选 `66fe0f70` = 金标引入点分毫不差 |
| blast radius / impact analysis(2026 业界做成 PR 实时信号) | ✅ `blast_radius` 图驱动波及面,patch-review 在用 |

**结论**:办案纪律(证伪循环 + 多假设淘汰)、硬门(apply 梯子)、记忆闭环(先验 → 修法 → 沉淀 → 纠正)三件套都已在 2026 主流线上,且"带记忆的 bug-RCA"在系统软件垂直场景是差异化定位。原待办的三处收尾(多候选假设纪律 / auto-query 召回硬化 / bug 引入 commit 定位)已全部落地,剩余改进是触发级的(候选根因置信数值化等,记 CLAUDE.md「低优 backlog」)。

---

## 7. 明确不做(YAGNI,防未来跑偏)

- **编译 / 测试 / 复现验证永不做**(用户封顶):系统软件构建环境重、测试信号歧义(通过 ≠ 修对),RootRecall 验到 apply 为止,真机归用户。correctness 措辞只报 apply-based / reasoning-based,不报 tested / verified。
- **不建日志切片专用工具**:`filter_logs` 建过又撤(deer-flow / omp 双证 opencode 的 read / grep / awk 等价且更灵活)——重造 agent 已会的就是踩坑#2。日志领域的知识(时间窗 / 日志词汇 / 窗口会漏根因 / 重心代码)进菜谱,不进代码。
- **不建平行定位管线**:opencode 自己就会读码定位,RootRecall 建平行管线就是重复建设——正确姿势是给它"记忆 + 确定性工具 + 纪律"(delegate-already-localizes 原则)。
- **delegate 多阶段 workflow 不复活**:localize → repair 的 verify-refine 收敛逻辑有价值,但 skill 主路径已覆盖等价能力,留作参考实现不接回。
- **验证不做 Tier 1/2**(复现测试 / 对抗审):当初规划过,与"不编译不复现"冲突,砍。
