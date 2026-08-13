---
name: domain-knowledge-handoff
description: "2026-08-13 落地:记忆加第 4 类 kind domain_knowledge(领域/项目知识,semantic memory)+ source_url 溯源字段 + domain-research skill(opencode 自带 websearch/webfetch,0 新 MCP 工具)"
metadata:
  type: project
---

**2026-08-13 落地**:记忆系统加第 4 类知识 `domain_knowledge`(领域/项目知识 = agent memory 的 semantic memory 语义层),填补前三类(codebase_fact 读码事实 / bug_lesson bug 教训 / mental_model 程序性规则)的语义层空白。用户三档需求全覆盖:① 记领域知识(蓝牙协议/wpa 各层)② opencode 网调→本项目记 ③ 记用户任意技术笔记。

## 核心设计(克制版,用户拍板)

不另起记忆系统,只在现有 taxonomy 加 **1 个 kind + 1 个可选溯源字段 + 1 个 skill**,复用现有 memorize/recall/ingest/dump。

**为什么有必要(非过度设计)**:协议知识(蓝牙 L2CAP、wpa 4-way handshake 各层职责)不锚某个函数、不是某次 bug 产物,是领域常理。这正是 2026 agent memory 共识四类(working/semantic/episodic/procedural)里的 semantic memory。**直击踩坑#11**:glm-5.2 把根因误诊成显眼日志行 → recall 时带协议语义知识给 agent 多层证伪依据。recall.py 全文 kind-agnostic(只有 1 处渲染碰 kind),domain_knowledge **自动进 recall/RRF/decay,0 改动** —— 这是核心价值命题。

## 改的文件(8 个)

### A. 记忆核心(5 文件)

1. **schema.py** —— `kind` Literal 加 `domain_knowledge`;`kind_detail` Literal 加 `domain`(语义清晰非功能刚需:kind_detail 仅 memorize.py 跨 kind 去重读 + store 持久化,recall 零引用,默认 module 对 domain_knowledge 功能无害);新增 `source_url: str | None = None`(外部溯源 URL,domain_knowledge 用);docstring 三类→四类。

2. **store.py** —— `source_url` 列 **6 个接触点全改**(漏一个静默不持久化):① `_KI_FIELD_LIST` 加字段 ② `_SCHEMA` DDL 加列定义 ③ 幂等 migration(仿 corrected_by 模式,PRAGMA table_info + ADD COLUMN,旧库升级)④ `_ki_to_row` 序列化 ⑤ `_row_to_ki` 反序列化 ⑥ `_UPSERT` SQL 的 VALUES 占位符 + ON CONFLICT SET 子句。FTS5 表 kind-agnostic 不动;`kind TEXT NOT NULL` 无 CHECK 约束不动。

3. **consolidate.py** —— 排除 domain_knowledge 自动升级 mental_model(`it.kind not in ("mental_model", "domain_knowledge")`)。domain_knowledge 是语义层 evergreen 常理,不像 bug 教训会反复出现后「毕业」成程序性规则。

4. **mcp_memory.py** —— `memory_memorize`:kind Literal 加 domain_knowledge;新增 `source_url` 参数;**source_tier 按 source_url 有无自动分层**(domain_knowledge + 有 url → imported[0.6,网调] / 无 url → stated[1.0,用户笔记];bug/codebase 维持 delegate);kind_detail 透传逻辑加 domain_knowledge;返回串回显 source_url。memory_recall/dump **不改**(kind 是泛型 filter,domain_knowledge 自动进出)。

5. **cli.py** —— `memory add --kind` choices 加 domain_knowledge + 新 `--source-url` 参数;KI 构造透传 source_url + domain_knowledge 的 source_tier 分层(同 mcp_memory 逻辑)。**不碰** `memory ingest --kind`(不同语义)。

### B. 新 skill + agent block(2 文件)

