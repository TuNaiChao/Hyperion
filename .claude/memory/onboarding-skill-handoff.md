---
name: onboarding-skill-handoff
description: "2026-08-13 单 codebase 架构导览 onboarding skill 落地 —— 1 skill + 1 agent block + 第 14 MCP 工具 repo_overview(聚合 architecture_overview/communities/hub_nodes/bridge_nodes)。spec-drift 修正:原规范「0 新工具」错(列的 3 个名字是 CodeGraph 方法不是 MCP 工具)。镜像 compare:recall-first 短路 + memorize 读码即记。方法论取 theroadtoenterprise 2026-05 六阶段 onboarding。opencode e2e 待真机跑。"
metadata:
  node_type: memory
  type: project
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-08-13T00:00:00.000Z
---

**2026-08-13 onboarding skill 落地**(architecture-review §六 功能1):1 个 `onboarding` skill + 1 个 opencode agent block(`hyperion-onboarding`)+ **第 14 个 MCP 工具 `repo_overview`**。填现有矩阵唯一空白 —— 5 个旧 skill(backport/bug-rca/patch-review/upstream-merge/compare)全 bug/补丁/对比导向,**没有「给新人讲清单仓架构」纯调研型**。

## spec-drift 修正(关键,本会话查出 + 用户拍板)

