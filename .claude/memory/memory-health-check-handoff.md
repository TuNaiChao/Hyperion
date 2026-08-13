---
name: memory-health-check-handoff
description: "2026-08-13 记忆体检 skill 落地 —— 1 skill + 1 agent block + 第 15 MCP 工具 memory_dump(包已是契约的 MemoryService.list_items)。非 spec-drift(本会话查实确无浏览工具)。比 onboarding/compare 更严:连记忆库都只读 + 体检默认不 memorize。差异化卖点=治理型 agent memory(provenance/confidence/staleness/audit),Hyperion 字段天生带。opencode e2e 待真机跑。"
metadata:
  node_type: memory
  type: project
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-08-13T00:00:00.000Z
---

**2026-08-13 记忆体检 skill 落地**(architecture-review §六 功能2):1 个 `memory-health-check` skill + 1 个 opencode agent block(`hyperion-memory-health`)+ **第 15 个 MCP 工具 `memory_dump`**。填记忆能力唯一空白 —— 5 个旧 skill(backport/bug-rca/patch-review/upstream-merge/compare/onboarding)全调研或 bug/补丁导向,**没有「审记忆库质量」的能力**;现 2 个记忆 MCP 工具(memory_recall query / memory_memorize write)**也无浏览/导出入口**。用户问「我们对这个仓到底记了啥 / 哪些记忆可信 / 审一下记忆库」就靠它。

## 非 spec-drift(关键,本会话查实 —— 区别于功能1)

