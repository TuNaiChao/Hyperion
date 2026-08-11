---
name: route3-cross-version-handoff
description: "2026-08-11 路线 #3(feature 2b cross_version_diff)完成 —— 同仓两 git ref 跨版本对比(git 为核 + 图可选富化);第 11 个 MCP 工具。"
metadata:
  node_type: memory
  type: project
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-08-11T04:10:00.000Z
---

**2026-08-11 路线 #3「feature 2b 跨版本对比 `cross_version_diff`」完成。** 多库(#1)+ call_chain(#2)已收尾,这是「代码情报」的第 11 个工具,补上**版本时间轴**维度(blast_radius=空间波及 / call_chain=调用链 / cross_version_diff=版本时间,三者按维度互补)。

**需求来源**:`docs/设计/harness-v2/04-deep-research.md` §2b —— 同仓两版本(bluez 5.50 vs 5.85),回答「concern 旧版本存在、新版本修了没?修法?给参考代码」(对标 PatchSeeker/PortGPT;spec 点名确定性门 `git patch-id`/`git cherry`)。

**新增**:
- `cross_version_diff(base_ref, head_ref, *, repo_path, concern_files=None, concern_symbols=None, graph=None, top_commits=30, max_diff_chars=8000)`(`code_graph.py` **模块级函数**,非 CodeGraph 方法 —— 因 git 核不需图实例):git 编排核心 + 可选 graph 富化。产出 `{refs, commits, commits_truncated, patch_equivalence, concern_diff, touched_functions, note}`。
  - **git 核(不需 CRG/图也能跑)**:rev-parse 验 ref→sha;`git log base..head [-- concern]` 提交门(确定性门,MVP 用 log,逐 commit patch-id 留迭代);`git cherry base head` 等价摘要;`git diff base..head -- concern` 文本给 agent 读修法。
  - **图富化(可选)**:`_resolve_concern_files`(concern_symbols→file,复用 call_chain 同款符号解析)+ CRG `parse_git_diff_ranges`+`map_changes_to_nodes`(base..head diff→触及函数,图须=head 版本才行号对齐)。
  - **安全**:`_SAFE_GIT_REF` regex 防 ref 注入(抄 CRG changes.py);subprocess 列表参数**无 shell**;timeout;cwd=repo_path。失败全转 `ValueError`(工具层兜底友好串,绝不漏 traceback)。
- `_resolve_concern_files(graph, symbols)`(模块级 helper,一次建图多符号共用)。
- `cross_version_diff` MCP 工具(`mcp_memory.py`,第 11 个):壳镜像 call_chain + `repo_path` 必填(同 validate_patch)+ 图可选(`CodeGraph.open` try/except→None,git 核照跑)+ per-call `codebase`(🔀,第 6 个支持的工具)。

**对 spec 的 3 处有意偏离(pivot 后修正)**:
1. **不内置 LLM**(spec §2b 要「LLM 判修没修」)→ 工具出确定性事实,判断归 agent(pivot + 2026-08-10 工具审核判据:灵活类撤、硬门/差异化留)。
2. **单仓两 ref 非 `repo_a/repo_b`**(spec 签名)→ 主用例(bluez 5.50 vs 5.85)= 一仓两 git ref,共享 object store,`git diff/log/cherry` 一条命令;两独立 clone + 嵌入对应 = spec 明确推迟的「语义等价」→ backlog。
3. **git 为核图可选**(spec 偏「两版本索引 + 符号对应」)→ 跨版本对比正主是 git(结构化、零依赖、确定性);图只做可选富化 → **没图也能跑**,降级比 blast_radius/call_chain 强。调研背书:difftastic/SemanticDiff/GumTree 是**查看器**(产 AST 可视化,不做函数级分类、不打补丁),对「给 agent 喂事实」不如 git 直接;`git cherry`/patch-id 是「等价补丁」经典门(Halvar Flake / differential 模式)。

**关键复用(没造轮子)**:CRG `parse_git_diff_ranges`+`_parse_unified_diff`+`map_changes_to_nodes`(全 `code_review_graph.changes` 模块级公开函数,lazy import)+ `_SAFE_GIT_REF` + 2a 的 `_store._batch_get_nodes`。**CRG 缺口由 git 核绕开**:无 `build_at_ref`(eval 里 `git checkout`+full_build 脏工作树,不可用于工具)/ `analyze_changes` 单图单侧 / `graph_diff.py` 休眠+太粗(无 line/hash 字段,零调用)—— 都不需要,head 图(可选)+ git diff 即可。

**验证**:ruff 干净 + pytest **24 测绿**(1 deselected = kind_filter 真网络)。`test_cross_version_diff_small_git_repo` 正向:tmp git 仓两 commit → refs/commits/concern_diff/patch_equivalence 全断言 + CRG 在本机装了 → `CodeGraph.build` + touched_functions 富化实跑通;`test_cross_version_diff_bad_ref`(regex 拒 `;`,hermetic 无需 git)+ `test_cross_version_diff_not_a_repo`(非 git 仓 → rev-parse 失败)→ 均友好串无 traceback。

**Why / How to apply:** 跨版本对比 = bug-RCA「向上游找已知修法」高价值视图,纯 git + 可选静态正合「不编译/不复现」方针。agent 用法:search_codebase→call_chain 定位 concern 符号 → 传 `cross_version_diff(base=旧 ref, head=新 ref, concern_symbols=...)` → 拿提交门 + concern diff + 触及函数,自己判「修没修/怎么移植」(工具不出 LLM 判断)。下一步:路线 #4(记忆自动 query P1)或 backlog(两独立 clone 对应 + 嵌入 / 逐 commit `patch-id` 全等价矩阵 / 函数级 add-remove-modify 精分类 + rename 检测,需两图 diff)。关联 [[route2-call-chain-handoff]]、[[multi-codebase-per-call-handoff]]、[[toolset-after-audit-2026-08-10]]、[[harness-route-review-2026-08-07]]。
