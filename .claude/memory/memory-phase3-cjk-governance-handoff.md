---
name: memory-phase3-cjk-governance-handoff
description: 2026-08-14 Phase 3 落地:jieba CJK 分词(BM25 中文检索)+ A2 治理展示(体检消费治理标签);FTS standalone 化
metadata: 
  node_type: memory
  type: project
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-08-14T06:26:20.092Z
---

# 记忆模块 Phase 3 交接(CJK 分词 + A2 治理展示)

**commit**:`6bade72`(feat)+ `5ea00a7`(roadmap)。**测试**:全套 278 绿 + ruff clean(+5 单测)。**e2e**:真 DB 备份→migration 79/79 重灌→纯中文 recall top-3 相关→恢复备份零副作用。roadmap 三阶段(Phase 1 矛盾+去重 / Phase 2 B3+B4 / Phase 3 CJK+A2)全部收口。

## CJK 分词(核心)

**问题**:FTS5 unicode61 不切中文(整段汉字 1 个 token),纯中文查询 BM25 路完全失明,只能靠向量 API。真库 wpa 52 条大段中文——roadmap 原判"低影响"(C 代码英文)是**误判**。

**方案(调研取舍)**:trigram 内置但要 ≥3 字查询(溢出/死锁/竞态全是 2 字)不合身;ICU 多数 Python sqlite3 构建没编译;jieba **Python 侧分词**零 C 扩展:索引侧+查询侧同一切词器,切完空格连回,unicode61 按空格切正好一词一 token。

**架构改动(必须记住 why)**:FTS 从 external-content + SQL 触发器 → **standalone 表**(文本自存,`upsert()` 同事务 delete-then-insert 维护)。原因:触发器在 SQL 层**调不了 Python 分词**。一致性成立的前提:summary/detail/root_cause 唯一写点就是 upsert(6 个 `UPDATE ... SET` 全不碰文本列——set_tags/set_confidence/set_kind/bump_access/set_invalid/mark_corrected)。

- [tokenize.py](../../../Desktop/Agent/Hyperion/src/hyperion/services/memory/backends/native/tokenize.py):`segment()` 只切 CJK 段(正则 `[一-鿿㐀-䶿豈-﫿]+`;英文标识符不进 jieba——切了反而碎成 `_/_/_`),停用词最小集滤掉,jieba 没装原样返回降级。
- store.py `_fts_sync`:upsert 同事务维护 FTS(rowid 按 id 回查,镜像 `_vec_upsert` 姿势);`_migrate_fts_standalone`:检测 `sqlite_master` 建表 SQL 含 `content=` → 重建 + 全量重灌,ki_meta `fts_standalone`=1 幂等,失败只 warning 不崩(BM25 返空,向量路照常)。
- `_fts_query`:查询先过 `_segment` 再 split(两侧同分词器是命中的关键)。

**单测抓的 2 个坑**:① 半角逗号贴着 CJK 段(`传输,扫描`)切完粘成一个 token → 段两侧补空格;② `executescript` **隐式 COMMIT** 打断外层 BEGIN IMMEDIATE(把 DROP+重建拆成两个自提交事务)→ 迁移里改单条 `execute`。第 ② 个是 sqlite3 的经典暗坑,以后写迁移必记。

**e2e 姿势(副作用意识)**:先备份→验证→恢复。第一遍 recall 触发了后台 auto-consolidate 给真 wpa 库打 3 条 needs_review(设计内行为但非用户显式操作)→ 恢复备份,改用 `svc.search()`(不 bump 不挂自转)重验,零副作用。另:`svc.close()` 后 fire-and-forget task 才跑会报 "Cannot operate on a closed database"(脚本时序,生产 MCP server 常驻不 close;`_safe_consolidate` 已 catch 不影响 recall)。

## A2 治理展示

- `memory_dump` 溯源卡渲染 `[tags]`(needs_review/merged_upstream/stale 逐条);header 加 `health: needs_review=N merged_upstream=N stale=N` 聚合行(无标签不输出)。
- memory-health-check SKILL 升级**双层读法**:consolidate 五 pass 自动打标(确定性),agent 在标签之上语义读信号(哪条该裁/该补锚点)。原案"置信度曲线可视化"没做——MCP 输出是文本,画曲线是展示端的事,YAGNI。

## 相关

- Phase 1/2:[[memory-consolidation-phase1-handoff]] / [[memory-consolidation-phase2-handoff]]
- 路线图 docs/memory-module-roadmap.md **已删**(2026-08-14 三阶段全 ✅ 后规划使命完成):长期价值(偏离记录要点+"明确不做"清单)已并入 docs/memory-module-analysis.md §7/§8,后续看那份。
