---
name: compare-skill-handoff
description: "2026-08-12 跨版本代码对比调研 compare skill 落地 —— 1 skill 0 新工具,镜像 upstream-merge 只读调研型;3 阶段对比法(锚定入口→语义配对→逐节点对照);memorize 读码即记(本 skill vs 其他 4 个的核心差异=下次秒答)。蓝连流程真数据探针全绿。"
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

关联 [[backport-workflow-handoff]] [[upstream-merge-handoff]] [[opencode-config-drift]] [[pitfall-log]](#2 漏斗) [[skill-prompt-writing-style]] [[route3-cross-version-handoff]](cross_version_diff 能力边界)。