architecture-review §六 功能2 原写「**0 新工具或加 1 个 `memory_dump`**」留了选择。本会话查实:**确无浏览/导出工具**(memory_recall 是 query 式得先知道问啥,memory_memorize 是 write),但 `MemoryService.list_items(scope, *, kind, include_invalid)` **早已是契约**([manager.py:61](../../src/hyperion/services/memory/manager.py#L61) ABC + [service.py:161](../../src/hyperion/services/memory/backends/native/service.py#L161) NativeMemoryService 委托 `store.list_items`,[store.py:462](../../src/hyperion/services/memory/backends/native/store.py#L462) 按 `updated_at DESC` + repo/kind 过滤 + include_invalid 开关)—— 只是没包成 MCP 工具。故 **加第 15 个薄工具 `memory_dump` wrap 它**,**0 新服务代码**。这不是规范误差(区别于功能1 把 CodeGraph 方法误当工具),是真缺一个「摊全量」入口。

**为什么这是生产级正确划分**:memory_recall 和 memory_dump 是**互补的两种记忆访问模式** —— recall = 按相关性 query 挑几条(手电筒,解决「我查个主题」);dump = 一次摊全量看健康(卫星图,解决「这库质量咋样 / 哪些可信 / 哪些过期」)。一个 query 一个 browse,缺一不可,且都该是薄工具(踩坑#2:别让 agent 自己遍历调 recall 模拟 dump,那是碎工具逼多调)。

## 做了啥

- **`src/hyperion/tools/mcp_memory.py`**:
  - 加模块级 helper `_render_audit_card(it)`(放 `_retrieval_bundle` 前):把一条 KnowledgeItem 渲染成体检溯源卡 —— `[kind] summary @file:line(或 @无证据) conf=X.XX tier=Y sha=xxxxxxxx {created} hits=N [STALE(...)]`。和 RecallHit.render() 区别:recall 精简带 score 给 LLM;体检卡摊全量重点看四审计维度(置信度/来源/溯源/时效)。
  - 加工具 #15 `memory_dump`(插在 memory_memorize 后,L182 后):`async def memory_dump(kind=None, include_invalid=False, codebase=None)`,包 `svc.list_items(active_scope, kind=, include_invalid=)`,空 → 友好提示串(回显 codebase),有 → header「Memory dump: N items (codebase=X)」+ 每张溯源卡,`[:8000]` 截断(同其他工具)。per-call codebase(模板同 memory_recall)。镜像 memory_recall 的 6 pattern。
  - 模块 docstring 工具表 + build_server「十三个」→「十五个」注释同步。
- **`.claude/skills/memory-health-check/SKILL.md`**(新):镜像 onboarding/compare 只读调研型骨架,但**两处更严**(见下)。frontmatter `allowed-tools`:hyperion_memory_dump(主)+ hyperion_memory_recall(查细节/核矛盾)+ hyperion_search_codebase+read+grep+glob(必要时核 file:line)+ hyperion_memory_memorize(仅未决矛盾);无 bash(read-only,踩坑#16)。
- **`config/opencode_hyperion.json`** 加 `hyperion-memory-health` agent block(mode primary / **steps 16** / permission read-only)。steps 比 onboarding 24 少 —— 体检主重活在 dump 数据里读信号,不读一堆函数体;16 居中,e2e 饿再调。
- 2 单测 + 全 mcp_tools **27 绿** + lint 绿。

## 核心设计:比 onboarding/compare 更严的两条边界

onboarding/compare 已经是 read-only(不改代码),memory-health-check **更严**:

1. **连记忆库都只读不写** —— 体检出的是**建议**(补溯源 / consolidate / 清 stale / 裁决矛盾),**不自动删 stale、不自动改 confidence、不自动 consolidate**。改记忆库是人的活(对齐「未经验证不 memorize / invalidate 谨慎」)。比 onboarding/compare(它们 memorize)更保守:体检连记忆库都不动。

2. **体检默认不 memorize** —— 体检本身**不产生新知识**(只是把已有记忆摊开看),所以默认不记。**唯一例外**:体检中发现记忆库有**未决矛盾**(两条都 active + 都高 confidence + 结论冲突)→ 这是关于记忆库本身的新观察,可 memorize 一条 `codebase_fact`「记忆库存矛盾:A vs B」标**需人工裁决**(不确定谁对,只记录这里有冲突待裁)。除此之外不记。

## 核心难点:从 dump 读健康信号是语义判断(同 onboarding/compare 同构)

体检不是「列清单」就完 —— 要从 dump 的条目里**读出四类健康信号**,这是**语义判断**(无确定性工具能自动打健康分):
- **溯源弱**:高 confidence(≥0.7)但 `@无证据` 且无 `sha`(结论自信但追不到代码,该补锚点)。
- **待巩固**:低 confidence(<0.4)但 `hits` 高(被反复召回却不自信,可能该 consolidate 升级 mental_model)。
- **已过期**:`STALE`(invalid_at / superseded_by,占位但不该再用)。
- **未决矛盾**:两条都 active + 都高 conf + 结论冲突(记忆库自相打架,要人裁)。

`memory_dump` 只摊数据(图/字段驱动防幻觉),**读信号靠 agent**。同 [[onboarding-skill-handoff]]「挑哪条旅程是语义判断」、[[compare-skill-handoff]]「函数配对是语义判断」同构。

## 差异化卖点(不追平前沿)

2025-2026 治理型 agent memory 的关键维度 = **provenance(溯源)+ confidence(置信度)+ staleness(陈旧)+ audit trails**(Atlan/Mem0/OvalEdge/PMC-NIH 多源)。Hyperion 的 KnowledgeItem **天生带这套字段**([schema.py:127-166](../../src/hyperion/services/memory/schema.py#L127-L166):bi-temporal `valid_at`/`invalid_at` + `source_tier`/`evidence`/`commit_sha` + `access_count` + `superseded_by`)—— 功能 2 把这套字段最擅长的事(可审计知识库)做成可见。**Mem0/Cognee 没这种「带溯源的团队记忆体检」**(调研坐实)。本 skill 把 Hyperion 已有的审计字段用在对的地方,不追平(不自己造 governance 框架)。

## memory_dump 工具返回 shape(给后续维护)

```
Memory dump: N items (codebase=X[, kind=K][, +invalid]):
- [bug_lesson] <summary>  @<file:line>[; ...]  conf=0.90 tier=delegate  sha=abcdef12  2026-08-13  hits=3
- [codebase_fact] <summary>  @无证据  conf=0.20 tier=tool  2026-08-12
- [bug_lesson] <summary>  @<file:line>  conf=0.50 tier=inferred  2026-07-01  STALE(invalid 2026-08-01)
```
header 行:`Memory dump: N items (codebase=X[, kind=K][, +invalid])`。每行一张溯源卡(`@无证据` 标无 evidence 的条目 = 溯源弱信号;`STALE(...)` 标失效/被取代)。

## 复用工具(零改动)

`memory_dump`(新,#15)/ `memory_recall`(查特定主题补充,某条想看 detail/root_cause)/ `search_codebase`+`read`+`grep`+`glob`(必要时核 file:line 真存在,防「记忆指向不存在的代码」)/ `memory_memorize`(仅发现未决矛盾才记)。

## 故意不做(YAGNI)

- **不拆多工具**(按 kind / 按 stale / 按矛盾各一个)—— memory_dump 一次返全量 + kind/include_invalid 过滤就够,健康信号是 agent 读出来不是工具筛出来(踩坑#2)。
- **不内置健康评分 LLM** —— memory_dump 只摊带溯源数据(字段驱动),健康信号判断归 agent(对齐 onboarding/compare)。
- **不自动清 stale / 自动 consolidate** —— 体检是「看 + 建议」,改记忆库是人的活;只标建议不动手。
- **不强制体检必走 search_codebase 核 file:line** —— 大多数记忆指向真代码,核验是「必要时」(发现溯源弱才核),不每次全核(省 token)。
- **体检默认不 memorize** —— 体检不产新知识(只摊开已有记忆看);仅未决矛盾才记一条「需裁决」。

## 验证(绿)

| 测试 | 测了啥 | 期望 | 实际 |
|---|---|---|---|
| `test_memory_dump_empty` | 假 svc 返 []→空提示分支 | 无 Traceback + 回显 codebase + list_items 被调 + scope codebase 对 | ✅ |
| `test_memory_dump_renders_audit_cards` | 假 svc 注入 2 KI(高 conf 带证据/sha + 低 conf 无证据)→ 溯源卡渲染 | "2 items" + 两 summary + conf=0.90/0.20 + tier=delegate/tool + sha=abcdef12 + lib/sdp.c:1222 + @无证据 | ✅ |
| 全 mcp_tools 回归 | 25 旧 + 2 新 | 全绿 | ✅ 27 passed |
| lint | mcp_memory.py + test_mcp_tools.py | ruff 绿 | ✅ |

**不跑编译/真模型/opencode e2e**(用户自验铁律)。opencode e2e 真机跑是用户的事,本交接**不预填「全绿」** —— 待用户真机跑完再补。

## 待 commit 文件

- `src/hyperion/tools/mcp_memory.py`(+ helper `_render_audit_card` + 工具 #15 memory_dump)
- `.claude/skills/memory-health-check/SKILL.md`(新)
- `config/opencode_hyperion.json`(+ hyperion-memory-health agent block,经 symlink 自动到 opencode.json)
- `tests/test_mcp_tools.py`(+ 2 测 + `_FakeMemSvc.list_items`)
- `docs/设计/architecture-review-2026-08-12.md`(§六 功能2 `[ ]`→`[x]` + §4.2 「14→15 个」+ 记忆 2→3)
- `CLAUDE.md`(L38/L87/L99 tool count + L102 新 backlog 段)
- handoff memory + MEMORY.md

关联 [[onboarding-skill-handoff]](镜像对象:read-only + 语义判断难点 + e2e 待真机跑) [[compare-skill-handoff]](recall 短路 + 语义判断同构) [[memory-design-review-2026-08-12]](memory_recall 职责=翻长期记忆不混 code,memory_dump 是浏览/审计互补入口) [[pitfall-log]](#2 漏斗/薄工具不碎 / #16 只读 skill 要 bash:deny) [[skill-prompt-writing-style]] [[avoid-overengineering]](list_items 已是契约→0 服务代码,只差 MCP 薄封装)。
