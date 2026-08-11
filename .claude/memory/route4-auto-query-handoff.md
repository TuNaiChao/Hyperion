---
name: route4-auto-query-handoff
description: 路线 #4 记忆自动 query P1 —— 定位后用 problem_summary 召回历史修法(A1 日志摘要被探针证伪→转 B)
metadata:
  type: project
---

**2026-08-11 路线 #4「记忆自动 query (P1)」完成**(代码完未 commit)。补 R3 收尾 ②[b] 的 P0(`recall_lessons` 用 trigger 预注入)之外**最后一个洞**:纯日志驱动(`--trigger` 省略)时,定位阶段无任何历史教训先验 —— **更关键的是修复阶段完全没召回**(存储用 problem_summary/root_cause,检索却只喂 trigger,store/retrieve 不对称)。

## 关键决策:A1(日志确定性摘要)被探针证伪 → 转 B(problem_summary 召回)

**A1 `_log_digest` 被废弃**:原计划无 trigger 时从日志抽「错误关键词+函数名」当 recall query。探针实证**行不通** —— 真 demo2 journalctl 日志 tail 32KB 全是音频噪声,全扫则 X11 `BadWindow` 噪声占前 1500 字符,真 wpa abort 信号被淹没。**确定性日志切片无法区分相关错误与无关错误**,这正是 2026-08-10 砍 `filter_logs` 的同一根因(踩坑 #11)。**别再在 Hyperion 里做日志切片工具。**

**B(采用)**:在定位阶段① 之后、修复阶段② 之前,加确定性 `recall_for_repair` 节点,拿 delegate 定位出的 `problem_summary`(LLM 产,质量高、零额外 LLM cost)当 query 召回历史修法,预进修复 prompt。**零日志切片、零额外 LLM 调用**,直接对标 OM-RAG「query=bug 描述本身」。这关掉的是**真故障**(修复阶段从不召回),不是 A1 猜的「定位阶段 trigger 空」(后者影响小,P0 已用 trigger 兜)。

## 调研背书

- **OM-RAG**(arXiv 2607.21911v1,2026,WebFetch 核验):① query = 新 issue 的 title+body 原文 embed,**不经 LLM 改写**;② flat 结构化检索完胜 chunk(+186%)与 graph(+77%);③ 无检索诊断准确率 **0.238** vs 结构化检索 **0.931**(+291%)—— 检索**必需非可选**;④ 「retrieved but wrong」= source mismatch + structure mismatch。Hyperion 记忆已是结构化 BugLesson + flat(FTS5+向量+RRF),✅ 架构对齐。B 的 query(problem_summary)= OM-RAG 的 title+body 同位物。
- **deer-flow**:记忆层 chat/research-planner oriented,锁定「记忆自建不抄 deer-flow」,本特性 N/A。
- **踩坑 #11 防护**:注入先验段硬带「**这是先验/参考,不是答案** —— 与根因/证据矛盾时以根因为准,别照抄历史补丁」framing(复用 P0 localize prompt 同款安全 nudge),防 glm-5.2 被先验误导旧病复发。

## 改动(B)

| 文件 | 改动 |
|---|---|
| `workflows/bug_rca/state.py` | 加 `recalled_repair_lessons_ctx: str` 字段 |
| `workflows/bug_rca/nodes.py` | 新 `node_recall_for_repair`(query = `problem_summary` → 退 `root_cause` → 退 `trigger` → 无则跳过);改 `node_assemble_repair` 在「你是 C/系统软件」前 prepend「历史同类 bug 的修法」先验段(非空才插) |
| `workflows/bug_rca/graph.py` | 加 `recall_for_repair` 节点 + 边 `delegate_localize_loop → recall_for_repair → assemble_repair`;6 节点线性 → 7 节点 |
| `tests/workflows/bug_rca/test_recall_preinject.py` | `_FakeSvc.search` 捕获 `last_query`/`search_called`;+4 recall_for_repair 测(problem_summary 当 query / 退 root_cause / 无线索跳过 / 异常降级)+2 assemble_repair 先验段测 |

graph 节点链:`ingest → recall_lessons → delegate_localize_loop → recall_for_repair → assemble_repair → delegate_repair_loop → report_memorize`。

## ⚠️ 参考实现,非主路径

`graph.py` docstring 明示:post-pivot(2026-08-06)这条 7 节点 StateGraph 是**参考实现,不再是主路径**。bug-RCA 主路径 = **opencode + bug-rca skill(.claude/skills/bug-rca/SKILL.md)+ hyperion MCP 工具**,agent 自驱能自纠。本 workflow 保留因 verify-refine 收敛 / 报告渲染 / 结构化 memorize 的逻辑值得日后抢救;**不要**把它暴露成 MCP 工具。P0 `recall_lessons` 是 pivot 当天加的,B 顺着同一条线做,**与近期团队意图一致**。

## 验证(全绿,hermetic)

- ruff 4 文件干净;
- `test_recall_preinject.py` 13 测全绿(5 原有 recall_lessons + 4 recall_for_repair + 2 assemble_repair 先验 + 2 localize 先验);
- `tests/workflows/bug_rca/` 33 测全绿(report_memorize / verify_refine_loop 无回归);
- `build_graph()` 编译 OK,7 节点顺序正确。
- mock `get_memory_service`(假 svc.search 返脚本 RecallHit),不碰真 DashScope 网络/库。

## Backlog(记入,不本轮做)

1. ~~A1 `_log_digest`~~ —— **已废弃**(探针证伪,同 filter_logs)。
2. **trigger 有值时把日志也折进 query**(OM-RAG title+body 同检索)—— 先不碰绿路,YAGNI。
3. **LLM symptom 合成**(digest 信号不足时上)—— A1 的子项,A1 废后降级,B 的 problem_summary 已是高质量 LLM 产 query,基本顶替。
4. recall_for_repair 在**主路径(opencode skill)**里的对等物 —— skill 已有 `memory_recall` MCP 工具,agent 在 repair 前自觉调即可;是否要确定性注入待 e2e 观察(踩坑 #12:工具箱+人在环,不强制管线)。

下一步:**commit route #4 B**(显式路径,不含 Python语法.md/todo.md;push 单独确认),然后路线 #4 收尾。CLAUDE.md 核心顺序 #1-#4 全部落地(多库 / 2a call_chain / 2b cross_version_diff / 记忆自动 query P1)。
