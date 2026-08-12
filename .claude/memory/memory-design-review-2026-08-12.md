---
name: memory-design-review-2026-08-12
description: "2026-08-12 记忆设计复核 —— recall N=2 不正向触发的复核:存储层不过度留、检索层一处真 bug(memory_recall 混 code 越界)、注入层主路径已对;voxos/mem0/arXiv 背书;A+B 落地,C 并入 A 不立项"
metadata: 
  node_type: memory
  type: project
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-08-12T02:32:41.208Z
---

**2026-08-12 用户要求(recall N=2 见 [[recall-validation-handoff]] 效果不明显)→ 复核记忆设计是否过度 + 更好设计 + 2025-2026 最佳实践。结论:没整体过度,一处真 bug + 价值命题错配;A+B 落地,C 并入 A 不立项,D 不做。**

## 复核三层判定

| 层 | 判定 | 理由 |
|---|---|---|
| **① 存储层**(SQLite+FTS5+向量 native 后端 + `(owner,codebase)` 分区 + 溯源 + 只追加写入对标 mem0 v3) | ✅ **不过度,全留** | 标准做法(Mem0/cognee 同套路);分区+溯源+持续学习是 Hyperion 真差异化,简化会丢灵魂 |
| **② 检索层**(`recall()` 4 路 RRF+rerank+decay) | ⚠️ **函数本身不砍,但 `memory_recall` 工具调错了它** | 详见下方"真 bug"——是接线错位(踩坑#2 变体),不是算法过度 |
| **③ 注入层**(主路径 opencode+MCP on-demand;reference path orchestrator 预注入) | ✅ **已对,C 不立项** | 主路径 on-demand = mem0 gating layer 理想形状;reference path 预注入节点已调 `svc.search()` memory-only(C 原担忧不存在) |

## 真正的 bug(核代码后发现,不是原诊断的"算法过度")

核实 [service.py:122](../../src/hyperion/services/memory/backends/native/service.py#L122) 有**两个方法**:
- `svc.recall()`(L123)→ `_recall(code_bundle=..., structural=...)` **记忆+代码混合检索**
- `svc.search()`(L134)→ `_recall(code_bundle=None, structural=None)` **memory-only**(注释明写"不混 code/structural")

接线现状:
- **orchestrator 预注入节点** [bug_rca/nodes.py:115,151](../../src/hyperion/workflows/bug_rca/nodes.py#L115) → 调 `svc.search()` ✅ memory-only(早做对了,C 担忧不存在)
- **`memory_recall` MCP 工具** [mcp_memory.py:121](../../src/hyperion/tools/mcp_memory.py#L121) → 调 `svc.recall()` ❌ **混了 code chunk**
- **CLI `recall`** [cli.py:225](../../src/hyperion/cli.py#L225) → 调 `svc.recall()`(调试用全量检索,可接受)

**问题**:`memory_recall` 的 docstring 自称"翻长期记忆:historical bug lessons / codebase facts"、"reuse prior root-causes/fixes",但它调混合检索 → 把 code chunk 当记忆返给 agent。agent 另有 `search_codebase` 专查代码 → **职责重叠(踩坑#2 变体)+ 与自身文档矛盾**。这是 recall N=2"信号被吃/噪声"的一个具体来源:工具返了不该返的代码 chunk,稀释了真记忆的信号。

## 研究背书(2025-2026)

- **voxos.ai「纯文本文件胜过 RAG」**(60k+ 项目):三条硬数据①"lost in the middle"(斯坦福 2023)长上下文中段信息掉 15-30% 性能 → 预注入先验塞进 delegate 长 prompt 中段被注意力稀释,机制性解释 N=2 信号没兑现;②指令天花板 ~150-200(HumanLayer),质量随条数**均匀**下降;③Letta 纯文件 74% > Mem0 图变体 68.5%(LoCoMo),"30 条精炼 > 300 条堆砌"。**反方观点,不能照搬**(丢结构化/分区/溯源 = 丢 Hyperion 灵魂),但"小规模精炼 > 复杂管线"是共识。
- **Mem0「coding agent 记忆」+ 「proactive memory gating」**:schema 前置(✅)+ 保守写入(✅ only-append)+ **memory bloat 拉低检索质量**(支持砍 memory_recall 越界返代码)+ **proactive injection 必须有 gating layer**(支持主路径 on-demand)+ 记忆要**和现读源码结合、鼓励 agent 在当前代码验证假设**(delegate 已这么做,recall 角色本非"塞答案")。
- **arXiv 2607.08716(Meta Proactive Memory Agent)**:ablation proactive > 被动 > 通用检索,**但前提是长程任务**。bug-RCA 定位是聚焦 1-2 轮任务,proactive 受益条件不成立 → 与 N=2 自洽。
- **共识**:proactive 要 gating(按需才注入)否则 context bloat;纯 markdown 小规模赢、大规模+多 agent 共享结构化才值。Hyperion 是后者 → 留结构化存储,砍过度接线,不改注入。

## 四档建议(执行口径)

**A. 改价值命题(零代码,已落)**:recall 成功指标**不是 step-count/定位轮数**(N=2 证伪),是定性「防重复误诊(踩坑#11 系统性误诊,一条红鲱鱼教训拦再犯)+ 带架构事实 + 团队共享教训」。落 [[recall-validation-handoff]] + MEMORY.md + todo.md。

**B. 砍 `memory_recall` 越界返代码(小改)**:`memory_recall` 工具 `svc.recall()` → `svc.search()`(memory-only),职责干净化;其余不动。`recall()` 多路函数保留(向后兼容 + 将来"统一记忆+代码检索"可用)+ decay 保留(防陈旧正确,空仓 age≈0 不生效非设计问题)。

**C. 注入方式 —— 并入 A,不立项**:主路径(opencode+MCP)代码层已 on-demand(mem0 gating 形状)无需改;reference path 预注入节点已 `svc.search()` memory-only(C 原担忧不存在)。C 唯一"动作"= 两条认知(别往主路径加预注入 + 别据 reference path step-count 评 recall),写进 A 的价值命题即可,单独立项是空动作。

**D. 明确不做(防过度反应)**:❌ 不抄 markdown-only(丢灵魂);❌ 不砍 recall() 4 路算法(过度诊断);❌ 不为"提速"重跑 N=3-5(指标错配,跑再多不稳)。

## 教训

- **复核要先核代码再下"算法过度"结论**:原诊断猜"砍 recall.py 4 路",核代码发现是多路函数没错 + `search()` 已 memory-only,真问题是 `memory_recall` 工具调错函数。**接线错位 ≠ 算法过度**,修法差很多(改一行调用 vs 重写检索栈)。同 [[avoid-overengineering]] 但别过度诊断过度设计。
- **"效果不明显"先查职责越界**:N=2 recall 信号被吃,根因之一是 memory_recall 返了 code chunk 稀释记忆信号 —— 踩坑#2(职责重叠)的隐蔽变体。查"这个工具/节点该不该返这个"比"算法够不够强"更先。

关联 [[recall-validation-handoff]] [[similar-bug-recall-roadmap]] [[avoid-overengineering]] [[pitfall-log]](#2 漏斗/#11 误诊) [[delegate-already-localizes]] [[memory-append-only-directive]]。
