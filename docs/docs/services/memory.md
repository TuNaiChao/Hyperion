# 服务 · 记忆核心(memory)

> `services/memory/` —— **P3 记忆与持续学习(★差异化)**。结构化 `KnowledgeItem` + 可换后端(v1 native = SQLite + FTS5 + 向量 + 四路 RRF 召回)+ bi-temporal + Bayes 合并。
> 闭环 P1/P2:调研 / RCA 产出 → `memorize`;新任务 → `recall` 预注入历史教训。

## 概览

普通 coding agent 每开新会话都失忆。Hyperion 把"这个库长啥样 / 之前哪些 bug 怎么修的"沉淀成可检索、带溯源、持续学习的记忆。一条记忆是一个 `KnowledgeItem`;写入只追加(对标 mem0 v3),检索时最新为主、旧作参考。

三种知识项(用 `kind` 区分,同一张表存):

| kind | 产出 | 说明 |
|---|---|---|
| `codebase_fact` | P1 调研 | 模块 / 符号 / 架构是干啥的、关键设计 |
| `bug_lesson` | P2 bug-RCA | 根因是啥、怎么修的、影响面多大 |
| `mental_model` | 巩固升级 | 反复被召回(≥N 次)的教训固化成规律 |

## 源码

| 文件 | 职责 |
|---|---|
| `schema.py` | 数据模型(`KnowledgeItem` / `RecallHit` / `Scope` / `Evidence` / `SourceTier`) |
| `manager.py` | `MemoryService` ABC 契约(分层)+ 单例工厂 + 后端解析 |
| `ingest.py` | 文档 / 补丁摄取(`ingest_document` / `PatchIngestPipeline` / `LongDocChunker`) |
| `backends/native/store.py` | SQLite + FTS5(external-content + 三同步触发器)+ float32 向量 BLOB |
| `backends/native/recall.py` | 四路 RRF 召回 + 衰减 |
| `backends/native/memorize.py` | 写入(嵌向量 + 连边 + Bayes 合并) |
| `backends/native/consolidate.py` | 巩固(升级 mental_model) |
| `backends/native/extract.py` | LLM 从报告抽 KnowledgeItem |
| `backends/native/structural.py` | 结构图路(`StructuralBackend` Protocol + Noop / Crg) |
| `backends/native/service.py` | `NativeMemoryService(MemoryService)` —— 组装全部依赖 |

## 数据模型(schema.py)

```python
class SourceTier(StrEnum): delegate | stated | inferred | imported | unknown | tool   # 决定合并权重
TIER_WEIGHT = {delegate:1.0, stated:1.0, inferred:0.7, imported:0.6, unknown:0.8, tool:0.5}

class Scope(BaseModel): owner: str = "default"; codebase: str = "default"   # 租户隔离

class Evidence(BaseModel): file: str; line: int | None = None; snippet: str = ""

def make_id(scope, kind, summary) -> str   # sha256(scope+kind+content_key)[:16] 稳定 id

class KnowledgeItem(BaseModel):
    id: str = ""                # 空 → 按内容自动算
    kind: Literal["codebase_fact", "bug_lesson", "mental_model"]
    repo: str; scope: Scope; summary: str
    detail: str = ""
    # bug_lesson 专用
    symptom: str = ""; root_cause: str = ""; fix_patch: str = ""; blast_radius_files: list[str] = []
    # codebase_fact 专用
    kind_detail: Literal["module", "symbol", "architecture"] = "module"
    # 溯源 + 证据
    commit_sha: str | None = None; evidence: list[Evidence] = []; source: str = ""; source_tier: SourceTier
    # 持续学习信号
    confidence: float = 0.0; access_count: int = 0; last_recalled: datetime | None = None
    # bi-temporal
    valid_at: datetime; invalid_at: datetime | None = None; created_at: datetime
    related: list[str] = []; tags: list[str] = []; superseded_by: str | None = None
    embedding: list[float] | None = None
    @property
    def active(self) -> bool: ...   # invalid_at is None and superseded_by is None

class RecallHit(BaseModel):
    summary: str; score: float; source: Literal["memory","code","structural"]; kind: str; repo: str
    evidence: list[Evidence]; confidence: float; valid_at; created_at; superseded_by; item_id
    file; line_start; line_end; snippet   # code/structural 路定位
    def render(self) -> str: ...          # 给 LLM 看的一行(带溯源+置信+日期+旧版本标)
```

