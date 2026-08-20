# RootRecall 待办交接文档(todo.md)

> **用途**:换会话 / 换 agent 时的交接卡。一页读完"现在到哪了 / 下一步干啥 / 该看哪些文档和参考项目 / 哪些铁律别违反"。
> **权威路线**:CLAUDE.md「路线」+「路线复核」段(以此为准,本文件不重复路线明细)。
> **最后更新**:2026-08-19(仓库生命周期层 F1–F5 全落地 + 后续计划定稿见 §2;本卡同日重写对齐)。

---

## 0. 一句话项目认知

**RootRecall(2026-08-17 前叫 Hyperion)= 给系统软件(C 代码库,如 wpa_supplicant / bluez)做「带记忆的 bug 根因定位 + 深度调研」的领域 harness —— 记忆 + 代码情报 + 补丁验证 + 标准流程 skill,作为 MCP tool/skill server 供 opencode 调用。**(2026-08-06 pivot 后不再自跑固定管线)

差异化在「**记忆 + 持续学习 + 精准的工具与标准流程**」,不在重造 coding agent —— **重活(读码/改码/定位)归成熟 coding agent**,RootRecall 自做「记忆 + 代码情报 + 硬门验证 + 沉淀」。三支柱(P1 深度调研 / P2 bug-RCA / P3 记忆★特色)共享平台 + 共享服务层。

### 三锁定决策(别推翻)
1. **记忆** = 自有 `MemoryService` 契约 + v1 native 后端(SQLite+FTS5+jieba+sqlite-vec 向量);cognee/mem0 可换、v1 不用。
2. **bug-RCA 重活委托**给 coding agent(抽象 `CodingAgentDelegate`,v1 默认 omp/opencode)。
3. **验证封顶 apply**(Tier 0):**永不编译/测试/复现**(用户真机自验);correctness 只报推理结论,不报 tested/verified;真机验证过才 memorize。

---

## 1. 当前进展(现状快照)

**已落地(17 个 MCP 工具 + 8 个 skill + 记忆闭环 + 仓库生命周期层 + P0 自动开仓,全量 pytest 338 绿 + ruff clean)**::
- **代码情报(8)**:search_codebase / blast_radius / call_chain / cross_version_diff / repo_map / repo_overview / merge_eval(merge-tree 零 touch)/ when_introduced(SZZ 式引入 commit,第 16 工具)
- **记忆(3)**:memory_recall / memory_memorize(含 `corrects` 纠正声明)/ memory_dump(分页体检);4 类 kind(codebase_fact / bug_lesson / mental_model / domain_knowledge)+ `source_url` 溯源
- **确定性硬门(3)**:validate_patch / export_patch / export_report(opt-in 产 AGENTS.md)
- **PR 抓取(2)**:fetch_patch / ensure_repo(Gerrit 鉴权 + URL 分流;F1 起查注册表、clone 后自动登记)
- **skill(8 个,opencode 原生发现 `.claude/skills/`)**:bug-rca / patch-review / upstream-merge / backport / compare / onboarding / domain-research / memory-health-check
- **CLI**:`rootrecall index(--seed 播种)/ repo(ls·register·resolve·checkout·sync·gc)/ install --global / here / models / memory / mcp / lsp`(+ bug-rca / research / patch-report 老编排器降级留参考)

**2026-08-19 主线:仓库生命周期层 F1–F5**(多代码库管理,细节见 [docs/cli.md](docs/cli.md) repo/install 节 + tests/e2e/ 五个 e2e):
- **F1 仓库注册表**(`data/repos.yaml`,registry.py):索引名↔路径↔角色↔bug 关联;IndexManifest 记 `repo_path`;5 个 MCP 工具的 repo_path **直接吃注册名**(注册表→索引清单→data/repos 反查);ensure_repo 接注册表
- **F2 全局安装**:`install --global`(skills 软链 + mcp 合并 + AGENTS.md 标记段,幂等可卸载)后**任意目录 opencode 免接线**;`here` bug 目录轻标记;quickstart 加全局注册步
- **F3 worktree + gc**:bare 镜像(`data/mirrors/`,全量保历史)共享对象库;`repo checkout` 秒开 ephemeral 检出;`repo gc` 级联回收(worktree+向量索引+结构图+记录;记忆/baseline 不碰;legacy 老索引只提示不删);workspace manager copytree→worktree(脏态覆写)
- **F4 repo sync**:fetch→ff→增量刷索引→`--analyze` 上游三态报告(merge_eval 纯 git 零 LLM,落 `data/upstream_reports/`);systemd timer / cron 样例在 `deploy/`
- **F5 index --seed**:小版本索引从同线基线拷贝播种,只重嵌差异文件(e2e 计数对账)
- 踩坑沉淀 #22–#26(见 [docs/踩坑记录.md](docs/踩坑记录.md) 第五章):upsert 静默降级 / 孤儿判定误删老索引 / 注册表副作用测试污染 / porcelain 列位解析 / dict.get 罩不住 None

