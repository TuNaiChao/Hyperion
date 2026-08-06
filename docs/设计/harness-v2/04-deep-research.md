# 04 · 代码库深度调研扩能设计(harness 转向后)

> 新增:feature 2(2a 调用链 + 2b 跨版本)= **tool + skill**(跟 bug-RCA / patch 一致),不建 workflow。
> 老 `docs/设计/deep-research-design.md` 的 deep_research **workflow**(批量架构/模块调研报告)仍有效、保留;
> 本文档是 feature 2 的**交互式工具**设计(用户问"蓝牙连接流程涉及哪些函数" → 工具即时答)。

## 用户需求(原始 2 条)
- **2a** 先让 agent 分析 bluez 代码库,然后问"蓝牙连接的流程涉及哪些函数?" → 快速给出**函数调用链 + 说明**。
- **2b** 分析单个代码库的**不同分支/版本**(bluez 5.50 vs 5.85),分析具体问题(蓝牙连接)时**准确对比两版本差异**;某问题 5.50 存在、5.85 已解决 → 给相关代码作修改参考。

---

## 2a · 函数调用链(阶段 3)

### 现状(已有,但浅)
CRG([services/code_index/code_graph.py](../../../src/hyperion/services/code_index/code_graph.py))有 `callers_of`/`callees_of` **单跳**([structural.py:59-88](../../../src/hyperion/services/memory/backends/native/structural.py#L59));LSP `find_references`([tools/code_nav.py:389](../../../src/hyperion/tools/code_nav.py#L389))单符号精确引用。**缺**:多跳遍历、自然语言→入口符号、MCP 工具,且 structural 后端默认关(Noop)。

### 新件:`hyperion_call_chain` 工具 + research skill
1. **`hyperion_call_chain(seed, direction="down"|"up"|"both", depth=3) -> dict`**:`CodeGraph.open(repo)` 上做 **BFS/DFS 多跳遍历**(callers_of/callees_of 递归到 depth);返回调用链(节点 = 函数 + file:line + 社区;边 = 调用关系)+ 每节点简注。
2. **语义种子**(自然语言 → 入口符号):`seed` 是"蓝牙连接流程"这类自然语言时,先用 `search_codebase`(语义)+ LLM 锚定 → 解析出真实入口函数名(anti-hallucination:只给索引里真实存在的符号)→ 再走 call_chain。`seed` 是确切函数名则直接走。
3. **排序**(挑该展示的):PageRank/中心度(Aider repomap / LocAgent 式)排序,避免一条链几百个函数全塞给 agent —— 选 top-N 关键节点。
4. **`research` SKILL.md**(`.claude/skills/research/` ⏳):playbook = ① 语义找入口(search_codebase)→ ② `call_chain` 展链 → ③ 读关键节点精读(read/grep)→ ④ 给链 + 说明。opencode agent enforcement 待 2a 落地时加。
5. **structural 后端默认开**(或 call_chain 工具直接用 CodeGraph,绕过 structural 后端)—— 让 call-chain 开箱即用(现在 Noop 要 `--extra code-review-graph`)。

### 诚实边界
**没有现成索引器能解函数指针 / 回调 / 异步**(BlueZ/kernel 满地是 `file_operations`、D-Bus method table、`g_idle_add`、HCI event cb)。静态图断不掉这些边。务实配方:**CRG 单跳边(已有)→ PageRank/社区排序 → MCP 暴露 → 让 LLM 识别 handler-table 惯用法补间接调用缺口**。skill 里会注明"链是静态近似,函数指针/回调处需 agent 结合代码判断"。

---

## 2b · 跨版本差异(阶段 4,依赖 2a)

### 现状(基本没有,greenfield)
按仓名持久化 graph(`data/structgraph/<repo>/graph.db`)—— 机械上能建两个版本图(`bluez-5.50` / `bluez-5.85`)。但**无两版本对比 workflow / 原语**;`graph_diff.py`(snapshot/diff)在 backlog;`KnowledgeItem.commit_sha` 锚单 commit,无"某 fact 在版本 X 成立、对比版本 Y"概念。

### 用户场景拆解
"5.50 有 bug、5.85 修了 → 给 5.85 的修法代码作参考" = 反向 fixed-upstream 检测。**这是研究空白**(没人端到端做过;PatchSeeker/PortGPT/MigGPT 都是正向 CVE→commit 或已知 fix→backport)。

### 新件:`hyperion_cross_version_diff` 工具 + skill
1. **两版本索引**:`hyperion index` 两个 scope(`bluez-5.50`、`bluez-5.85`),各自 CodeGraph + code_index。
2. **`hyperion_cross_version_diff(repo_a, repo_b, concern) -> dict`**:
   - **符号对应**:同名匹配 + 嵌入相似(函数级)→ 建立 5.50↔5.85 符号映射。
   - **`git diff` 两版本相关符号**(concern 圈定,用 2a 的 call_chain 定位 concern 涉及的符号)→ 改了啥。
   - **LLM 判**:"concern 在新版本修了?修法是?" + 给代码片段作参考。
   - **确定性门**:`git patch-id` / `git cherry`(老版本里有没有等价 patch)—— 便宜精确,先过这道。
3. **`research` SKILL.md 扩**跨版本章节:① 2a 在旧版本定位 concern 符号 → ② 对应到新版本 → ③ `cross_version_diff` → ④ LLM 总结"修了?参考代码"。

### 诚实边界(研究级,MVP 确定性 + LLM 摘要先跑通)
- 语义等价(同名改了实现 / 不同名但同 fix)难判;MVP 先确定性(`git patch-id`)+ 函数级 `git diff` + LLM 摘要;**语义等价 critic 留迭代**。
- 版本锚点:`KnowledgeItem` 加版本维度(或两 scope 隔离站得住)—— 设计待 C0 细化。
- 这是 4 个阶段里最难的(greenfield + 研究级);MVP 先跑通"两版本 + concern → diff + LLM 摘要",再迭代到语义等价。

---

## 依赖与顺序
- **2a 是 2b 的地基**(2b 要先在一个版本里用 2a 定位 concern 符号)。
- 阶段 3(2a)→ 阶段 4(2b)。
- 都经 MCP 暴露(同一 server),skill 走 `.claude/skills/`(跨平台)。

## 前沿依据(2025-2026,详见 harness-pivot-handoff / 调研记忆)
- 调用链:RepoGraph(ICLR25 line-level graph)/ LocAgent(ACL25 异质图)/ Codebase-Memory / codebadger(Joern CPG as MCP)/ Aider repomap(PageRank)—— 都是 **tool-server 给 agent 调**。Hyperion 的 CRG 单跳已有,补多跳 + 语义种子 + MCP 即对齐主流。
- 跨版本:PatchSeeker(arXiv:2509.07540,CVE→commit)/ PortGPT(2510.22396 backport)/ MigGPT(NeurIPS25 内核跨版本迁移)/ `git patch-id` 确定性门。反向"我这 bug 上游修没"是 gap —— Hyperion 补这个 composition。
