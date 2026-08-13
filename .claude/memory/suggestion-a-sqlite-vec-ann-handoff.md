---
name: suggestion-a-sqlite-vec-ann-handoff
description: 建议 A 落地:记忆向量换 sqlite-vec ANN(渐进式双路径阈值切换)。调研校正「迁 LanceDB」为「sqlite-vec 同栈」+ 探针盲点 cosine metric。
metadata:
  type: project
---

2026-08-13 落地 architecture-review §五 建议 A(记忆向量 ANN,优先级最高 🥇)。

## 调研重塑了方案(三个关键校正)

1. **deer-flow 生产级完全不用向量**(Explore agent 遍历确认)—— 纯 BM25 + jieba + time_decay,零 embedding/ANN。框架留了 `retrieval_adapter` 注入点但官方没实现。信号:这规模段向量 ANN 易过度设计(踩坑#2)。但仍做 —— Hyperion 记忆有向量路(code_index embedder 复用),且 cross-codebase 累积会到 N>500。
2. **numpy 向量化是死路**(benchmark 实测):小规模无收益、大规模反慢 2.5×。瓶颈在**逐行 `np.frombuffer` 解码 BLOB**,不在 Python 循环本身。所以"换向量化"这看似最小改动其实没用。
3. **sqlite-vec 是真解**(benchmark,1024维 K=20):N>500 稳定快 2-4×(N=500:1.6×,N=2000:4.3×,N=10000:2.1×),N<200 反慢 3× → 双路径阈值切换。**且 sqlite-vec 0.1.9 已在 site-packages,零新运行时依赖**(只需 pyproject 声明 + load_extension),嵌入式进程内,与 SQLite 栈同源。

**原建议写「迁 LanceDB」,校正为「sqlite-vec」** —— store.py docstring 明确拒绝 LanceDB(SQLite 关系操作是 KI 核心:scope/kind 过滤、软删、access_count 累加;LanceDB 留 code_index 的代码 chunk)。sqlite-vec 是 SQLite 扩展,同栈零冲突,不造第三套检索栈。

## 改了啥

- **`search_vector` 分流**(store.py):`count(scope) > ann_threshold(默认 500)` → vec0 KNN,否则现状 loop(抽成 `_search_vec_loop`)。`_should_use_ann` 判定 + `_search_vec0` KNN 路径。契约不变,recall.py 无感。
- **延迟建表 `_ensure_vec_table(dim)`**(镜像 code_index `_open_or_create(repo, dim)`):首次 upsert 带 embedding 的 KI 时用 `len(embedding)` 探测 DIM 建表(vec0 维度建表时定死,但 config 不硬编码 dim —— code_index embed.py 的 dim 从 model 派生 Qwen3 可调 64-2048)。dim 存 `ki_meta('vec_dim')` 供冷启动恢复。
- **`upsert` 同事务双写**(store.py):主表 ON CONFLICT 后,同事务 `_vec_upsert` delete-then-insert 进 vec0(vec0 虚拟表无 ON CONFLICT;executemany 对 vec0 实测可用)。rowid 稳定(ON CONFLICT 原地更新),按 id 回查 rowid 映射。失败静默降级(主表已写,绝不崩)。
- **`config.py`**:`NativeMemoryConfig.auto_index: bool = True` + `ann_threshold: int = 500`。
- **`pyproject.toml`**:加 `sqlite-vec>=0.1.9,<0.2` 声明。
- 5 单测 + 全记忆 42 绿(37→42)。

## 探针盲点(实施时发现,关键)

1. **sqlite-vec 默认 metric 是 L2 euclidean,不是 cosine**!`distance=√3` 对零向量。我 plan 里写 `1-distance` 转 cosine sim 完全错(误差 1.83)。**修正**:建表显式 `distance_metric=cosine` → distance = 1 - cosine_sim(实测转换误差<1e-7)。
2. **cosine metric 下零向量崩**(distance=None,未定义)。**修正**:`_vec_upsert` 跳过全零 embedding(零向量仍进主表 BLOB,loop 路 cosine 算 sim≈0 排末尾不崩);`_search_vec0` 查询向量全零 → 降级 loop。
3. **partition_key 完美隔离 KNN**(实测):查 `owner=A AND codebase=A` 只在该 scope 搜,绝不混入 B → owner+codebase 挂 partition_key = 完整还原 `_scope_filter`,隔离免费。active/repo 只能 KNN 后回主表过滤(vec0 不支持非 partition_key WHERE)→ over_fetch=limit×4 补漏(对齐 recall.py `cand=max(top_k*4,20)`)。

## 设计要点(防回退踩坑)

1. **双路径不删 loop** —— benchmark 实测 N<200 loop 最快;阈值切换正收益最稳。`_search_vec_loop` 是小规模快路径 + vec0 降级兜底,双重角色。
2. **同事务双写一致性** —— `BEGIN IMMEDIATE ... 主表 executemany ... _vec_upsert ... COMMIT`,主表+vec0 原子;vec0 失败不 ROLLBACK 主表(降级,只 warning)。
3. **维度冲突不重建** —— 配置换 model 改 dim(如 Qwen3 1024→2048)→ `_ensure_vec_table` 返 False → vec0 不写(降级 loop)。重建=全量 reindex 属运维操作,记 backlog。
4. **`uv run` 改 pyproject 后慢** —— 测试用 `.venv/bin/python -m pytest` 直接跑(避开 uv lockfile 校验);sqlite-vec 已在 site-packages。

## 故意不做(YAGNI)

- **不迁 LanceDB** —— store docstring 拒绝;sqlite-vec 同栈零冲突。
- **不做后台线程 backfill** —— 旧库升级(vec0 空但主表有 embedding)首次超阈值 search 时惰性触发(YAGNI;线程+事务复杂)。真机规模小。
- **不做 HNSW/IVF 调参** —— sqlite-vec 小规模近 brute-force,默认参数够;千万级再调(记 backlog)。
- **不强制 active 过滤进 partition_key** —— 软删是少数,RRF 容错;加 active 进 partition_key 双写更复杂。
- **不做 numpy 向量化** —— benchmark 已证死路(逐行解码 BLOB 主导)。

相关:[[memory-design-review-2026-08-12]] recall 多路融合(RRF 容错补 vec0 active 漏召回);[[avoid-overengineering]] deer-flow 零向量信号筛掉过度设计;[[align-to-deerflow-production-grade]] 生产级对齐;[[toolset-after-audit-2026-08-10]] memory 工具集。