**设计要点**:
- **稳定 id**(`make_id`):同一根因重复 memorize → 同 id → 走合并 / 加权而非新增(持续学习去重基础)。
- **bi-temporal**(`valid_at` / `invalid_at`):矛盾的旧知识"失效"而非"删除",能回答"这个 bug 在 X 时点还存不存在"。
- **溯源**(`commit_sha` + `evidence[file:line]`):每条结论都能追到具体代码状态 / 行。
- **只追加**:写入不取代,靠检索 decay 排"最新为主";旧版本不隐藏,作参考(手动 `invalidate` 的错卡才隐藏)。

## 契约(manager.py)

```python
class MemoryService(abc.ABC):
    supports_search: ClassVar[bool] = True
    # tier-1(必须实现)
    async def memorize(self, items: list[KnowledgeItem], scope: Scope) -> int
    async def recall(self, query: str, scope: Scope, *, top_k: int = 5) -> list[RecallHit]
    # tier-2(默认 raise,后端按需覆盖)
    async def search(query, scope, *, top_k=5, **kw) -> list[RecallHit]
    async def get(item_id, scope) -> KnowledgeItem | None
    async def list_items(scope, *, kind=None, include_invalid=False) -> list[KnowledgeItem]
    async def memorize_report(report_text, scope, *, repo=None, commit_sha=None, source="", source_tier=inferred) -> int
    # tier-3(可选)
    async def consolidate(scope) -> dict[str, Any]
    async def invalidate(item_id, scope, *, reason="") -> bool
    def close(self) -> None
    @classmethod
    def from_config(cls, cfg, **host_hooks) -> MemoryService
```

工厂 + 后端解析:

```python
def discover_backends() -> dict[str, type[MemoryService]]   # 扫 backends/<name>/__init__.py 的 BACKEND_CLASS
def resolve_backend_class(name) -> type[MemoryService]      # 短名 | 'pkg.mod:Cls',拒绝静默回退
def get_memory_service(config=None) -> MemoryService        # 双检锁单例
def reset_memory_service() -> None
```

## native 后端(★存储栈 + 召回)

### store.py(SQLite + FTS5 + 向量 BLOB)

```python
class MemoryStore(store_path, *, db_name="memory.db"):   # WAL + busy_timeout;写锁串行读并发
    def upsert(items) -> int
    def bump_access(item_id); set_invalid(item_id, *, superseded_by=None, invalid_at=None) -> bool
    def get(item_id) -> KnowledgeItem | None
    def list_items(scope, *, repo=None, kind=None, include_invalid=False)
    def count(scope, *, include_invalid=False) -> int
    def search_bm25(query, scope, *, repo=None, limit=20) -> list[tuple[KnowledgeItem, float]]
    def search_vector(query_vec, scope, *, repo=None, limit=20) -> list[tuple[KnowledgeItem, float]]
```

FTS5 用 **external-content + 三同步触发器**(unicode61);向量是 **float32 BLOB,cosine 在 Python 算**。bi-temporal:矛盾 / 失效软删,**永不物理删**。

### recall.py(★四路融合)

```python
def recall(query, scope, *, store, repo=None, top_k=5, embedder=None, reranker=None,
           code_bundle=None, structural=None, halflife_days=180.0, bump=True) -> list[RecallHit]
```

```
四路召回:
  memory·BM25   (始终)
  memory·vector (需 embedder)
  code          (code_index,经 code_bundle)
  structural    (CRG,经 structural)
      │
      ▼  RRF(K=60)融合
  可选 reranker 精排
      │
      ▼  衰减 exp(-age / halflife) × 置信加权
  top-k
      │
      ▼  命中 memory 条 → bump_access(被召回次数 +1)
```

### memorize.py

```python
def memorize_items(items, *, store, embedder=None, step=0.3) -> int
    # 嵌向量 + 文件交集连边 + Bayes 合并重提 + upsert;冲突只追加不取代
def memorize_report(report_text, *, repo, scope, store, model, embedder=None,
                    commit_sha=None, source="", source_tier=inferred, step=0.3) -> int
```