**2026-08-13 → 08-17 主线**(改名 RootRecall 收口、第 8 skill、P1/P2 改进落地、文档一致性清零;细节见 `.claude/memory/` 各 handoff)。

---

## 2. 下一步要做的(2026-08-19 定稿,按序挨个完成)

> 主线 = 仓库生命周期层收尾,对齐用户三个期望:**① 单机轻量一键装 ② bug 目录问一句自动处理 ③ 共享基线 vs 一次性 bug 仓**。F1–F5 已把骨架搭完,下面是剩余的肉;每个大功能做完照例补单测 + e2e。

### P0 自然语言 → 自动开仓(期望②闭环,先做;全是接线活,半天量级)

- [x] **`find_repo` 暴露成 MCP 工具**(第 17 个,2026-08-20):项目+版本→注册表候选;**带版本时区分精确命中 vs Related**——find() 的 loose 兜底会把同项目基线冒充命中,工具层重分类(实现中修掉的真语义坑);未命中返回基线清单 + 带安装根、bash 可原样跑的开仓命令。进 minimal/research 预设。
- [x] **`repo checkout --index`**(2026-08-20):播种基线索引增量建(差异文件才重嵌);基线无索引→诚实全量;embedder 挂→跳过建索引不挡检出;**chdir 锚安装根**(agent 常在 bug 目录跑,cmd_index 的 data/ 相对落点防漂)。
- [x] **skill 补自动开仓路径**(2026-08-20):bug-rca 加「仓库就绪(别问用户要路径)」节 + 工具表行;backport 加 step 0 + 工具清单;AGENTS.md 路由加「仓库就绪三步」(install --global 原样抄它,单一真相)。
- [x] e2e(2026-08-20):`tests/e2e/test_p0_autoprovision_e2e.py` 两用例——① find_repo 未命中→按命令 checkout --index→worktree+播种索引就绪(embed 恰好只重嵌差异文件,F5 同款对账;manifest 记 repo_path)→ find_repo 复查命中 ephemeral;② 基线无索引→--index 诚实回落全量。338 测全绿 + ruff clean。

### P1 部署轻量化收尾(期望①最后两块)

- [x] **`ROOTRECALL_HOME` 环境变量**(2026-08-20):`data_root()` env 优先+安装根回落;`reanchor_data_path()` 把 config/默认参数里 `data/` 前缀相对路径改锚新家(绝对/其他相对不搬);全锚点改接(注册表/镜像/worktree/索引/结构图/记忆 store_path/clone_dir/检索束/export 交付物/上游报告)+ CRG build/update 补传 base_dir(原 cwd 相对漏洞)+ install 时 env 透传进 mcp 块。**硬约束:env 未设 = 零行为变化**(单测锁死)。e2e:迁家后注册表/镜像/worktree/索引/结构图全落新家,安装根不长 data/。附带:真实 wpa manifest 补 repo_path 后戳破一个老测试的隔离缺口(撞真实索引),按 #24 加固(_install_root 锚 tmp)。
- [x] **最小模式**(2026-08-20):实证后按真实阶梯写 —— **没有纯 BM25 检索路径**(hybrid_search 向量必传),零 key 两路:本地 sentence_transformers 档(全功能,数据不出本地)或暂不建索引(记忆 FTS+repo 管理可用,检索类不可用)。quickstart 缺 key 分支提示 + `rootrecall index` 零 key 友好指路(不甩栈)+ configuration.md「最小模式」节。344 测全绿。

### P1 定时分析深化(期望③的"分析"半自动化)

