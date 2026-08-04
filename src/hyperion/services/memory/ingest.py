"""文档摄取 → 记忆(ingest,R3.4 · P3 记忆扩充)。

干什么(面向小白)
  把"外面的文档"吃进 Hyperion 的长期记忆,变成可检索、带溯源、能持续学习的知识项。三类入口:
    - bug 报告 / 调研报告(.md/.txt/.pdf)→ 复用 parse_issue 取文本 → (长文切块)→ extract_items 抽 KI → memorize。
    - 补丁(.patch/.diff)→ PatchIngestPipeline:解 hunk → retrieve 周围代码上下文 → LLM 抽 root_cause/intent
      → 组装 bug_lesson(带 file:line 证据)→ memorize。
  两类最后都汇到 svc.memorize —— 去重(content-addressed make_id)/ 合并(bayesian step=0.3)/
  关联(evidence 文件交集)全已在 native 后端就位,**本模块零新存储**。

为什么不自造存储(调研定论,非凭感觉)
  mem0/cognee/Zep/Letta 2026 格局 + mnemopi veracity-consolidation 逐条核实:Hyperion 已走的结构化
  KnowledgeItem 路线 = mnemopi 的 Memory 式(bayesian 合并 / source_tier 权重 / 确定性 sha256 ID /
  bi-temporal + superseded_by 全就位)。Cognee 的 Extract→Cognify→Load 是从零造整条管线;Hyperion
  复用已有 extract/memorize/retrieval,只补三块拼图:① 文档入口 ② 长文分块 ③ 补丁的 retrieve-then-summarize。

补丁为什么要 retrieve-then-summarize
  裸 diff 缺周围代码上下文,LLM 难判根因/意图。先 code_index.retrieve 取被改符号周围代码再喂 LLM。
  依据(均已 WebFetch 核验):PATCH(ACM 2025)/ SpecRover(ICSE 2025)/ What-Do-They-Fix(NDSS 2026)。
  (注:计划旧版引的 "Codeant/ICSE2026 arXiv:2503.15223" 是张冠李戴 —— 该 id 实为 SWE-bench correctness 论文;
   Codeant 是商业产品非论文。已订正。)

kind 设计:不新增 kind(调研结论)
  补丁产出的教训用现有 `bug_lesson`(它本就有 symptom/root_cause/fix_patch/blast_radius_files 字段,
  schema.py:144)+ `tags=["patch_insight"]` + `source_tier=SourceTier.imported`(枚举里本就有,schema.py:50)。
  加新 kind 要动 schema/extract prompt/_same_subject/consolidate/FTS,牵动大、不值。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from hyperion.services.memory.backends.native.extract import extract_items  # noqa: F401 (re-export 便于外部单测)
from hyperion.services.memory.manager import MemoryService, get_memory_service
from hyperion.services.memory.schema import Evidence, KnowledgeItem, Scope, SourceTier, make_id  # noqa: F401 (Evidence 供 PatchIngestPipeline.run 核心用)
from hyperion.services.trigger_parser.parser import parse_issue  # 复用现成 loader(.md/.txt/.pdf)

logger = logging.getLogger(__name__)

# 补丁类后缀(按扩展名自动判定走 PatchIngestPipeline)。
_PATCH_SUFFIXES = (".patch", ".diff")

# 长文切块默认上限(字符)。单块塞一次 extract_items 的 LLM 调用;6000 字远低于任何模型上下文,
# 既保证每次抽取聚焦、省钱,又给 markdown 报告留足一节的篇幅。
_DEFAULT_MAX_CHARS = 6000


# ──────────────────────────────────────────────────────────────────────────────
# §1 LongDocChunker:长文分块(报告类用,纯 stdlib)
# ──────────────────────────────────────────────────────────────────────────────


_HEADER_RE = re.compile(r"^(#{1,6})\s+(.*)$")


class LongDocChunker:
    """长报告分块器:按 markdown header 切节;单节仍超 max_chars → 按空行段落再切。

    为什么自己写(不引 langchain-text-splitters):报告结构就是 markdown 章节,header 切节天然
    对齐语义边界,几行正则够用(YAGNI)。将来遇乱序长文再换 RecursiveCharacterTextSplitter。
    每块自带 header 行做前缀(模型看得到自己在读哪一节)。

    面向小白:就像把一本厚书按"章"撕成小册子,一章还是太厚就再按"段"撕 —— 每撕一刀都顺着作者
    本来就分好的边界,不会把一句话从中间劈开。
    """

    def __init__(self, max_chars: int = _DEFAULT_MAX_CHARS):
        self.max_chars = max_chars

    def split(self, text: str) -> list[str]:
        """text → 文本块列表(每块 ≤ max_chars,除非单段本身就超)。短文(无 header 且短)→ 返 1 块。"""
        if not text or not text.strip():
            return []
        sections = self._by_header(text)
        chunks: list[str] = []
        for title, body in sections:
            # 每块带上它自己的 header 行(模型才知道在读哪一节)。
            blob = f"{title}\n{body}".strip() if title else body.strip()
            if len(blob) <= self.max_chars:
                chunks.append(blob)
            else:
                chunks.extend(self._by_paragraph(blob))
        return chunks or [text.strip()]

    def _by_header(self, text: str) -> list[tuple[str, str]]:
        """按 markdown header(`^# …`)切 (title, body)。第一个 header 之前的内容 title=""。"""
        sections: list[tuple[str, str]] = []
        cur_title = ""
        cur_body: list[str] = []
        for line in text.splitlines():
            if _HEADER_RE.match(line):
                if cur_body or cur_title:
                    sections.append((cur_title, "\n".join(cur_body)))
                cur_title = line
                cur_body = []
            else:
                cur_body.append(line)
        if cur_body or cur_title:
            sections.append((cur_title, "\n".join(cur_body)))
        return sections

    def _by_paragraph(self, blob: str) -> list[str]:
        """单节超长 → 按空行段落攒成 ≤ max_chars 的块(单段本身超长则整段成一块,不劈中段)。"""
        paras = re.split(r"\n\s*\n", blob)
        chunks: list[str] = []
        cur: list[str] = []
        cur_len = 0
        for para in paras:
            para = para.strip()
            if not para:
                continue
            # 加这一段会爆上限、且当前块非空 → 先把当前块封口,另起一块。
            if cur and cur_len + len(para) > self.max_chars:
                chunks.append("\n\n".join(cur))
                cur = []
                cur_len = 0
            cur.append(para)
            cur_len += len(para) + 2  # +2 是 \n\n 的粗略开销
        if cur:
            chunks.append("\n\n".join(cur))
        return chunks


# ──────────────────────────────────────────────────────────────────────────────
# §2 PatchIngestPipeline:补丁 → 记忆(核心算法 · 窗口展示 · 用户手敲)
# ──────────────────────────────────────────────────────────────────────────────


def _retrieval_bundle():
    """构造 code_index 的 (embedder, store, reranker) 三元组(镜像 code_nav._retrieval_bundle / native._code_index_bundle)。

    补丁路取"被改符号周围代码上下文"用(retrieve-then-summarize 的 retrieve 腿)。
    code_index 没配好 / 依赖缺失 → 返 None,PatchIngestPipeline 降级为"只喂 diff 给 LLM"(不阻塞)。
    局部 import:没建索引的环境 import 本模块也不触发重依赖加载。
    """
    try:
        from hyperion.platform.config import get_app_config
        from hyperion.services.code_index.embed import create_embedder
        from hyperion.services.code_index.retrieval import create_reranker
        from hyperion.services.code_index.store import LanceDBStore

        cfg = get_app_config()
        embedder = create_embedder(cfg.code_index.embedding)
        vs = getattr(cfg.code_index, "vector_store", None)
        vs_path = getattr(vs, "path", "data/code_index") if vs else "data/code_index"
        store = LanceDBStore(vs_path)
        reranker = create_reranker(getattr(cfg.code_index, "reranker", None))
        return embedder, store, reranker
    except Exception as e:  # noqa: BLE001 - code_index 没配好不阻断 ingest(只少 retrieve 腿)
        logger.warning("ingest: code_index bundle 构造失败,补丁路将只喂 diff 不 retrieve: %s", e)
        return None


# hunk 头:^@@ -旧起,长 +新起,长 @@ (我们只要新文件的 +新起 当 evidence 行号)
_HUNK_HEADER_RE = re.compile(r"^@@\s*-(\d+)(?:,\d+)?\s*\+(\d+)(?:,\d+)?\s*@@")


def _parse_diff_hunks(diff_text: str) -> list[dict]:
    """解析 unified-diff → [{file, new_start, body}, …],按出现顺序。

    面向小白:一个补丁由若干 hunk(代码块改动)组成,每个 hunk 头是
    `@@ -旧起,长 +新起,长 @@`。我们要「新文件」的起始行(新起)当 evidence 行号;
    file 取这 hunk 之前最近的 `+++ b/路径`。解析不出(不是合法 diff)→ 返 [](上层降级)。
    """
    if not diff_text:
        return []
    lines = diff_text.splitlines()
    hunks: list[dict] = []
    cur_file = ""
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if line.startswith("+++ "):
            rest = line[4:].strip()
            if rest.startswith("b/"):
                rest = rest[2:]
            cur_file = "" if rest.startswith("/") else rest  # /dev/null → 空(被删的文件)
        elif line.startswith("diff --git"):
            m = re.search(r"\bb/(.+)$", line)
            cur_file = m.group(1).strip() if m else cur_file
        m = _HUNK_HEADER_RE.match(line)
        if m:
            new_start = int(m.group(2))  # 新文件起始行
            j = i + 1
            while j < n and not lines[j].startswith("@@") and not lines[j].startswith("diff --git"):
                j += 1
            hunks.append({"file": cur_file, "new_start": new_start, "body": "\n".join(lines[i + 1:j])})
            i = j
            continue
        i += 1
    return hunks


def _coerce_int(v) -> int | None:
    """LLM 给的行号防御解析 → 首个 int(或 None)。

    镜像 bug_rca `_coerce_evidence_line`(nodes.py:311)—— 但**不跨包 import 它**:
    services.memory 依赖 workflows.bug_rca 是反向依赖(层次倒置),所以内联这 3 行。
    模型偶尔吐 "3067" / "3067,4105" / "3067-3070" / float,都取首个 int。
    """
    if v is None or isinstance(v, bool):  # bool 是 int 子类,先排除
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    if isinstance(v, str):
        first = v.split(",")[0].split("-")[0].strip()
        try:
            return int(first)
        except ValueError:
            return None
    return None


# LLM 抽 bug 教训的提示词(喂 Schema + 直出 JSON,DeepSeek-safe,沿用 extract 的策略)。
_PATCH_PROMPT = """你在分析一个代码补丁(unified diff),为代码库的"长期记忆"抽出一条可复用的 bug 教训。

