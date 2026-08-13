---
name: backlog-fragments-handoff
description: "补三块 backlog 碎片 —— invalidate 前缀(已闭环)/ repo_overview 大仓截断 / memory_memorize 多 evidence + kind_detail/confidence 透传"
metadata:
  type: project
---

**2026-08-13**:architecture-review 七项 + correction-link 全落后,可建队列空,补「验证优先」轮暴露的三块 backlog 碎片。**碎片 #1 复核发现已闭环(无需改),碎片 #2/#3 实改 + 测绿。**

## 背景

[e2e-validation-2026-08-13-handoff](e2e-validation-2026-08-13-handoff.md) 跑 onboarding + memory-health 两 skill 真机 e2e,记了两渣症 + 一个「CLI invalidate 没接前缀」的口头 backlog。本轮挨个排:发现 #1 其实已闭环(e2e 交接时记过头了),#2/#3 是真缺口。

## 碎片 #1:memory invalidate 接 _resolve_id —— ✅ 复核发现已闭环(无需改)

**诊断校正(踩坑#20:别信交接卡的口头 backlog,实证)**:e2e 交接卡说「`_resolve_id` 给了 mark_corrected/set_invalid,但 `memory invalidate` CLI 还没接到」。**复核调用链发现全通**:
- CLI `hyperion memory invalidate <id>`([cli.py:281-282](../../src/hyperion/cli.py#L281))→ `svc.invalidate(id)` → `store.set_invalid(id)`。
- bug 2 修复(commit `5fe2a99`)给 `set_invalid` **也**接了 `_resolve_id`([store.py:472](../../src/hyperion/services/memory/backends/native/store.py#L472)),不只 mark_corrected。
- `_resolve_id` 的 docstring 本就写「agent 据此调 mark_corrected/corrects **或 memory invalidate** 时传 8 位前缀」。
- 测试早覆盖:`test_memorize_corrects_accepts_id_prefix`([test_memory_native.py:163-166](../../tests/services/memory/test_memory_native.py#L163))断言 `set_invalid(other.id[:8]) is True`,注释明写「CLI memory invalidate 同路径」。

**结论**:闭环链路 CLI→service→store 一直透传 id,bug 2 修 `set_invalid` 时就覆盖了 CLI invalidate(同方法)。**无需改代码/测试**,只在交接卡/CLAUDE.md 标「已闭环」。关联 [[correction-link-handoff]]。

## 碎片 #2:repo_overview 大仓社区截断 —— 真改

**痛点**:onboarding e2e 暴露 wpa 746 社区塞进单个工具返回爆 `body[:8000]` 截断 → 「每次首个社区处截断,hub/bridge 部分取不到」,agent 被迫用 repo_map+call_chain 绕路(最后还是出报告,但浪费步数)。

**根因读码**([mcp_memory.py:595 旧版](../../src/hyperion/tools/mcp_memory.py#L595)):`json.dumps(result)[:8000]` 一次硬截断;`result` 里 communities 是 list[dict] 每个**带全量 members qn**(大社区几十上百个),communities 段最 bulky → 撑满 8000 → 末尾的 hub_nodes/bridge_nodes/warnings(架构最关键的)被静默丢。

**修(三招,纯图查询层不动 CRG)**:
1. **社区 cap**:新 `max_communities: int = 30` 参数(默认 30),按 `size` 降序取最大 N 个(header 仍诚实报真实总数 `n_total_comm`,不受 cap 影响)。
2. **社区瘦身**:每社区把 `members`(全量 qn)压成 `member_count` + `sample_members[:5]`(代表性样本),不堆几十个 qn。`cross_community_edges` 也 cap 到前 20(warnings 本就少不动)。
3. **重排 body 顺序**:`hub_nodes`/`bridge_nodes`/`warnings`/`cross_community_edges` 移到 **communities 前**。communities 是最 bulky + 单条最不重要 → 放末尾,即便截断丢的也是社区清单,枢纽/咽喉/告警永不丢。截断阈值提到 12000(对齐 ToolOutputBudget 量级),触发时附**诚实 note**(`[截断:... 要更多社区:重调 repo_overview 加大 max_communities]`),不静默丢。
   - 注:note 不指向不存在的工具 —— 复核发现**没有 `communities` 专用 MCP 工具**,故指向「重调 repo_overview 加大 max_communities」(真实可用路径)。

**测试**:`test_repo_overview_large_repo_caps_communities_and_keeps_hubs`(假图 50 社区×100 member+长描述,验 4 点:header 诚实报总数 50+「本调用含 30/共 50」、member_count+sample 压缩、hub 在 `"communities"` 键前、截断 note 触发+给补取路径)。原 2 测不破。

## 碎片 #3:memory_memorize 多 evidence + kind_detail/confidence 透传 —— 真改

**痛点**:onboarding e2e 记的架构事实 `evidence=[]` 空。**根因双重**:
1. 工具只接单 `file`/`line`([mcp_memory.py:179 旧签名](../../src/hyperion/tools/mcp_memory.py#L179))→ 构造 `evidence=[Evidence(file,line)] if file else []`,架构事实涉及多 file:line 塞不进。
2. onboarding SKILL step7([SKILL.md:42](../../.claude/skills/onboarding/SKILL.md#L42))早就写 `memorize(..., evidence=[<file:line+片段>], kind_detail=architecture, confidence=...)` —— **SKILL 假设工具收 list,但工具收不下**;且 `kind_detail`/`confidence` 也不是工具参数 → 静默丢(架构事实全记成默认 `kind_detail=module`)。

**修(纯加参数,向后兼容;schema `KnowledgeItem.evidence` 早是 list[Evidence],零 schema 改)**:
1. **`evidence: list[dict]`** 参数:每条 `{"file", "line?", "snippet?"}`,去重(同 `(file,line)` 只留一条),与旧 `file`/`line` 合并(旧单锚点向后兼容);脏条目(无 file / line 非数字)跳过不崩。line 接受字符串数字。
2. **`kind_detail: "module"|"symbol"|"architecture"`**:codebase_fact 才有意义;None→默认 module。onboarding 记架构事实终于能标 architecture 层(而非全落 module)。
3. **`confidence: float`**:0..1 显式覆盖(否则按 source_tier delegate=0.5)。

**为什么这是生产级正确**(对齐 deer-flow `memory/manager.py` fact 字段 + mnemopi schema):架构事实 = 多锚点(入口+派发表+回调),单 file:line 表达不了;`kind_detail` 是分层检索(architecture/module/symbol)的 key,onboarding 的架构导览事实该在 architecture 层被 recall 命中,不是混在 module 级零散事实里被 onboarding step2 判「主题对不上」走重跑。这正是 onboarding e2e「recall 命中 10 条但都零散模块事实、没一条架构级」的部分原因 —— 之前记的时候全压成 module 了。

**测试**:`test_memory_memorize_multi_evidence`(假 svc,验 6 点:多锚点写入、同锚点去重、脏条目跳过、line 字符串解析、旧 file/line 合并向后兼容、snippet/kind_detail/confidence 透传)。原 2 memorize 测不破。

## 改的文件(本轮)

1. [src/hyperion/tools/mcp_memory.py](../../src/hyperion/tools/mcp_memory.py) —— `repo_overview`(max_communities cap + 社区瘦身 + body 重排 + 诚实截断 note)+ `memory_memorize`(evidence list[dict] + kind_detail + confidence)。
2. [tests/test_mcp_tools.py](../../tests/test_mcp_tools.py) —— `test_repo_overview_large_repo_caps_communities_and_keeps_hubs`(新)+ `test_memory_memorize_multi_evidence`(新)。

## 验证

- **52 测绿**(mcp_tools 30 + memory_native 22;原 50 + 新 2)+ ruff clean。
- **不跑真模型 e2e**(用户自验铁律);onboarding 真机 e2e 验「架构事实 evidence 非空 + kind_detail=architecture」待下次真机跑(本轮纯工具层,SKILL step7 语法本就写着 evidence=[...] 现在工具收得下了)。

## 不做(YAGNI)

- 不加 `communities` 专用 MCP 工具(repo_overview 加大 max_communities 已够分页;CRG 的 `get_communities` 不是 MCP 工具,加它 = 造平行查询入口,踩坑#2)。
- 不改 CRG `get_communities`/`architecture_overview`(它们是 Hyperion 依赖,只读参考 + 改了跨项目;瘦身在 Hyperion 工具层做够)。
- 不动 schema(`KnowledgeItem.evidence` 早是 list,`kind_detail`/`confidence` 早是字段 —— 只差工具没暴露)。
- 不给 repo_overview 做真分页(limit/offset 翻页):onboarding 用不到全量 746 社区(top hub/bridge + 最大 N 个社区够讲架构);真要全量是审计需求,用 memory_dump 同款分页模式按触发再加。

关联 [[e2e-validation-2026-08-13-handoff]] [[onboarding-skill-handoff]] [[correction-link-handoff]] [[pitfall-log]](#20 草稿判断实证推翻 —— 碎片 #1 复核)。
