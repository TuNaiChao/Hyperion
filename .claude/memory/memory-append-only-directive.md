---
name: memory-append-only-directive
description: "用户设计指令(2026-08-06,✅已落地):Hyperion 记忆【只追加写入 + 检索时最新为主、旧记忆仅供参考】,对标 mem0 v3。已改:删 memorize supersede(只追加)+ 松 recall 过滤(旧版本作参考)+ RecallHit 加 created_at;保留 merge/bi-temporal/手动 invalidate。关联 R4.1 文档同步。"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-08-06T02:40:37.687Z
---

2026-08-06 用户设计指令:**Hyperion 记忆改成"只追加写入、检索时最新为主、之前的记忆仅供参考"——不要写入时覆写/合并**。对标 mem0 从"写入时消歧"到"检索时推理"的演进。

> ✅ **已落地(2026-08-06,本会话)**:核实 mem0 v3 数字(arXiv:2504.19413 + LoCoMo 92.5/LongMemEval 94.4,均查实)+ 调研 2026 共识(bi-temporal 骨干 + 检索时/混合消解)→ 改代码:
> - **写侧** [memorize.py](src/hyperion/services/memory/backends/native/memorize.py):删 supersede 循环,`_merge_or_supersede` → `_merge_on_remention`(冲突只追加、旧条保持 active);**保留 merge**(同事实重提合并增强)。
> - **读侧** [store.py](src/hyperion/services/memory/backends/native/store.py):`search_bm25`/`search_vector` 过滤去 `superseded_by`(旧版本重见天日作参考),只留 `invalid_at`(手动 invalidate 错卡仍隐藏);list/count(管理视图)保持 active-only。
> - **检索信息** [schema.py](src/hyperion/services/memory/schema.py)+[recall.py](src/hyperion/services/memory/backends/native/recall.py):RecallHit 加 `created_at`+`superseded_by`,render 显日期+"(旧版本)";"最新为主"复用现成 decay(新=高分、旧=低分但可见),不预建 subject dedup(pull-by-need)。
> - **文档** [pr-review-design.md](docs/设计/pr-review-design.md):6 处 supersede→检索时最新为主;**测试** test_memory_native(重写 appends_both + 新增 recall latest 测)。34 memory + 15 bug_rca 测绿,ruff 绿。
> - **未走纯检索时极端**(reconsolidation 有失真风险):保留 bi-temporal 骨干 + 仅排名(不在检索时 mutate)。用户拍板"保留 merge 只删 supersede"(Hyperion content-addressed,merge=同事实增强非冲突覆写)。

**Why(用户给的 mem0 案例,待核验 arXiv:2504.19413 + v3 数字后用于设计文档):**
- **2025 论文(arXiv:2504.19413)+ v2 = 写入时消歧**:对话后 LLM 抽候选事实 → 向量检索找相近已有记忆 → LLM 在 ADD/UPDATE/DELETE/NOOP 决策("住北京"→"搬上海"=UPDATE)。优点:记忆库始终简洁一致。风险:① 一次错误 UPDATE/DELETE 不可逆丢历史;② 每条候选都要检索+第二次 LLM 判断(贵)。Mem0-g(实体-关系图,多跳+时序)是历史设计。
- **2026-04 v3 = 仅追加写入 + 混合检索**:一次 LLM 抽事实只做 ADD;"住北京"和"搬上海"作为带时间戳的两条事实并存。查询时融合 语义相似度+BM25+实体匹配+时间排序 找当前事实;agent 确认完成的动作也成一等事实。避免错误 UPDATE/DELETE 丢历史 + 少 LLM 调用 + 多检索信号+时间排序。报告 **LoCoMo 71.4→92.5(+21.1)、LongMemEval 67.8→94.4(+26.6)**。当前 OSS 移除外部图存储+relations 返回,实体链接仅内部检索加权。

**How to apply(对 Hyperion 的影响 + 待改的代码流程):**
- 现状冲突:`memorize.py:_merge_or_supersede`(同 subject 同 conclusion→贝叶斯合并;同 subject 不同 conclusion→**supersede**:旧 `invalid_at`+`superseded_by`)。这是**写入时消歧**,正是用户要改掉的。R3.4 ingest / R4.1 跨 PR 去重 / bug_rca 都走这条。
- 改造方向(待调研定稿):① 写入侧:冲突时**不 supersede**(不标 invalid),改为追加新条(带更新时间戳/版本);② 检索侧:recall 已有 decay+confidence 加权,需确保"被覆盖的旧条不过滤、只降权",并加**显式时间排序/最新为主**逻辑(latest-wins);③ bi-temporal(`valid_at`/`invalid_at`)保留作"参考"溯源,但召回默认不按 invalid_at 排除。
- ✅ **已完成**(见上方"已落地"段):核实数字 + 调研共识 + 出设计 + 改 memorize/store/schema/recall + 文档/测试。下方"改造方向"是当时的设计推演,已据此落地(保留作设计溯源,非覆写)。

关联:[[similar-bug-recall-roadmap]](P0 预注入也依赖 recall 行为) [[r35-report-handoff]] [[avoid-overengineering]](改造前先评估是否过度,但这是用户明确指令+前沿共识,不是加功能是改策略)。**注意:此指令与 R4.1 设计依赖的 supersede 去重有张力,设计时一并处理。**