- [x] **`sync --analyze-agent`**(2026-08-20):headless `opencode run` 复核「该不该合」追加进报告(§Agent 复核);三档诚实降级(不在 PATH/超时/rc≠0 → 报告保持纯三态并注明);CLI 守卫「须与 --analyze 同用」。e2e 四用例(桩追加/无 opencode 退三态/ingest 项目名剥 -v/守卫)。
- [x] **同步报告入记忆**(2026-08-20):`--ingest-report` 把报告(含 agent 复核)摄取进记忆,codebase=项目名(baseline 名剥 -v 段);真机冒烟 18 条入 scope=bluez,recall top-2 可召回「该不该合 sdp_extract_seqtype」。
- [x] **systemd gc timer 样例**(2026-08-20):deploy/rootrecall-gc.{service,timer}(每周一 08:30)+ README 重写(两进阶档用法 + cron 行更新)。
- 真机冒烟(bluez v25→v20,10 真 commit):三态 honest 全 uncertain(quilt 打包 commit 对 v20 本就不可判);agent 复核 3447 字符逐条该合/不该合/存疑 + 总体建议(连 sdp 路径漂移都对,来自记忆库 backport 知识);meta 建议记 backlog:sync 报告把 debian/ 打包 commit 单独分组,免得 uncertain 淹没安全修复。

### P2 验证补强与小项

