---
name: backport-workflow-handoff
description: "2026-08-12 跨版本回移植(v25 fix → v20)backport skill 落地 + e2e 真机全绿 —— 1 skill 0 新工具,镜像 upstream-merge;语义判 bug(A 方案)+ 路径适配 + validate_patch 验 apply。commit 9311973(opencode 同步 3a12721)"
metadata: 
  node_type: memory
  type: project
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-08-12T05:18:18.512Z
---

**2026-08-12 backport 工作流落地完成 + e2e 真机全绿**:1 个 `backport` skill + 1 个 opencode agent block,**0 个新 MCP 工具**(用户拍板 #1)。commit `9311973`(skill+agent+handoff+CLAUDE.md)+ `3a12721`(opencode.json 同步,见 [[opencode-config-drift]])。**e2e(opencode 自驱 sdp 任务)23 工具 7 步全过**,backport 卡 + 纯净补丁落盘,v20 仓已还原测试前。

## 做了啥

- **`.claude/skills/backport/SKILL.md`**(新):镜像 `upstream-merge` 结构(frontmatter name/description/allowed-tools → # 标题 → 两边界 → ## 运行模式 7 步(step 3 判 bug / step 6 验 apply 标【硬门】)→ ## 工具表 → ## 硬约束 → 输出格式 backport 卡 → ## 不要)。
- **`config/opencode_hyperion.json`** 加 `hyperion-backport` agent block(mode primary / steps 30 / edit+bash+read+grep+hyperion*+skill 全 allow)。**注意:upstream-merge 没加 agent block**(它只读不改 fork,原生 skill 默认权限够);backport **step 5 要 edit 改 v20 代码**,故要专用 agent block 放开 edit+bash,对齐 `hyperion-bug-rca`。

## 核心设计:1 skill,0 新工具

- **形态** = 镜像 `upstream-merge`(单文件 SKILL.md + opencode agent block),**不建 workflow / 不引 graph 节点**。
- **0 工具**(用户拍板 #1,对齐 pivot + 踩坑#2):候选 `symbol_lookup`(字面函数名→索引已解析函数体)与 opencode `grep`+`read` 重叠。**sdp 探针实证 grep+read 够稳**(sdp 函数体不大、边界清晰),未触发"grep 找 C 大函数边界不稳"→ symbol_lookup 不建(按触发再建,同 backlog #60)。

## backport vs upstream-merge 的核心差异(写进 skill)

upstream-merge 有确定性锚(`merge_eval` patch-id 三态);**backport 没有确定性"判 v20 有 bug"的工具** —— 这是 VeriPort(arXiv 2606.22704)定义的 **vulnerability oracle**:VeriPort 用可执行 PoC 判,**Hyperion 铁律永不编译** → 这一步**只能语义判断**(opencode 读 v20 函数体对照 v25 fix-point = backlog A 方案)。故 backport 比 upstream-merge 更依赖 LLM 推理,没有等价薄确定性工具可建。

**关键认知**:`merge_eval` 跨两条独立发行版线**不可用**(patch-id 需共同祖先,backlog 实测全 conflict;[code_graph.py:362-363](../../src/hyperion/services/code_index/code_graph.py#L362-L363) + apply 检查误用 worktree [code_graph.py:387-389](../../src/hyperion/services/code_index/code_graph.py#L387-L389))。backport skill **不用 merge_eval**,allowed-tools 里也没它。

## sdp 真数据探针(step3/step6 全绿)

用 v25 `c50c7ea` 的 `fix-integer-overflow-in-sdp_extract_seqtype.patch`(SDP_SEQ32 分支 `bt_get_be32` 赋 `int *size` 加 `INT_MAX` 溢出检查):

| step | 验了啥 | 结果 |
|---|---|---|
| **3a/3b** | grep 在 v20 定位目标函数 | `sdp_extract_seqtype` 定义 v20 `lib/sdp.c:1222`(v25 是 `lib/bluetooth/sdp.c`,**路径漂移确认**) |
| **3c 核心** | 读 v20 函数体对照 v25 fix-point 语义判 bug | v20 `:1255` `*size = bt_get_be32(buf)` 无 INT_MAX 检查 → **判:有同一 bug** |
| **5** | 适配 v25 修复意图到 v20 | 路径 `lib/bluetooth/sdp.c`→`lib/sdp.c`,行号对齐 v20(1222/1253) |
| **6 硬门** | `validate_patch` 验适配补丁打 v20 | `forward_method: strict`(一次过)、`verified: True` —— adapted apply 干净 |

**核心结论**:0 工具路线成立。grep+read 一步定位 C 函数,语义判 bug 准确,路径适配后 strict apply 通过。

## skill 流程(7 步)

1. 拿 v25 fix(read 补丁 / `git show <sha>` / `git show <sha>:debian/patches/<name>.patch`)
2. 理解 fix(改哪个函数/堵什么漏洞/fix-point)
3. **判 v20 有无 bug【硬门·核心】**:grep 找函数 + read v20 函数体,对照 v25 fix-point 语义判,三态(有同一 bug→继续 / already_fixed→停 / 函数没了→incompatible 停)
4. call_chain/blast_radius(codebase=v20)看影响面,别漏 caller
5. edit 改 v20 目标文件,照 v25 修复**意图**不照搬行号(两版路径/签名常漂移)
6. **验 apply【硬门】**validate_patch(补丁, v20_repo_path),打不上回⑤改到干净 apply 或判 incompatible
7. export_patch+export_report 落盘出 backport 卡 → 用户真机验证通过后才 memorize

## 复用工具(零改动)

`search_codebase`/`call_chain`/`blast_radius`(per-codebase,codebase 指向 v20 如 `bluez_v20`)/ `cross_version_diff`(看两版差异)/ `validate_patch`(硬门)/ `export_patch`·`export_report`(落盘)/ `memory_recall`·`memory_memorize`(验证后收尾)/ `ensure_repo` + opencode 原生 `read`/`grep`/`bash`(grep 定位+read 读函数体是 step3 核心)。

## VeriPort 借鉴 + 不照搬

- **借鉴**:① `direct/adapted/incompatible` 分级(skill 让 opencode 输出);② git-apply ladder(strict→3way→patch)= validate_patch 已覆盖;③ vulnerability oracle 概念(= "判 v20 有无 bug")。
- **不照搬**:PoC/regression oracle(依赖执行验证,与不编译铁律冲突);MSP 隔离(debian quilt 补丁通常已纯,v1 不剥 hunk);跨独立仓 patch-id 判等(merge_eval 已证不可信)。

## 故意不做(YAGNI)

- **不建新 MCP 工具**(symbol_lookup)—— grep+read 等价(踩坑#2);按触发再建(sdp 探针未触发)。
- **不建 workflow** —— skill 形态够(镜像 upstream-merge)。
- **不做 PoC/regression oracle** —— 不编译铁律。
- **不做跨独立仓 patch-id 判等** —— merge_eval 已证不可信,靠语义判。

## e2e 真机结果(2026-08-12,opencode 自驱 sdp 任务)

`hyperion-backport` agent 自驱跑完 sdp backport,**23 个工具调用、7 步全过**(2.5min,exit 0):
1. `skill(backport)` → `read`(v25 patch)→ `grep`(v20 定位 `sdp_extract_seqtype`)
2. `memory_recall`(codebase=bluez_v20)+ `read` v20 `sdp.c`/`sdpd-request.c`
3. **step3 硬门语义判**:`read` v20 `sdp.c:1222` 函数体,`:1261` 裸 `*size = bt_get_be32(buf)` 无 INT_MAX 检查 → **判:有同一 bug**(准确,金标一致)
4. `call_chain`(codebase=bluez_v20)4 个外部 caller 都处理返回 0 失败路径 → 适配安全
5. `edit` 改 v20 sdp.c(照 v25 意图:`val32` 中间变量 + `INT_MAX` 守卫)
6. **step6 硬门**:`validate_patch` strict → ✅ clean apply(agent 还先撤销自己 edit 验 forward-check,再重新应用 —— 验证姿势正确)
7. `export_patch`(落 `bluez.patch` 全量 + `bluez-backport-sdp_extract_seqtype.patch` 纯净 2-hunk)+ `export_report`(backport 卡)

**关键验证点全绿**:语义判 bug 准(无确定性工具靠 LLM read 函数体)/ strict apply 过 / 路径适配对(`lib/bluetooth/sdp.c`→`lib/sdp.c`)/ 未 memorize(守「验证后才记」边界)。**gotcha:v20 仓 dirty(quilt 基线),export_patch 落全量补丁含基线 → agent 额外手搓纯净 2-hunk 补丁单独落盘**(SKILL.md 提示了「从我的 hunk 取」)。

## 已 commit 文件(commit 9311973 + 3a12721)

- `.claude/skills/backport/SKILL.md`(新)
- `config/opencode_hyperion.json`(+ hyperion-backport agent block)
- `opencode.json`(cwd 实际加载,同步模板 —— 见 [[opencode-config-drift]])
- handoff memory + MEMORY.md + CLAUDE.md(backport backlog → 已成)

关联 [[backport-workflow-backlog]](原实测 backlog) [[upstream-merge-handoff]] [[pitfall-log]](#2 漏斗) [[skill-prompt-writing-style]] [[bug-rca-skill-toolbox-hitl]]。
