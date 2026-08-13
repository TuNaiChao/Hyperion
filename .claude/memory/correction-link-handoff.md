---
name: correction-link-handoff
description: 补「纠正关系」闭环 —— corrects(正向,transit)+ corrected_by(反向,持久化+检索降权);体检 skill 闭环已解矛盾。
metadata:
  type: project
---

# 补「纠正关系」闭环:corrects 纠正链(2026-08-13 落地)

## 触发场景(为什么做)

memory-health-check e2e 真机发现:wpa 的 P2P scan leak bug 存**两派打架根因** ——
- A派(abort-failure,旧/**错**)4 条:summary 「P2P scan 后 abort 处理失败导致 leak」
- B派(scan-only 覆盖竞态,新/**对**)3 条:summary 明写「纠正先前 abort-failure 误诊,实为 scan-only 覆盖竞态」

B派显式说要纠正 A派,但 A派仍 `active=True` + 高 confidence + `superseded_by=None` + `corrected_by=None` —— **没有任何字段记录纠正关系**。风险:下次 recall 可能 surface 错根因(两派 confidence/decay 接近),patch 打到错 fix-point。

## 根本原因(为什么之前没这能力)

2026-08-06「只追加」指令(对标 mem0 v3)删了写时 supersede 循环(`_merge_or_supersede` → `_merge_on_remention`):冲突时新旧都 active 并存,靠 recall decay 排「最新为主」。但**纠正 ≠ 事实演化**:
- 事实演化(住北京→搬上海):append-only 正确,两条都可能是「当时为真」
- 纠正(根因 A 从来就错,B 推翻 A):A 需要被标记纠正,否则可能误导

只追加原则没问题,**缺的是「纠正」这一独立维度**。

## 设计:双字段(正向 transit + 反向持久化)

| 字段 | 位置 | 含义 | 持久化? | 谁设 |
|---|---|---|---|---|
| `corrects: list[str]` | 新 KI(corrector) | 「我纠正了哪些旧条目」(正向链) | ❌ transit,写入时消费掉 | agent 显式声明 → memorize 抽取 |
| `corrected_by: str \| None` | 旧 KI(corrected) | 「我被哪条纠正了」(反向链) | ✅ SQLite 列 | memorize 写新条时自动回填到旧条 |

### 为什么不复用 `superseded_by`?

`superseded_by` 与 `active` 属性(`invalid_at is None and superseded_by is None`)和 `list_items` WHERE(`invalid_at IS NULL AND superseded_by IS NULL`)绑定。设了它的条目会从 active 视图消失(体检默认看不到)—— **太激进:纠正 ≠ 失效**。被纠正的条目仍可引用(审计/参考),只是降权。独立 `corrected_by`:
- `active` 不受影响(纠正 ≠ 失效)
- `list_items` 默认仍可见(体检能看)
- 检索时额外降权(仍可召回但排后面)
- 只追加原则 100% 兼容(旧条物理不变,只加反向标记)

### 为什么 `corrects` 不入库?

`corrects` 是写入时指令(新条说「我纠正了谁」),`memorize_items` 消费它回填旧条的 `corrected_by`,**消费完就完了**。查询/检索/体检读的是旧条上的 `corrected_by`(反向链)。正链不入库省一列,且避免双写不一致。

## 检索侧降权(recall.py)

`CORRECTED_PENALTY = 0.3` —— `_apply_decay_confidence` 里,`corrected_by` 非空条目 `weight *= 0.3`(在 decay + confidence 加权之后)。被纠正条目仍可召回(参考价值),但分数砍到 30%,自然排到纠正者后面。RecallHit 透传 `corrected_by`,`render()` 加 `"(已被纠正)"` 标记(对称 `"(旧版本)"`)。

## 写侧抽取(memorize.py)

`memorize_items` 循环体,`_merge_on_remention` 之后、`to_upsert.append` 之前:检查 `merged.corrects` 非空 → 对每个目标 id 调 `store.mark_corrected(target, corrected_by=merged.id)`。失败只 warning 不阻断(目标可能不存在)。

`store.mark_corrected`(新方法,镜像 `set_invalid` 但不设 `invalid_at`):一条 UPDATE,WHERE 加 `corrected_by IS NULL`(幂等:已设过不覆盖)。

## memory_memorize 工具加 corrects 参数

`memory_memorize(..., corrects: list[str] | None = None)`:agent 声明「这条纠正了哪些旧条目」(传旧条 id 列表)。工具去重填入 KI,返回串带 `corrects=N`。`_render_audit_card` 加 `CORRECTED(by xxxxxxxx)` 标记(在 STALE 之后)。

## memory-health-check skill 升级:闭环已解矛盾

step 3「未决矛盾」升级:**先判能不能闭环** —— 发现矛盾后,先读双方 summary/detail 找有没有一方**显式说「纠正/推翻/更正」另一方**。
- 能判定谁对(corrector 明确)→ **不记「需裁决」**,调 `memory_memorize(kind=codebase_fact, corrects=[旧id], ...)` 写纠正关系(旧条自动标 `corrected_by` + 检索降权)。
- 真正无法裁定 → 留「需裁决」(现状不变)。

这样体检能把**已解矛盾**闭环掉(标 corrects),只留真正**未解**的「待裁决」。

## 调研坐实(2025-2026 前沿)

- **Vectorize 四杠杆框架**(Hindsight 博客 2026-05-21):「recency-wins with **explicit invalidation** is the most defensible default... marks the old one invalid **rather than deleting it**. Old state is recoverable for audit; current state is unambiguous for retrieval.」—— 旧条保留(审计可追溯),标纠正关系让检索降权,正是本设计。
- **Graphiti/Zep**(生产级 decay 最强):边级失效(`valid_at`/`expired_at`/`invalid_at`)。新事实显式标 supersede 边指向旧事实。Hyperion 的 `superseded_by` 就是这骨架,只追加指令时把自动设它的循环删了。
- **mem0 v3**:纯追加写入 + 检索时推理。Hyperion 只追加指令对标它,但 mem0 v3 有 contradiction handling(论文 §3.4),我们缺的正是这一块 —— 本设计补上。
- **deer-flow**:correction 在**对话层**(`detect_correction` regex 匹配 "that's wrong"/"不对"),不是 KB 条目间纠正链。CHANGELOG #3592「Guaranteed injection of correction facts」坐实「纠正事实要保证到达模型」原则,但实现不适用(对话层 vs KB 层)。

## 改的文件(全落地)

1. `src/hyperion/services/memory/schema.py` — KnowledgeItem 加 `corrects`+`corrected_by`;RecallHit 加 `corrected_by`+`render()` 标记
2. `src/hyperion/services/memory/backends/native/store.py` — DDL `corrected_by TEXT` + `ALTER TABLE` migration + `_KI_FIELD_LIST`/`_ki_to_row`/`_row_to_ki`/upsert + 新 `mark_corrected`
3. `src/hyperion/services/memory/backends/native/memorize.py` — 消费 `corrects` 回填 `corrected_by`
4. `src/hyperion/services/memory/backends/native/recall.py` — `CORRECTED_PENALTY=0.3` + `_ki_to_hit` 透传 + `_apply_decay_confidence` 降权
5. `src/hyperion/tools/mcp_memory.py` — `memory_memorize` 加 `corrects` 参数 + `_render_audit_card` CORRECTED 标记
6. `.claude/skills/memory-health-check/SKILL.md` — step 3/5 闭环已解矛盾
7. `tests/services/memory/test_memory_native.py` — 2 新测(corrects 标旧条 + recall 降权)
8. `tests/test_mcp_tools.py` — 1 新测(memory_memorize corrects 参数)+ _FakeMemSvc 记录

## 验证

- **单测全绿**:memory 19 passed;mcp_tools 29 passed;ruff clean。
- **不跑编译/真模型/opencode e2e**(用户自验铁律)。
- opencode e2e 待真机跑(用户自验):理想场景 = 跑 memory-health-check on wpa,体检 agent 发现 A派 vs B派矛盾 → 读到 B派 summary「纠正先前…误诊」→ 判 B 对 → `memory_memorize(corrects=[A派id...])` → A派被标 `corrected_by` + 检索降权 → 体检卡 ③ 显示 CORRECTED 已闭环。

## 故意不做(YAGNI)

- **不自动检测纠正**(用户拍板):不做 regex/embedding/LLM 判纠正。agent 显式声明 `corrects=[id]` 才标(自动检测误报率太高,调研坐实)。
- **不复用 superseded_by**:纠正 ≠ 失效。独立 `corrected_by` 字段解耦。
- **不加 corrects 入库**:transit 字段,写入时消费掉。
- **不级联纠正链**(A纠正B,C纠正A):单层标记。级联记 backlog,YAGNI。
- **降权因子不配置**:硬编码 0.3(对称 halflife 180天也硬编码)。

关联:[[memory-design-review-2026-08-12]](只追加指令背景)、[[memory-append-only-directive]](只追加对标 mem0 v3)、[[memory-health-check-handoff]](体检 skill)、[[recall-validation-handoff]](recall 价值验证)。