- [x] **真 opencode e2e**(2026-08-20 ✅全绿,详见 [p0-autoprovision-e2e-handoff](../RootRecall/.claude/memory/p0-autoprovision-e2e-handoff.md)):install --global 真装后,/tmp 空目录只放问题 txt+真日志,`opencode run` 一句「分析 wpa_supplicant 2.9.0.21 根因」→ 默认 agent 自动路由 bug-rca → find_repo(agent 自己解析项目+版本)→ 原样跑开仓命令(镜像+worktree+播种索引 0 重嵌+登记 ephemeral)→ 诊断根因与金标准同源(scan-only 误路由→p2p_scan_work 泄漏,时序证伪 abort 假说=踩坑#11 纪律生效)→ 补丁 strict apply+报告落盘+bug_lesson 入库 recall top-1。9 次 rootrecall 调用,~7 分钟,零人工干预。⚠ 发现:memorize 早于真机验证(SKILL 纪律偏差,记 backlog);⚠ headless 401 复现(opencode 不读 .env,跑前必须 source)。
- [x] **真实仓迁移演练**(2026-08-20 ✅):wpa/bluez_v20/bluez 三索引全部重跑=增量(bluez_v20 141 chunk 补漂移,余 noop)+ repo_path 落盘纳管;bluez v20/v25 adopt 成 baseline(挂既有索引);真实 `sync --analyze --analyze-agent --ingest-report` 出报告(见上)。另:本机数据已迁 ~/.local/share/rootrecall(2.3G,worktree repair + repos.yaml 路径重写 + .env ROOTRECALL_HOME + install 透传);踩坑 #28(dotenv 回灌漏进测试,setenv 空串治)/ #29(裸 python -c 看不见 .env,数据操作走 CLI)。
- [x] `data/bug_rca/` 按 bug_id 归档(2026-08-20):export_patch/export_report 双写 —— 平铺「最新一份」约定不变 + 查注册表 bug_id 再写 `<out_dir>/<bug_id>/` 副本(gc 回收仓后交付物仍可按 bug 追溯;查不到 bug_id 静默跳过不挡交付);名字输入时文件名用注册名(对齐 gc/索引命名)。
- [x] README/docs `wire_opencode.sh` 定位「项目级备选」(2026-08-20):不想全局注入 AGENTS.md / 无权写 ~/.config 时用;同款还有 rootrecall here。
- [x] memorize 纪律硬化(2026-08-20,P0 e2e 发现的偏差):`memory_memorize` 加 `verification` 参数 —— apply_only 打 unverified 标(**recall 渲染显式带「(未真机验证)」**,RecallHit 透传 tags)+ 置信封顶 0.5;真机验证后同补丁重提 real_machine(同 id 合并,新条 tags 替换)即升级。结构性规则替代「憋着不记」禁令;bug-rca/backport SKILL 措辞同步。
- [x] sync 报告 debian/ 打包 commit 单独分组(2026-08-20,agent 复核的 meta 建议):纯 debian/ 的 commit 单列「打包层」节,源码修复(尤其安全修复)不被 uncertain 海淹没;混合型(源码+debian 都动)留在源码组。354 测全绿。

### 三期望终验三件套(2026-08-20 下午,用户拍板按 1→2→3)

- [x] **任务1 干净环境安装演练**(期望①终验):fresh clone + quickstart 零 key / 全功能两轮 RC=0(工具已齐跳过 apt、最小模式提示纯文本、index rc=2 不打死脚本、ROOTRECALL_HOME 提示);**抓到两条真 bug 并修**:踩坑#30(echo 双引号里 Markdown 反引号=命令替换,零 key 机静默拉 800MB torch)/ #31(幂等安装跳过粒度太粗,换目录重装半换链 —— skill 软链吃旧根,mcp 跑新根)+ export_patch 排除 quilt `.pc/`(26 行修复膨胀 30 万行垃圾);install --global 换链/回链活体验证(opencode.json 逐字节还原)。356 测全绿。真·全新机(无 uv/无缓存)仍留给同事机/VM 复验。
- [x] **任务2 bluez 一句话 e2e + backport 行动闭环**(期望②贴脸+期望③全链,详见 [bluez-sdp-e2e-backport-handoff](../RootRecall/.claude/memory/bluez-sdp-e2e-backport-handoff.md)):真 tag 5.50.2(5.50.61 不存在)+ 素材全真零构造;Run1 bug-rca 根因与上游金标准逐字一致、memorize 带 apply_only(P2 纪律生效);Run2 backport 三 fix-point 语义判定+适配路径漂移+strict apply,53 行三合一补丁落盘,**memory_recall 给出 v20 路径线索 = P1 摄取记忆首次在行动链变现**;未 memorize 等真机(纪律在线)。⚠ 记 backlog:agent 对 Related 基线跳过了 ephemeral checkout 直接改脏基线(结论仍对);debian 仓 stash 往返值得 SKILL 提示。
- [x] **任务3 真上游 sync + timer 上线**(期望③收口,2026-08-20):bluez-v25 基线 url 切 github deepin-community(HEAD 与本地 synced_sha 无缝衔接);真网络 sync --analyze --analyze-agent --ingest-report 两轮全绿(0 新 commit=诚实空范围,agent 复核反向验证「空范围是真的」+ 提出 deepin 镜像 master 不常动/真上游在 kernel.org 的洞察,随报告入记忆);systemd user timers 真机上线(sync 每日+gc 周一 08:30,enable --now)+ 服务上下文整链验证(agent 复核 1578 字符成功);真机坑两个已修进样例:systemd user 极简 PATH 看不见 uv/opencode(Environment=PATH 行 + opencode 软链 ~/.local/bin)、agent key 走 EnvironmentFile=.env 注入。

### 仍在等触发 / 等验证(保留观察,不当新主线)

1. **backport 补丁真机复验**(等用户):sdp v20 补丁已验 apply + e2e 绿,用户真机验证通过后才 memorize。
2. **recall 价值验证 B 档**:需 N≥5 个类似 bug 场景定性(现 N=2 结论"无害但不显著"),靠攒真实案例。
3. **功能 3 记忆图边强化**:长期低优(等记忆量上去 + 关联查询需求出现)。
4. **触发式 backlog**(CLAUDE.md「低优 backlog」+ [backlog-production-grade.md](.claude/memory/backlog-production-grade.md)):代码情报硬化 9 条 / 评测严格化 7 条 / 记忆工具硬化 3 条;老低优表 7 条(老 workflow 资产抢救 / LSP 归档注释 / 语言扩展 / compare 配对半自动 / chunker callers 回填 / 置信数值化 / 查询 boosting 三级降级)均挂触发。
5. **明确不做**(防误当待办):stdio→http 切换(等 opencode 上游修注册 bug)/ fetch_upstream_commit / introduced_by 参数(YAGNI)/ R4 租户·鉴权 / R5 沙箱·前端 / Skill 子系统 S1-5 / 日志切片专用工具 / 平行定位管线 / delegate 多阶段 workflow 复活 / Neo4j·跨仓联合图 / 定时器自动合上游(只出报告,合不合走 upstream-merge 复核)。

---

## 3. 必读文档(按场景)

| 场景 | 文档 | 该看哪节 |
|---|---|---|
| 踩坑(动手前必读) | [docs/踩坑记录.md](docs/踩坑记录.md) | #1(rerank)/ #2(漏斗重复)/ #6(chunker)/ #7#8(runtime)/ #11(误诊)/ #13(skill 受众)/ #18(recall 短路)/ #21(git 污染)/ #22(upsert 降级)/ #23(清理误删老数据) |
| skill 怎么选(8 个判据+易混对) | [docs/skill-routing-matrix.md](docs/skill-routing-matrix.md) | 主表 + 易混对 |
| bug 定位现状分析 | [docs/bug-rca-module-analysis.md](docs/bug-rca-module-analysis.md) | 全文 |
| 代码调研现状分析 | [docs/code-research-module-analysis.md](docs/code-research-module-analysis.md) | 全文 |
| 记忆现状分析 | [docs/memory-module-analysis.md](docs/memory-module-analysis.md) | 全文 |
| 查工具/配置/CLI 参数 | [docs/mcp-tools.md](docs/mcp-tools.md) · [docs/configuration.md](docs/configuration.md) · [docs/cli.md](docs/cli.md) | 16 工具详参 / config 各段 / 命令行 |
| 待办总账 | [.claude/memory/backlog-production-grade.md](.claude/memory/backlog-production-grade.md) | 按需查 |
| v2 设计文档(architecture / memory / bug-rca / deep-research) | `docs-bak/设计/`(本机只读归档,未随 git) | 历史参考 |

> **CLAUDE.md「路线」+「路线复核」段是权威**,本文件不重复路线明细。`.claude/memory/` 下各 handoff 是每个已完成功能的全细节交接。

---

## 4. 参考的开源项目(本地只读副本,.gitignore)

> **铁律:设计任何模块前,先 WebSearch 调研 2025-2026 前沿 + 精读 deer-flow 对应实现,再动手。**(见 CLAUDE.md ⭐ 工作准则)

| 项目 | 本地路径 | 看什么 | 对齐 RootRecall 哪块 |
|---|---|---|---|
| **deer-flow**(ByteDance) | `deer-flow/` | `agents/factory.py` / `middlewares/`(TokenBudget/ToolOutput/Summarization)/ `memory/manager.py` / `workspace_changes/{scanner,diff}.py` | runtime 中间件 + 记忆 ABC + workspace_changes 范本 |
| **oh-my-pi**(omp) | `oh-my-pi/` | `packages/mnemopi/`(记忆 schema/consolidate)/ `packages/coding-agent/src/lsp/`(multilspy)/ `packages/hashline/`(行锚补丁) | 记忆持续学习 + LSP + 补丁格式 |
| **opencode**(delegate 目标 + skill 宿主) | (本机全局装) | `mcp/index.ts`+`catalog.ts`(MCP 一等公民)/ `cli/cmd/run.ts`(`--continue`/`--format json`) | 委托后端 + MCP 接线真相(踩坑#10) |
| **code-review-graph** | `code-review-graph/` | `graph.py` / `communities.py` / `analysis.py` | 代码情报底座(结构图) |
| **Aider** | (本地 `aider/repomap.py`) | `repomap.py`(PageRank) | repo_map(已落地) |

---

## 5. 铁律(别违反)

### 5.1 踩坑提炼的硬规则(详见 [docs/踩坑记录.md](docs/踩坑记录.md))
- **#1**:patch 多候选投票 rerank 已整体移除。**上多采样/投票前先问:有 oracle 吗?样本会真多样吗?** 两否 → 别上。
- **#2**:RootRecall 侧定位漏斗与 opencode 重复 → 整套砍,改工具驱动。**建任何能力前先问:opencode(delegate)是不是已经会?会 → 别建平行管线,把 RootRecall 独有的做成 MCP 工具给它调。**
- **#6**:chunker 超长 chunk 只修符号路径、漏 module 路径。**修"无上限"类 bug 要把所有产该产物的路径都过一遍;离线验证用全仓聚合统计,别只验点名样本。**
- **#7/#8**:ReAct 子 agent 撞 `recursion_limit` 硬抛会**全丢证据** → 优雅降级;`@tool` 在线程池懒导入重模块死锁 → import 提模块顶层。
- **#11**:glm-5.2 系统性把根因误诊成显眼日志行。**apply 过 ≠ 根因对**;重心代码 + 多轮证伪 + 真机 oracle。
- **#13**:skill/prompt 受众是**模型**(指令性),不是人;不要项目内部知识。
- **#18**:recall 命中要短路(召回层≠注入层,SKILL 别写成无脑流水线)。
- **#20**:草稿判断(读码印象/agent 自述)会被实证推翻。**下结论前先实证。**
- **#21**:测试仓 git 操作污染主仓 main。**Bash cwd 跨调用持久;git init/commit 前 pwd 确认;建测试仓全程 `git -C <path>` 不 cd。**
- **#22**:带具体默认值的 upsert 会把分派字段(role/状态)静默降级。**upsert 的枚举字段缺省 = 保留现值(None 哨兵),别给具体默认值;「操作成功但目标不动」先核数据再怀疑逻辑。**
- **#23**:清理/删除类功能把「字段缺失」(老数据常态)并入「判定失败」= 误删历史数据。**缺字段 ≠ 判定失败;带删除的功能上线前拿真实数据 dry-run 一遍。**
- 共性:**用户对复杂度/冗余的直觉通常对**;设计前先核前提 + 评估 YAGNI。

### 5.2 git / 安全
- **commit ≠ push**,push 单独确认。commit 直接提交 main(项目惯例)。
- **commit 用显式路径,别 `git add -A` / `git add .`**(防误带 `Python语法.md` / `todo.md`)。
- **`todo.md` 不提交**(本机交接卡,untracked);**`Python语法.md` 永不提交**(个人笔记)。
- **永不打印 API key 的值**(只布尔/非空检查);含 key 文件只 grep 绝不 cat。`.env` / `data/` / `docs-bak/` 是 gitignore。
- 破坏性/对外动作(push、删文件等)先确认;一处授权不延伸到下一处。

### 5.3 工作风格
- **代码我直接 Write/Edit**,同时用大白话 + 比喻讲清在干啥(面向小白);**README.md 例外 —— 对外门面用专业表述不用比喻**(2026-08-17 用户定)。
- 注释/docstring **面向小白**;**skill/prompt 面向模型**(指令性);**docs/*.md 面向小白+比喻,不带时间戳**。
- 跑测试时正文打印"测了啥 + 期望 + 实际(绿/红)",不只甩 Bash 输出块。
- 做了新功能**自己跑 opencode e2e**(不等用户自验);系统软件编译/复现仍用户自验。
- 引 arXiv 前先 WebFetch `arxiv.org/abs/<id>` 核验再 commit。
- 实现优先照齐 deer-flow,目标**生产级不是 demo**;每处简化记 backlog。
- 两台机协作(Linux + macOS):`uv sync` 靠 uv.lock;记忆靠 `scripts/setup_claude.sh` 软链到 `.claude/memory/`;建议两台机仓库路径一致。

---

## 6. 快速命令

```bash
uv sync --extra mcp --extra code-review-graph  # 装/同步依赖(MCP server + 结构图两个产品 extra)
uv run rootrecall models                        # 验配置 + 工厂加载
uv run rootrecall install --global              # opencode 全局注册(装一次任意目录免接线;--uninstall 卸)
uv run rootrecall here --codebase <索引名>      # bug 目录轻标记(.rootrecall.yaml + 项目 opencode.json)
uv run rootrecall index <repo_path> <name>      # 建向量索引 + 结构图(--seed <基线> 播种增量;--force 重建)
uv run rootrecall repo ls|register|resolve      # 仓库注册表(索引名↔路径↔角色)
uv run rootrecall repo checkout <名> --from <基线> --ref <tag> --bug <id>   # 秒开一次性检出(worktree)
uv run rootrecall repo sync [--analyze <fork名>] [--no-index]  # 基线同步 + 上游三态报告(定时器样例 deploy/)
uv run rootrecall repo gc [--dry-run]           # 回收过期 ephemeral(级联;先 dry-run 看清单)
uv run pytest -q -k "not kind_filter"           # 测试(跳挂真网络的 kind_filter)
uv run ruff check .                             # lint
uv run rootrecall mcp serve                     # MCP server(stdio;16 工具给 opencode 调)
```