architecture-review §六 功能1 原写「**0 新工具**,串 `repo_map` + `architecture_overview`/`hub_nodes`/`communities`」—— 但**这三个名字是 CodeGraph 已实现的方法**([code_graph.py:568-593](../../src/hyperion/services/code_index/code_graph.py#L568-L593)),**不是 MCP 工具**(当时只 13 工具,无一含这些)。规范作者把「底层方法」误当「现成工具」了。「0 新工具」前提错。用户拍板:**加第 14 个薄工具 `repo_overview`** wrap 这三个 + `bridge_nodes`(同 analysis.py 家族,~0 额外代码)。

**为什么这是生产级正确划分而非图省事**:onboarding 是**第一个真需「模块/耦合」视角的 skill** —— bug-RCA/compare 要的是具体调用链(手电筒照一条路),不是模块布局(卫星图)。`repo_map` 给「最重要的函数」(符号层),`repo_overview` 给「模块边界+枢纽+瓶颈+耦合告警」(架构层),**分层检索**:两个维度互补不打架。2026 前沿(theroadtoenterprise.com「Onboarding to a New Codebase with AI Tools in 2026」,2026-05)六阶段循环 phase1 明确「先看项目形状(结构图/社区)再读码」—— 这正是 `repo_overview`+`repo_map` 的活。

## 做了啥

- **`src/hyperion/tools/mcp_memory.py`** 加工具 #14 `repo_overview`(插在 `repo_map` 之后,镜像 repo_map 6 pattern:import-guard / `CodeGraph.open` / FileNotFoundError→「图未建」+ `uv run hyperion index` 提示 / `body[:8000]` / header 计数 / per-call codebase)。**聚合四方法一次返**:`architecture_overview()`(返 `{communities, cross_community_edges, warnings}`,warnings 是 `list[str]`)+ `hub_nodes(top_n)` + `bridge_nodes(top_n)`;`communities` 复用 `arch["communities"]` 省一次调用(architecture_overview 内部已调 get_communities)。纯图查询无 LLM,**图驱动防幻觉**(讲「这仓分几大模块」靠社区检测不靠模型编)。
- **`.claude/skills/onboarding/SKILL.md`**(新):镜像 compare 只读调研型骨架。`allowed-tools` 加 `hyperion_repo_overview`;单仓不带 `cross_version_diff`/`blast_radius`;无 bash(read-only,踩坑#16)。
- **`config/opencode_hyperion.json`** 加 `hyperion-onboarding` agent block(mode primary / **steps 24** / permission **read-only**:edit deny + bash deny + read/grep/glob/list/hyperion*/skill allow)。**opencode.json 是 symlink → 模板**(见 [[opencode-config-drift]]),改模板一处即生效。
- 2 单测 + 全 mcp_tools 25 绿 + lint 绿。

## 核心设计:1 skill,1 薄工具,7 步 recall-first 短路

**形态** = 镜像 compare(只读调研型,不改代码;memorize 读码即记不等用户验证)。

**7 步流程**(2026 六阶段映射 Hyperion 工具,recall-first 短路**镜像 compare**,见 [[compare-skill-handoff]] 的「recall 命中就短路」gotcha):
1. 确认 codebase + 导览主题 + **recall 探底**(第一步必 `memory_recall(query=概念, codebase=该)`)。
2. **短路 vs 重跑分流**:recall 命中同 codebase + 同主题 + file:line 齐 → 复用直接出导览卡(step 5/6),**不重跑 repo_overview/read**;没命中/主题对不上/缺关键模块才走完整。
3. 俯瞰架构【阶段1·结构快照,仅重跑】:`repo_overview`(社区/hub/bridge/耦合告警)+ `repo_map`(PageRank 符号俯瞰)。
4. 挑一条旅程 + 端到端走【阶段4·核心,仅重跑】:默认 hub_nodes 排第一(全仓被依赖最多的入口)/ 用户指定就 `search_codebase` 定位 → `call_chain` 多跳展开 → **逐节点 `read` 函数体**(顺手记命名/错误处理/日志 conventions,每条带 file:line)。
5. 聚导览级结论【短路也走】:为什么这么分模块 / 为什么这个入口是核心 / 主旅程怎么走 / 架构风险点 / 新人先读哪几个文件。
6. `export_report` 落导览报告(每条结论附 file:line 防幻觉)。
7. memorize(仅重跑才记,kind=codebase_fact,kind_detail=architecture)。

**核心难点 = 挑哪条旅程是语义判断**(无确定性工具能挑最有代表性的旅程)—— 默认 hub_nodes 排第一 + 用户指定优先,忌挑边缘函数误导新人。同 [[compare-skill-handoff]]「函数配对是语义判断」、[[backport-workflow-handoff]]「判 bug 是语义判断」同构。

## memorize 读码即记 —— 同 compare,区别于 bug/补丁型

backport/bug-rca/patch-review/upstream-merge 守「未经验证不 memorize」(涉及 bug/补丁是否真修对,要等用户真机)。**onboarding 是纯读码事实**(架构观察不依赖编译/真机),读完即记 → 下次问同类问题 `memory_recall` 命中 = 「秒答」。同 [[compare-skill-handoff]]。

## repo_overview 工具返回 shape(给后续维护)

```python
{"codebase": <str>, "communities": <list[{id,name,members,cohesion}]>,
 "hub_nodes": <list[{name,qualified_name,kind,file,in_degree,out_degree,total_degree,community_id}]>,
 "bridge_nodes": <list[{...,betweenness,community_id}]>,
 "cross_community_edges": <list[{source_community,target_community,edge_kind,source,target}]>,
 "warnings": <list[str]>  # 高耦合(>10边)社区对,如 "High coupling (12 edges) between 'core' and 'util'"
}
```
header 行:`repo-overview(codebase=X, top_n=N): C communities / H hubs / B bridges [/ W 高耦合告警]`。

## 复用工具(零改动)

`repo_overview`(新,#14)/ `repo_map`(PageRank 俯瞰)/ `search_codebase`(用户指定主题时定位旅程入口)/ `call_chain`(旅程多跳展开)/ `read`·`grep`·`glob`(逐节点读函数体)/ `memory_recall`(命中短路)/ `memory_memorize`(读码即记仅重跑)/ `export_report`/ `ensure_repo`。

## 故意不做(YAGNI)

- **不拆 4 个独立 MCP 工具**(architecture_overview/communities/hub_nodes/bridge_nodes 各一个)—— onboarding 每次要全要,拆 4 个逼 agent 调 4 次(踩坑#2:薄工具别碎);聚合一个一次拿全。
- **不内置 LLM 讲解** —— repo_overview 只返结构数据(图驱动防幻觉),讲解归 opencode agent 读码推理(对齐 compare)。
- **不做 cross-version onboarding** —— 那是 compare 的活;onboarding 纯单仓。
- **不强制 trace 旅程的确定性工具** —— 挑旅程是语义判断,默认 hub 排第一 + 用户覆盖。

## 验证(绿)

| 测试 | 测了啥 | 期望 | 实际 |
|---|---|---|---|
| `test_repo_overview_not_built` | 图未建→工具不抛 traceback | 无 Traceback + 命中「未建/不可用/失败」之一 | ✅ |
| `test_repo_overview_success_via_fake_graph` | 假图三方法返固定 dict→header+body+top_n 透传 | "2 communities / 1 hubs / 1 bridges / 1 高耦合告警" + 含 hub 名 + top_n==8 | ✅ |
| 全 mcp_tools 回归 | 23 旧 + 2 新 | 全绿 | ✅ 25 passed |
| lint | mcp_memory.py + test_mcp_tools.py | ruff 绿 | ✅ |

**不跑编译/真模型/opencode e2e**(用户自验铁律)。opencode e2e 真机跑是用户的事(同 compare 落地后由用户跑蓝连 e2e),本交接**不预填「全绿」** —— 待用户真机跑完再补。

## 待 commit 文件

- `src/hyperion/tools/mcp_memory.py`(+ 工具 #14 repo_overview)
- `.claude/skills/onboarding/SKILL.md`(新)
- `config/opencode_hyperion.json`(+ hyperion-onboarding agent block,经 symlink 自动到 opencode.json)
- `tests/test_mcp_tools.py`(+ 2 测)
- `docs/设计/architecture-review-2026-08-12.md`(§六 功能1 `[ ]`→`[x]` + spec-drift 修正 + §4.2 「13→14 个」)
- `CLAUDE.md`(L38/L87/L99 tool count + L102 新 backlog 段)
- handoff memory + MEMORY.md

关联 [[compare-skill-handoff]](镜像对象:recall 短路 + 读码即记 + gotcha) [[opencode-config-drift]](symlink 模板) [[opencode-mcp-wiring]](真机 e2e 待跑的环境坑先例) [[pitfall-log]](#2 漏斗 / #16 只读 skill 要 bash:deny) [[skill-prompt-writing-style]] [[avoid-overengineering]](spec-drift 下坚持加工具是对的不将就 0 工具)。