### consolidate.py / extract.py / structural.py

```python
def consolidate(scope, *, store, promote_access_count=3) -> dict   # access_count 达标 → 升级 mental_model
def extract_items(report_text, *, repo, scope, model, commit_sha=None, source="", source_tier=inferred) -> list[KnowledgeItem]
    # LLM 喂 JSON Schema + 直出 JSON + _extract_json_object 鲁棒解析 + 逐条校验(DeepSeek-safe)
class StructuralBackend(Protocol): blast_radius(query, *, repo, limit) -> list[RecallHit]
class NoopStructuralBackend            # 默认
class CrgStructuralBackend(repo_root)  # callers_of / callees_of via code_review_graph.tools.query
```

### service.py

`NativeMemoryService(MemoryService)` —— `from_config(cfg)` 组装全部依赖(embed / rerank / code_bundle 复用 code_index;structural 默认 Noop,`memory.native.structural=crg` 时用 Crg);实现所有 tier-1/2/3。

## 摄取(ingest.py)

```python
class LongDocChunker(max_chars=6000): .split(text) -> list[str]   # 按 markdown header 切,超长按段落再切

class PatchIngestPipeline(diff_text, *, repo, scope, source="", source_tier=imported, commit_sha=None, model=None):
    .run() -> list[KnowledgeItem]
    # unified-diff 解析 hunk → code_index.retrieve 取上下文 → LLM 抽 root_cause → 组装 bug_lesson(各环降级)

async def ingest_document(path, *, scope, repo, svc=None, source_tier=imported, commit_sha=None, kind="auto", max_chars=6000) -> dict
    # 入口分流器:.md/.txt/.pdf → 报告路(parse_issue + LongDocChunker + memorize_report)
    #            .patch/.diff → 补丁路(PatchIngestPipeline)
```

> [!NOTE]
> 补丁 id 按 **diff 内容**算(非 LLM summary),防同一补丁重复入库。

## 配置

```yaml
memory:
  backend: native
  store_path: data/memory
  native:
    structural: none            # none | crg
    embed: code_index           # 复用 code_index embedder | off
    rerank: code_index          # 复用 code_index reranker | off
    recall_top_k: 5
    decay_halflife_days: 180.0
    promote_access_count: 3
    merge_step: 0.3
```

切后端 = 丢 `backends/<name>/` 文件夹(暴露 `BACKEND_CLASS`)+ 改 `backend`;mem0 / cognee 作可选 extra(零锁死)。拒绝静默回退。

## 边界与限制

- **只追加,不取代**:写入冲突走合并 / 加权;旧版本靠检索 decay 排后,不隐藏(手动 `invalidate` 的错卡才隐藏)。
- 向量 cosine 在 Python 算(非 SQLite 原生向量)—— 规模大时可换后端。
- `extract.py` 走「喂 Schema + 直出 JSON + 解析」,不用 `tool_choice`(DeepSeek 思考模式不支持)。
- CRG structural 路是可选 extra。

## 示例

```python
import asyncio
from hyperion.services.memory import get_memory_service
from hyperion.services.memory.schema import KnowledgeItem, Scope, Evidence, SourceTier

svc = get_memory_service()
scope = Scope(owner="default", codebase="wpa_supplicant")

# 写
item = KnowledgeItem(
    kind="bug_lesson", repo="wpa_supplicant", scope=scope,
    summary="scan_only_handler 误路由 orphan scan 结果",
    root_cause="...", fix_patch="...",
    evidence=[Evidence(file="scan.c", line=142)],
    source="cli", source_tier=SourceTier.stated,
)
asyncio.run(svc.memorize([item], scope))

# 翻
hits = asyncio.run(svc.recall("scan result orphan", scope, top_k=5))
for h in hits:
    print(h.render())
```

## See Also

- [../tools/mcp-tools.md](../tools/mcp-tools.md) — `memory_recall` / `memory_memorize`
- [../guides/memory-ingest.md](../guides/memory-ingest.md) — 摄取报告 / 补丁
- [../operations/inspect-memory.md](../operations/inspect-memory.md) — 查看 SQLite
- [../configuration.md](../configuration.md) §memory
- 上级 [../设计/memory-design.md](../../设计/memory-design.md) — 完整设计