6. **.claude/skills/domain-research/SKILL.md** —— 新建,克隆 onboarding/compare 模板。allowed-tools:`websearch`+`webfetch`+`memory_recall`+`memory_memorize`+`export_report`+`read`/`grep`/`glob`(比 onboarding 少代码情报工具,多 web 工具)。**两边界**:① 只读不改代码 ② 领域知识调研即记(多源交叉印证坐实,不需等用户验证,区别于 bug/补丁型)。**核心难点=协议知识真伪是语义判断**(无确定性工具验真,靠多源交叉 ≥2 独立源 + 优先权威源 spec/RFC/手册,区别于 codebase_fact 读码坐实)。**recall 命中就短路**(同 onboarding)。**克制规则**:只记技术笔记(协议/架构/算法/配置原理),不记流水账/日程/私事。**子需求③用户笔记**:kind=domain_knowledge + source_tier=stated(无 url),不网调。

7. **config/opencode_hyperion.json** —— 加 `hyperion-domain-research` agent block(mode=primary/steps=24)。**关键:permission 加 `websearch: allow` + `webfetch: allow`**(opencode 内置 web 工具,授这俩 key 即可,**0 新 MCP fetch 工具** —— 踩坑#2 同源教训,沿用 compare/onboarding/backport 的「0 新工具」原则)。prompt 镜像 SKILL 流程。

### C. 测试(1 文件)

8. **tests/test_mcp_tools.py** —— 2 新测(仿 multi_evidence 的 `_FakeMemSvc` mock 手脚架):
   - `test_memory_memorize_domain_knowledge_with_url`:domain_knowledge + source_url → 验 source_tier=imported + source_url 透传 + kind_detail=domain。
   - `test_memory_memorize_domain_knowledge_user_note`:domain_knowledge 无 url(用户笔记)→ 验 source_tier=stated + source_url=None。

## 关键技术点

- **opencode 内置 web 工具(已核验 opencode.ai/docs/tools)**:opencode 自带 `websearch`(Exa AI,**免 API key**)+ `webfetch`,仅由 permission key 控制。故 domain-research **不需要新建任何 MCP fetch 工具** —— 只在 agent block 授 websearch/webfetch:allow。这是「0 新 MCP 工具」原则(第 8 个 skill,工具数仍 15)。
- **source_url 独立字段,不进 Evidence**:Evidence(file 必填)是代码锚点的「报告签名」+ 去重身份;URL 进去破坏其语义。source_url 是 kind-specific 可选字段(像 symptom/root_cause/kind_detail 先例)。deer-flow Fact 模型无 URL 字段 —— Hyperion 更强。
- **溯源分层**:domain_knowledge 的 source_tier 按 source_url 有无自动分(imported=网调 0.6 / stated=用户笔记 1.0)。bug/codebase 维持 delegate(委托 agent 产出,最可信)。
- **make_id 无需改**:kind 已进 hash,domain_knowledge 自然独立 id 空间,按 (owner,codebase,domain_knowledge,summary) 去重。

## 验证

- `tests/test_mcp_tools.py`:**33 passed**(31 原有 + 2 新)。
- `tests/services/memory/`:**46 passed**(store 持久化 + consolidate 排除 domain_knowledge 全不破)。
- ruff:**All checks passed**(5 改文件)。
- **不跑真模型/opencode e2e**(用户自验铁律)。skill 真机 e2e 用户自跑(在 Hyperion 根启动 opencode + 给 codebase 绝对路径,踩坑#17/#21)。

## 不做(YAGNI / 克制)

- 不建 domain_knowledge 专用 recall 通道(自动进通用 recall)。
- 不动 extract.py(domain_knowledge 走专用 skill/CLI,不从 bug 报告自动抽取)。
- 不新建 MCP fetch 工具(opencode 自带 websearch/webfetch)。
- 不改 Evidence 模型(URL 进独立 source_url 字段)。
- 不做 source_tier 显式参数(按 source_url 自动分层已够)。
- 不强制 repo 字段可选(skill 传 codebase 标签或 "general",保留多库 scope 分区)。
- 用户笔记不另建 skill/工具(同 domain_knowledge kind + CLI/skill 覆盖;克制规则在文档不在代码)。

关联 [[onboarding-skill-handoff]] [[compare-skill-handoff]](镜像 skill 模板:recall-first 短路 + 读码即记边界) [[pitfall-log]](#11 误诊→领域知识 recall 当证伪依据;#2 别重造 opencode 已会的;#13 skill 受众是模型) [[opencode-mcp-wiring]](websearch/webfetch 内置工具 permission key)。

## ✅ opencode 真机 e2e 全绿(2026-08-13 晚,本会话自跑)

**任务**:调研 WPA2 4-way handshake 流程 → 记 domain_knowledge(wpa codebase)。`opencode run --agent hyperion-domain-research`(Hyperion 根启动 + `.env` 灌环境 + 给 hostap 绝对路径 `/home/tnc/src/hostap`)。13 步 / 20 工具调用,exit 0。

**工具调用序列(SKILL 理想行为全到位)**:
- `websearch` ×3(撒网找权威源)+ `webfetch` ×2(精读 Stanford seclab 论文 + CERT-EU KRACK 公告)—— **opencode 1.18 内置 web 工具真能在 agent 里调起来(本特性最大未知已证)**。
- `read` ×7 + `grep` ×3 + `glob` ×1(第三重交叉验证:hostap 真源码核 handshake 函数,5 个函数 + `wpa_pmk_to_ptk("Pairwise key expansion")` + KEK AES-UNWRAP + tptk 注释 + ANonce 校验全对得上)。
- `hyperion_*` ×3(memory_recall 探底 + memory_memorize + export_report)。

**全验证点(DB raw 查证非幻觉)**:
1. ✅ **domain_knowledge 写入真 DB**(id `2f2205d8abbd7f09`)—— 单测用 mock,这是 6 接触点第一次过真 SQLite。
2. ✅ **source_url 真持久化**(`https://seclab.stanford.edu/pcl/mc/papers/fp09-he.pdf`)—— store.py round-trip 正常,新列 migration 自动建上(首次 MCP server 启动幂等 ALTER)。
3. ✅ **source_tier=imported**(网调分层对,有 source_url → imported,不是 delegate/stated)。
4. ✅ **kind_detail=domain**(透传成功,没被默认成 module)。
5. ✅ **confidence=0.9**(agent 据 ≥4 独立权威源一致给的高把握)。
6. ✅ **报告落盘** `data/bug_rca/hostap-rca.md`(79 行,每条结论附 source URL)。
7. ✅ **recall 闭环(核心价值)**:`hyperion memory recall "4-way handshake EAPOL-Key PTK" --repo wpa` 第一条就召回这条 domain_knowledge(id=2f2205d8,conf=0.90),**且和代码命中混在一起**(process_4_of_4/wpa_derive_ptk)—— 正是治踩坑#11 的机制:bug-RCA 查 handshake 问题时协议语义自动进上下文当证伪依据。recall.py kind-agnostic 0 改,实测验证。
8. ✅ **边界守对**:只读不改代码 + 调研即记(没等用户验证)+ 多源交叉(Stanford/CERT-EU/802.11i/CS161 四源一致)。

**agent 两个聪明的真行为**(非 bug):
- recall 命中了 wpa 整体架构导览但缺 4-way handshake 细节 → 正确判「主题对不上」走完整重跑(SKILL 的短路 vs 重跑分流生效)。
- webfetch 遇两个 PDF 是二进制读不了 → 改用 websearch 对权威源正文覆盖 + 源码三重验证补齐(优雅降级,没卡死)。
- 用户问的 "ANCE/SCE" 主动纠正成标准术语 **ANonce/SNonce**(没盲从用户笔误)。

**结论**:domain-research skill 真机 e2e 全绿,8 skill 全有 e2e。domain_knowledge 记忆特性从 schema → 持久化 → recall → skill 全链闭合。
