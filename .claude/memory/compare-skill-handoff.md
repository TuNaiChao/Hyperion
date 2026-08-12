---
name: compare-skill-handoff
description: "2026-08-12 跨版本代码对比调研 compare skill 落地 + opencode e2e 真机全绿 —— 1 skill 0 新工具,镜像 upstream-merge 只读调研型;3 阶段对比法(锚定入口→语义配对→逐节点对照);memorize 读码即记(本 skill vs 其他 4 个的核心差异=下次秒答)。e2e:44 工具 16 步,报告落盘+2 条记忆写 DB(raw 查证);steps 28;两环境坑记 opencode-mcp-wiring。"
metadata:
  node_type: memory
  type: project
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-08-12T00:00:00.000Z
---

**2026-08-12 compare skill 落地**:1 个 `compare` skill + 1 个 opencode agent block(`hyperion-compare`),**0 个新 MCP 工具**。填现有矩阵唯一空白 —— 4 个旧 skill(backport/bug-rca/patch-review/upstream-merge)全 bug/补丁导向,**没有调研/对比型**。用户原话:「opencode 上问『v20、v25 蓝牙在连接流程上有什么差异』快速准确回答」。

## 做了啥

- **`.claude/skills/compare/SKILL.md`**(新):镜像 `upstream-merge` 只读调研型骨架(frontmatter name/description/allowed-tools → # 标题 → 两边界 → 核心难点 → ## 运行模式 7 步 → ## 工具表 → ## 硬约束 → 对比卡输出格式 → ## 不要)。
- **`config/opencode_hyperion.json`** 加 `hyperion-compare` agent block(mode primary / steps 20 / permission **read-only**:edit deny + bash deny + read/grep/glob/list/hyperion*/skill allow)。**opencode.json 现是 symlink → 模板**(见 [[opencode-config-drift]]),改模板一处即生效,不用 cp。

## 核心设计:1 skill,0 工具,3 阶段对比法

**形态** = 镜像 upstream-merge(只读调研型,不改代码),不建 workflow(pivot 后一律 skill)。

**3 阶段对比法**(方法论取 **Code Researcher** ICLR 2026 + **Augment Code** 2025 分层检索律:broad context → focused analysis → dependency traversal):
- **A 锚定流程**(broad):两版各跑 `search_codebase`(传概念)+ `repo_map` + `call_chain`,拿各自入口函数群 + file:line。
- **B 逐节点对照**(focused):配上的每对函数 `read` 两版函数体讲差异。
- **C 综合对比**(traversal):节点差异聚成流程级差异表 + 因果解读。

**核心难点 = 两版函数配对是语义判断**(v20 foo ↔ v25 bar 可能改名/拆分/合并),无确定性工具(各 codebase 结构图独立无联合图)—— 和 [[backport-workflow-handoff]]「判 v20 有无 bug 是语义判断」同构。

## memorize 读码即记 —— 本 skill vs 其他 4 个的关键差异

其他 4 个(backport/bug-rca/patch-review/upstream-merge)守「未经验证不 memorize」(涉及 bug/补丁是否真修对,要等用户真机)。**compare 是纯读码事实,读完即记** → 下次问同类问题 `memory_recall` 命中 = 用户要的「秒答」。(用户拍板「读码即记」选项,非「用户确认后记」也非「verified=False」。)

## 刻意不带的工具(踩坑#2 + 能力边界)

- **不带 cross_version_diff** —— 单仓两 ref 专用([code_graph.py:152-156](../../src/hyperion/services/code_index/code_graph.py#L152-L156) 只一个 repo_param + docstring 三证),两独立仓(v20/v25)无效。backport 里它也只是「可选」。
- **砍补丁三件套**(validate_patch/export_patch/edit)—— 不改代码。
- **不带 blast_radius**(改了影响谁,对比用 call_chain 更合适)/ merge_eval/fetch_patch(上游/PR 专用)。

## 蓝连流程真数据探针(全绿)

bluez v25 = `code-test/v25/bluez` / v20 = `code-test/v20/bluez`;索引 `data/code_index/{bluez,bluez_v20}` + `data/structgraph/{bluez,bluez_v20}` 全在。

| step | 验了啥 | 结果 |
|---|---|---|
| **2 锚定** | `search_codebase("bluetooth connection establishment", codebase=各)` | v25 18790 行命中 `emulator/btdev.c:conn_add_sco/cis` 等;v20 14637 行命中 `src/device.c:probe_service` 等。**两 codebase 各跑通,只回真实符号** |
| **2 骨架** | `CodeGraph.open(cb).repo_map()` | 两版各返 `{map_text, top_symbols...}` PageRank 骨架 ✓ |
| **2/3 定位+配对** | grep `connect` 入口函数 | `connect_next`/`btd_device_connect_services`/`device_profile_connected`/`btd_device_is_connected` 两版同名直配;**v25 独有 `btd_device_bearer_is_connected`+`btd_device_bdaddr_type_connected`(v20 无)** → 流程级差异线索 |
| **4 逐节点对照** | read 两版 `device_add_connection` | **真差异**:v25 `(dev,bdaddr_type,flags)` 多 `flags` 参 + `btd_bearer_connected()` bearer 通知 + `state->initiator=flags&BIT(3)`;v20 `(dev,bdaddr_type)` 无。**grep+read 语义配对准,无确定性工具** |

**核心结论**:0 工具路线成立。3 阶段对比法在真数据上跑通,语义配对 + read 对照能发现真流程级差异(bearer 层信号 + initiator 记录是 v25 新增)。

## skill 流程(7 步)

1. 确认两版 codebase + 流程主题(连接/配对/SDP/GATT...);没仓 ensure_repo。
2. **锚定流程入口【阶段 A】**:两版各跑 search_codebase(概念)+ repo_map + 必要 call_chain。
3. **建两版函数对应【语义判断·核心】**:同名直配,名字不同 read 判同职责,配不上标「仅一版有」。
4. **逐节点对照【阶段 B】**:配上的每对 read 两版函数体讲差异 + memory_recall 两 codebase 各查。
5. **聚流程级结论【阶段 C】**:节点差异 → 流程级差异表 + 因果。
6. export_report 落对比报告(每条附双源 file:line 防幻觉)。
7. memorize(kind=codebase_fact, kind_detail=architecture, 带双源 evidence)— **读码即记**。

## 复用工具(零改动)

`search_codebase`/`repo_map`/`call_chain`(per-codebase,两版各跑一次)/ `read`·`grep`·`glob`(配对+对照核心)/ `memory_recall`(两 codebase 各查)/ `memory_memorize`(读码即记)/ `export_report`(落对比报告)/ `ensure_repo`。

## 故意不做(YAGNI)

- **不建新 MCP 工具**(跨版本函数配对是语义判断无确定性工具可建,同 backport symbol_lookup 先例,踩坑#2)。
- **不建 workflow / 不改造 deep_research**(写死单仓 state.py:31-32 + 已降级参考,改造=重写)。
- **不用 cross_version_diff**(单仓两 ref,两独立仓无效)。
- **不做跨 codebase 联合图**(各版结构图独立,靠 agent read 配对)。

## 待 commit 文件

- `.claude/skills/compare/SKILL.md`(新)
- `config/opencode_hyperion.json`(+ hyperion-compare agent block,经 symlink 自动到 opencode.json)
- handoff memory + MEMORY.md + CLAUDE.md(低优 backlog 标「已成」)

## ✅ opencode e2e 真机全绿(2026-08-12,hyperion-compare 自驱蓝连流程对比)

`hyperion-compare` agent 自驱跑完 v20/v25 蓝牙连接流程对比,**44 工具调用、16 步自然收尾**(steps 上限 28 没撞)。报告落盘 `data/compare/bluez-rca.md`(81 行)+ **memorize 2 条 codebase_fact 写进 DB(id `f8613b28`/`cc82239d`,raw DB 查证非幻觉)**。

工具序列对得上 skill 设计:`skill(compare)` → `search_codebase`×4 + `repo_map`×2(两 codebase 各跑,锚定入口)→ `read`×23 + `grep`×7(逐节点读两版函数体对照)→ `memory_recall`×2 → `export_report`×1(落盘)→ `memory_memorize`×2(读码即记)。

agent 自驱结论(与我手工探针一致,且更深):**连接主流程两版 1:1 同构**(`connect_next`/`hci_create_connection` 逐字节相同),差异在四个控制面 —— ① 错误码升级为结构化字符串码(ERR_BREDR_CONN_*/LE_CONN_*)② 连接权能护栏收紧(bonding 互斥/profile 权限过滤)③ **bearer 子系统落地**(v25 新增 src/bearer.c,`device_add_connection` 多 `flags` 参 + `state->initiator` + `btd_bearer_connected`)④ LE 状态前置。**与我探针发现的 `device_add_connection` v25 多 flags+bearer 通知完全吻合**。

### ⚠ e2e 踩到两个环境坑(已记 [[opencode-mcp-wiring]])

1. **opencode 不读 `.env`,LLM provider key 走 shell env** —— `opencode run` 报 401 Invalid API key(uniontech-ai/deepseek-v4-flash-0731 via ai.getdeepin.org),但 `.env` 里 `UNIONTECH_AI_API_KEY` 明明有。opencode 的 `"apiKey":"{env:UNIONTECH_AI_API_KEY}"` 从 **shell 环境变量**读,不 load `.env`。**修法:跑前 `set -a; . ./.env; set +a`**。
2. **code-test/ gitignored → opencode glob 看不见** —— 第一轮 e2e agent 卡在找仓库路径(glob 尊重 .gitignore 过滤掉 code-test/),耗光 steps=20 预算没到落盘。**且 compare 是只读 skill(bash deny),agent 不能 find/ls 找路径**。**修法:prompt 里给仓库绝对路径;steps 20→28(读两版大函数体需要预算)**。**SKILL 已可考虑加一句提示**(索引返回相对路径时 read 用绝对前缀 / 或让用户给路径)。

### steps 20→28 的依据

e2e1(steps=20)跑满 20 step 撞上限,只到 step 3(锚定+配对),没到 read 对照/export/memorize。e2e2(steps=28 + 绝对路径)16 步自然收尾,全 7 步走完。**28 留够 read 两版函数体的预算**(compare 的重活在 step 4 逐节点对照,不像 backport 只读单边)。config 已定 28。

## ✅✅ 「下次秒答」验证 + recall 短路修复(2026-08-12,commit 770c97e)

验证「记忆让下次秒答」:全新 session 问同一问题,看 recall 是否被优先用。

**e3(改前,无短路指令)**:recall 命中(两 codebase 各召回完整对比事实 1245/894 字符)且**被第一步调用**(优先级对了),**但 agent 仍整轮重跑** —— read×22 + search×4,与冷路径几乎一样(42 工具 vs 44)。agent 自述:「记忆已命中(conf=0.50),**但按 compare skill 流程我需要重新跑一遍读码验证**」。还重复 memorize×2(按 id 去重不污染,但白花步数)。

**根因 = 注入层 gap**(对标 [[memory-design-review-2026-08-12]] 那类复核):召回层工作了,但 SKILL.md 把流程写成固定 7 步 playbook,agent 当「每次必走完」的流水线,recall 命中后没敢短路。

**修法(SKILL.md + agent prompt 改「recall 优先 + 命中短路」)**:
- step 1 第一步必 `memory_recall` 两 codebase 各查;命中同主题对比事实 → 复用直接出对比卡(step 5/6),**不重跑 search/read**。
- 短路路径**不 memorize**(DB 已有);只有没命中/主题对不上才走完整 A→B→C。
- 关键约束 + 「不要」段都加「recall 命中就短路,别为走完流程又 search/read 一遍」。

**e4(改后,短路生效)**:`skill` → `memory_recall`×2 → `export_report`,**完事**。4 工具 / read×0 / memorize×0 / ~40s。报告质量不降(入口配对表 + 3 大差异面 + 双源 file:line 全在,复用记忆)。

| 路径 | 工具 | read | memorize | 历时 | 质量 |
|---|---|---|---|---|---|
| 冷 e2e2(首次) | 44 | 23 | 2 | ~5min | 完整 |
| 热 e3(改前无短路) | 42 | 22 | 2(重复) | ~4min | 完整但重跑浪费 |
| **热 e4(改后短路)** | **4** | **0** | **0** | **~40s** | 完整(复用记忆) |

**结论**:-90% 工具 / -100% read / ~7× 提速,报告质量不降。compare skill 价值闭环坐实:**首次调研→读码即记→下次 recall 命中→零重跑秒答**。

### gotcha(给后续 skill 的教训)

**「记忆召回」≠「记忆被用」**。recall 工具能命中只是召回层工作;**注入层**(skill/prompt 指令)必须显式写「命中→短路」分支,否则 agent 会把固定流程当流水线走完,记忆成了「召回了但不影响行为」。这条对其他带 memory_recall 的 skill(backport/bug-rca/patch-review/upstream-merge)同构 —— 只是那些 skill 的 recall 是「线索」(定位辅助),compare 的 recall 是「结论」(可直接复用),所以 compare 最该短路。后续若给其他 skill 加短路,判据 = recall 命中的是线索还是结论。

关联 [[backport-workflow-handoff]] [[upstream-merge-handoff]] [[opencode-config-drift]] [[opencode-mcp-wiring]](e2e 两个环境坑) [[memory-design-review-2026-08-12]](注入层复核同构) [[pitfall-log]](#2 漏斗) [[skill-prompt-writing-style]] [[route3-cross-version-handoff]](cross_version_diff 能力边界)。