### 补丁 ###
```diff
{diff}
```

### 被改文件周围的代码上下文(仓库检索得来;可能为空)###
{context}

### 要求 ###
只输出一个 JSON 对象(不要 markdown 围栏、不要解释文字),严格符合:
{{
  "summary": "一句话人读摘要:这个补丁修了什么 bug 或做了什么(≤120 字,要能脱离补丁独立看懂)",
  "root_cause": "根因/动机:为什么会出这个问题,或这个改动的意图",
  "blast_radius_files": ["涉及的文件路径"],
  "evidence": [{{"file": "相对仓根路径", "line": <int 或 null>, "snippet": ""}}]
}}
拿不准的字段留空字符串或空数组。"""


class PatchIngestPipeline:
    """补丁 → 知识项(retrieve-then-summarize,R3.4 核心算法)。

    流程
    ---
      .patch/.diff 文本
        → ① 解析 unified-diff 成 hunk 列表[(file, new_start, added_lines, removed_lines), …]
        → ② 每个 hunk:code_index.retrieve(file/symbol)取被改符号周围代码上下文
             └ repo 未索引(retrieve 返 empty)→ 降级:只喂 diff,不阻塞(同 Verifier 降级哲学)
        → ③ LLM(diff + 上下文 + 可选 commit msg)→ {root_cause, intent, 影响符号}(DeepSeek-safe)
        → ④ 组装 bug_lesson KI:
             fix_patch=diff 全文 / evidence=[Evidence(file,new_start,snippet)] /
             blast_radius_files=hunk 涉及的文件 / tags=["patch_insight"] /
             source_tier=SourceTier.imported / source=<patch 文件路径>

    为什么要 retrieve(不直接喂 diff)
    ---
      裸 diff 缺上下文,LLM 难判根因/意图。先取被改符号周围代码再喂 LLM。
      依据:PATCH(ACM 2025)/ SpecRover(ICSE 2025)/ What-Do-They-Fix(NDSS 2026)。

    复用(别重造)
    ---
      - unified-diff 解析:仓库里目前**完全没有**(validate_patch 只 git apply --check;_observe_patch
        只读 git diff 字符串,都不解 hunk)。手切 `^@@ -a,b +c,d @@` 或借 whatthepatch。
      - hunk 行号防御解析:复用 bug_rca `_coerce_evidence_line`(nodes.py:311,处理 int/"3067,4105"/"3067-3070")。
      - retrieve:`code_index.retrieval.retrieve(query, repo, embedder, store, reranker, *, top_k=)`
        (retrieval.py:236)→ RetrievalHit(file, start_line, end_line, text, …)。
      - DeepSeek-safe JSON 抽:**复用** extract._extract_json_object(extract.py:91),别造第二个。
      - evidence 渲染:可借 bug_rca `_render_evidence_snippets`(nodes.py:162,±10 行窗口)。

    ★ run() 已实现:解析 hunk → retrieve 周围代码 → LLM 抽根因 → 组装 bug_lesson;
       各环降级(解析失败返空 / 模型失败用文件名摘要 / retrieve 空只喂 diff),绝不阻塞 ingest。
    """

    def __init__(
        self,
        diff_text: str,
        *,
        repo: str,
        scope: Scope,
        source: str = "",
        source_tier: SourceTier = SourceTier.imported,
        commit_sha: str | None = None,
        model=None,  # BaseChatModel | None;None → run() 内 create_chat_model(role=…)
    ):
        self.diff_text = diff_text or ""
        self.repo = repo
        self.scope = scope
        self.source = source
        self.source_tier = source_tier
        self.commit_sha = commit_sha
        self.model = model

    def run(self) -> list[KnowledgeItem]:
        """★ 核心:解析 hunk → retrieve 周围代码 → LLM 抽 root_cause → 组装 bug_lesson。

        降级链(任何一环坏都不抛、不阻塞 ingest):
          - 解析不出 hunk → 返 [](不写);
          - 没模型 / LLM 失败 → 用 hunk 文件名凑降级摘要,仍写一条(只装 fix_patch,不判根因);
          - repo 未索引 / retrieve 空 → 只喂 diff 给 LLM。
        """
        # ① 解析 unified-diff 成 hunk 列表。
        hunks = _parse_diff_hunks(self.diff_text)
        if not hunks:
            logger.warning("ingest.patch: 解析不出 hunk(不是合法 diff?),跳过,不写记忆。")
            return []
        files = list(dict.fromkeys(h["file"] for h in hunks if h.get("file")))

        # ② retrieve 周围代码上下文(repo 未索引 → 空串,降级只喂 diff)。
        context = self._gather_context(hunks)

        # ③ LLM 抽 root_cause/intent(没模型 → _summarize 内部返 None,降级用 hunk 自动摘要)。
        if self.model is None:
            self.model = self._build_model()
        extracted = self._summarize(context)

        # ④ 组装一条 bug_lesson(fix_patch 装全文 / evidence 锚 file:line / tags=patch_insight)。
        return [self._assemble_ki(hunks, files, extracted)]

    def _build_model(self):
        """从 config 取记忆抽取用的模型(role=memory_extractor,走便宜模型);无则 None。"""
        from hyperion.platform.config import get_app_config
        from hyperion.platform.models import create_chat_model

        cfg = get_app_config()
        roles = getattr(cfg, "model_roles", None) or {}
        role = roles.get("memory_extractor") or (cfg.models[0].name if cfg.models else None)
        return create_chat_model(role) if role else None

    def _gather_context(self, hunks: list[dict]) -> str:
        """retrieve-then-summarize 的 retrieve 腿:取被改文件周围代码上下文。

        repo 没建索引 / bundle 没配好 / retrieve 空 → 返 ""(降级,run() 只喂 diff 给 LLM)。
        每个 hunk 的"新增行"当检索种子(query),取 top-3 chunk 拼成上下文。
        """
        bundle = _retrieval_bundle()
        if bundle is None:
            return ""
        embedder, store, reranker = bundle
        # repo 没索引过 → retrieve 必空,直接跳过(省一次 embed 调用)。
        try:
            if store.count(self.repo) == 0:
                return ""
        except Exception:  # noqa: BLE001
            return ""
        from hyperion.services.code_index.retrieval import retrieve

        parts, seen = [], set()
        for h in hunks:
            f = h.get("file") or ""
            if not f or f in seen:
                continue
            seen.add(f)
            # 新增行(+ 开头、非 +++ 文件头)当检索种子。
            added = "\n".join(line[1:] for line in h["body"].splitlines()
                              if line.startswith("+") and not line.startswith("+++"))
            query = (f + "\n" + added).strip() or f
            try:
                res = retrieve(query, self.repo, embedder, store, reranker, top_k=3)
            except Exception as e:  # noqa: BLE001 - 单文件 retrieve 失败不连坐
                logger.warning("ingest.patch: retrieve %s 失败,跳过: %s", f, e)
                continue
            for hit in res.hits:
                parts.append(f"### {hit.file}:{hit.start_line}-{hit.end_line} ({hit.symbol})\n{hit.text}")
        return "\n\n".join(parts)

    def _summarize(self, context: str) -> dict | None:
        """LLM(diff + 上下文)→ {summary, root_cause, blast_radius_files, evidence}。失败 → None。"""
        from hyperion.services.memory.backends.native.extract import _extract_json_object

        model = self.model
        if model is None:  # 无模型 → 降级(run() 已尝试 build,这里兜底)
            return None
        diff_short = self.diff_text[:4000]  # 防 diff 太长爆上下文(截断;根因通常在前部)
        prompt = _PATCH_PROMPT.format(diff=diff_short, context=context or "(无)")
        try:
            msg = model.invoke([{"role": "user", "content": prompt}])
            raw = msg.content if isinstance(msg.content, str) else str(msg.content)
        except Exception as e:  # noqa: BLE001 - LLM 失败降级(不抛,返 None)
            logger.warning("ingest.patch: LLM summarize 失败,降级用 hunk 自动摘要: %s", e)
            return None
        data = _extract_json_object(raw)
        if data is None:
            logger.warning("ingest.patch: 模型回复抠不到 JSON,降级。前 200 字: %s", raw[:200])
        return data

    def _auto_summary(self, hunks: list[dict]) -> str:
        """降级摘要:LLM 没出 summary 时,从 hunk 文件名凑一句。"""
        files = ", ".join(dict.fromkeys(h["file"] for h in hunks if h.get("file"))) or "(未知文件)"
        return f"补丁改动:{files}(LLM 未抽根因,降级)"

    def _assemble_ki(self, hunks: list[dict], files: list[str], extracted: dict | None) -> KnowledgeItem:
        """组装 bug_lesson:fix_patch 装全文;evidence 优先 LLM 给的,没有就用 hunk 的 (file,new_start)。"""
        extracted = extracted or {}
        summary = (extracted.get("summary") or "").strip() or self._auto_summary(hunks)
        root_cause = (extracted.get("root_cause") or "").strip()
        # LLM schema 不守(踩坑 #5):blast_radius_files / evidence 可能不是 list,防御。
        br = extracted.get("blast_radius_files")
        br = br if isinstance(br, list) else files
        ev_raw = extracted.get("evidence")
        ev_raw = ev_raw if isinstance(ev_raw, list) else []

        # evidence:LLM 给的优先(行号经 _coerce_int 防御解析);没有就用每个 hunk 的 (file, new_start)。
        evidence: list[Evidence] = []
        for e in ev_raw:
            if isinstance(e, dict) and e.get("file"):
                evidence.append(Evidence(file=e["file"], line=_coerce_int(e.get("line"))))
        if not evidence:
            for h in hunks:
                if h.get("file"):
                    evidence.append(Evidence(file=h["file"], line=h.get("new_start")))

        # 补丁的"身份"是 diff 本身,**不是 LLM 的总结**(总结每次措辞都变 → 会落不同 id → 重复入库)。
        # 用 diff 内容算稳定 id:同一个 .patch 摄取两次 → 同 id → bayesian 合并(置信度累加、evidence 并集)。
        # 不同但修同一 bug 的两个补丁的"语义近邻去重"是更难的问题,记 backlog(需 embedding 聚类)。
        stable_id = make_id(self.scope, "bug_lesson", self.diff_text or "") if self.diff_text else ""

        return KnowledgeItem(
            id=stable_id,
            kind="bug_lesson", repo=self.repo, scope=self.scope,
            summary=summary[:200], root_cause=root_cause, detail="",
            fix_patch=self.diff_text, blast_radius_files=list(dict.fromkeys(br)),
            evidence=evidence, source=self.source or "patch_ingest",
            source_tier=self.source_tier, commit_sha=self.commit_sha,
            tags=["patch_insight"],
        )


# ──────────────────────────────────────────────────────────────────────────────
# §3 ingest_document:文档入口分发器(report / patch 两路)
# ──────────────────────────────────────────────────────────────────────────────


async def ingest_document(
    path: str | Path,
    *,
    scope: Scope,
    repo: str,
    svc: MemoryService | None = None,
    source_tier: SourceTier = SourceTier.imported,
    commit_sha: str | None = None,
    kind: str = "auto",  # auto | report | patch
    max_chars: int = _DEFAULT_MAX_CHARS,
) -> dict:
    """摄取一个文档文件 → 写入记忆。按扩展名/kind 分流到 report 或 patch 路径。

    返回统计 dict(route/wrote/source + route 各自的细节键),供 CLI 打印 + 测试断言。

    - report 路径(.md/.txt/.pdf):parse_issue 取文本(复用)→ LongDocChunker 切块 →
      每块 svc.memorize_report(extract + memorize)。长报告自动分多次 LLM 调用,不爆上下文。
    - patch 路径(.patch/.diff):PatchIngestPipeline.run() 产 KI → svc.memorize。
      (run() 核心算法窗口展示后填;填实前此路径 raise NotImplementedError。)

    去重天然发生:同根因重复 ingest → make_id 落同 id → bayesian 合并(native memorize 已就位)。
    """
    svc = svc or get_memory_service()
    p = Path(path)
    suffix = p.suffix.lower()
    is_patch = suffix in _PATCH_SUFFIXES
    route = "patch" if (kind == "patch" or (kind == "auto" and is_patch)) else "report"

    if route == "patch":
        diff_text = p.read_text(encoding="utf-8", errors="replace")
        pipeline = PatchIngestPipeline(
            diff_text, repo=repo, scope=scope, source=str(p),
            source_tier=source_tier, commit_sha=commit_sha,
        )
        items = pipeline.run()  # ★ 核心算法(窗口展示后填)
        wrote = await svc.memorize(items, scope) if items else 0
        return {"route": "patch", "wrote": wrote, "items_produced": len(items), "source": str(p)}

    # report 路径:复用现成 loader(.md/.txt/.pdf → 纯文本)。
    doc = parse_issue(p)
    text = (doc.text or "").strip()
    if not text:
        logger.warning("ingest: %s 抽不出文本(扫描件/加密 PDF?),跳过。", p)
        return {"route": "report", "wrote": 0, "chunks": 0, "source": str(p),
                "warn": "文档抽不出文本(扫描件/加密 PDF?改用 .md/.txt)"}

    chunks = LongDocChunker(max_chars=max_chars).split(text)
    wrote = 0
    for ch in chunks:
        # memorize_report = extract_items(LLM 抽 KI)+ memorize_items(嵌向量/连边/合并)。
        wrote += await svc.memorize_report(
            ch, scope, repo=repo, commit_sha=commit_sha, source=str(p), source_tier=source_tier,
        )
    logger.debug("ingest: 报告 %s(%d 字)→ %d 块 → 写入 %d 条", p.name, len(text), len(chunks), wrote)
    return {"route": "report", "wrote": wrote, "chunks": len(chunks), "source": str(p)}
