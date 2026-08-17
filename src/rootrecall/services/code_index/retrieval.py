"""代码理解服务 · 检索层(P1.3 retrieval.py)。

这一层干什么
------------
用户给一句自然语言查询(如"蓝牙断连处理"),这层负责:
  query → embedder 算向量 → store.hybrid_search(BM25+向量+RRF 取 top-50 候选)
       → reranker cross-encoder 把候选重排到 top-5 → 返回最相关代码块。
两阶段架构(bi-encoder 召回 + cross-encoder 精排)是十年工业标准,reranker 典型增益 +20~48%。

reranker provider 抽象(镜像 embed.py)
--------------------------------------
- RemoteReranker:默认。两种请求形态:
    - "dashscope":阿里云百炼原生(嵌套 input/parameters,live 测确认 qwen3-rerank,与 embedding 同 key)。
    - "cohere":SiliconFlow/Jina/Cohere 扁平形态(query/documents/top_n)。SiliconFlow BAAI/bge-reranker-v2-m3 免费。
- LocalReranker:本地 sentence-transformers CrossEncoder(可选,需 uv sync --extra embedding-local;CPU 慢,仅 GPU)。
- create_reranker(config):按 config.reranker.provider 选实现。

为什么默认远端:bge-reranker-v2-m3 在 CPU 上 257s/100doc,交互式 agent 不可用;DashScope qwen3-rerank
同 key、同价(¥0.0005/千token,≈8万查询=¥1)、100+ 语言,是最优默认。

还没做(P1.3 范围外,记 backlog / 设计 §6.3)
----------------------------------------------
- 查询类型 boosting(PascalCase→Class、snake→Function、dotted→qualified):借 CRG,eval 不达标再加。
- 三级降级(hybrid→仅 FTS→仅向量→keyword):借 CRG,目前 hybrid 由 LanceDB 原生兜底,reranker 失败时降级用 hybrid 顺序。
- provider 硬化(retryable 指数退避):backlog #12(基础版已含 index 校验 + UA + 4xx body 透传)。

对外提供
--------
- Reranker(Protocol)、RemoteReranker、LocalReranker、create_reranker。
- retrieve(...):一次完整检索,返回 RetrievalResult(hits + out_mode)。
- RetrievalHit / RetrievalResult:结果数据结构。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from rootrecall.services.code_index.embed import Embedder
from rootrecall.services.code_index.store import VectorStore

logger = logging.getLogger(__name__)

# 远端 rerank 默认 UA(规避某些网关 403 拒 Python-urllib;借 CRG embeddings.py)
_UA = "rootrecall/0.1 (+https://github.com/TuNaiChao/RootRecall)"


# ──────────────────────────────────────────────────────────────────────────
# §1 结果数据结构
# ──────────────────────────────────────────────────────────────────────────


@dataclass
class RetrievalHit:
    """一条检索结果(chunk 定位字段 + 最终得分 + 额外透传字段)。"""

    id: str
    symbol: str
    kind: str
    file: str
    start_line: int
    end_line: int
    text: str
    score: float  # reranker 分(无 reranker 时是 RRF _relevance_score)
    extra: dict[str, Any] = field(default_factory=dict)  # language/fts_text/content_hash 等按需透传


@dataclass
class RetrievalResult:
    """一次检索的返回:hits + out_mode(走了哪条路,可观测,借 CRG _out_mode)。"""

    hits: list[RetrievalHit]
    out_mode: str  # "hybrid+rerank" | "hybrid" | "rerank-failed:hybrid" | "empty"


# ──────────────────────────────────────────────────────────────────────────
# §2 Reranker 接口 + 远端/本地实现
# ──────────────────────────────────────────────────────────────────────────


class Reranker(Protocol):
    """重排器接口。RemoteReranker / LocalReranker 都实现它。"""

    def rerank(self, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
        """对 documents 按 query 相关性重排,返回 [(原 documents 下标, 分数)] 取 top_n,降序。"""
        ...


class RemoteReranker:
    """远端 cross-encoder reranker。两种请求形态:dashscope(嵌套 input/parameters)/ cohere(扁平)。

    用 httpx 直连——rerank 不是 OpenAI 标准接口(openai 库没有 rerank 方法),各家自有端点。
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        shape: str = "dashscope",
        top_n: int = 5,
        timeout: float = 60.0,
    ):
        if not api_key:
            raise ValueError("远端 reranker 需要 api_key(如 DASHSCOPE_API_KEY / SILICONFLOW_API_KEY)。")
        if shape not in ("dashscope", "cohere"):
            raise ValueError(f"未知 rerank shape: {shape}(支持 dashscope / cohere)")
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._shape = shape
        self._top_n = top_n
        self._timeout = timeout

    def _build_body(self, query: str, documents: list[str], top_n: int) -> dict:
        if self._shape == "dashscope":
            return {
                "model": self._model,
                "input": {"query": query, "documents": documents},
                "parameters": {"top_n": top_n, "return_documents": False},
            }
        # cohere 扁平形态
        return {"model": self._model, "query": query, "documents": documents, "top_n": top_n, "return_documents": False}

    def _parse_results(self, data: dict) -> list[dict]:
        if self._shape == "dashscope":
            return data.get("output", {}).get("results", [])
        return data.get("results") or data.get("data") or []

    def rerank(self, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
        if not documents:
            return []
        import httpx  # 局部导入:没装 httpx 的环境也能 import 本模块的纯逻辑

        body = self._build_body(query, documents, top_n)
        resp = httpx.post(
            self._base_url,
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json", "User-Agent": _UA},
            json=body,
            timeout=self._timeout,
        )
        if resp.status_code >= 400:
            # 4xx/5xx 透传 body 真实原因(借 CRG:别只报 "400 Bad Request")
            raise RuntimeError(f"rerank HTTP {resp.status_code}: {resp.text[:300]}")
        results = self._parse_results(resp.json())
        # 响应 index 校验(借 CRG embeddings.py:某些网关乱序/丢项)——必须带 index + 分数、范围合法、不重复
        out: list[tuple[int, float]] = []
        seen: set[int] = set()
        for r in results:
            idx = r.get("index")
            score = r.get("relevance_score")
            if score is None:
                score = r.get("score")
            if idx is None or score is None or idx in seen or not (0 <= idx < len(documents)):
                continue
            seen.add(idx)
            out.append((int(idx), float(score)))
        out.sort(key=lambda x: x[1], reverse=True)
        return out[:top_n]


class LocalReranker:
    """本地 cross-encoder(sentence-transformers)。需 `uv sync --extra embedding-local`。CPU 慢,仅 GPU 可交互。"""

    def __init__(self, *, model: str = "BAAI/bge-reranker-v2-m3", device: str | None = None, hf_endpoint: str | None = "https://hf-mirror.com"):
        try:
            from sentence_transformers import CrossEncoder  # pyright: ignore[reportMissingImports]
        except ImportError as e:
            raise ImportError("本地 reranker 需要 sentence-transformers。装它:uv sync --extra embedding-local") from e
        if hf_endpoint:
            os.environ["HF_ENDPOINT"] = hf_endpoint  # 国内下载排雷
        self._model = CrossEncoder(model, device=device)

    def rerank(self, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
        if not documents:
            return []
        scores = self._model.predict([(query, d) for d in documents])
        order = sorted(range(len(documents)), key=lambda i: float(scores[i]), reverse=True)
        return [(i, float(scores[i])) for i in order[:top_n]]


def create_reranker(cfg) -> Reranker | None:
    """按 RerankerConfig(或 dict)的 provider 选实现。provider="off"/缺失 → None(不重排)。

    cfg 通常来自 get_app_config().code_index.reranker(pydantic extra="allow" 保留为 dict);也接受 dict。
    """
    _SHAPES = {"dashscope": ("dashscope", "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank", "qwen3-rerank"),
               "siliconflow": ("cohere", "https://api.siliconflow.cn/v1/rerank", "BAAI/bge-reranker-v2-m3")}

    def get(k, default=None):
        return cfg.get(k, default) if isinstance(cfg, dict) else getattr(cfg, k, default)

    provider = (get("provider") or "off").lower()
    if provider in ("off", "none", ""):
        return None
    if provider in _SHAPES:
        shape, default_url, default_model = _SHAPES[provider]
        return RemoteReranker(
            base_url=get("base_url") or default_url,
            api_key=get("api_key") or "",
            model=get("model", default_model),
            shape=shape,
            top_n=get("rerank_top_n", 5),
        )
    if provider in ("cohere", "jina"):
        return RemoteReranker(base_url=get("base_url") or "", api_key=get("api_key") or "", model=get("model") or "", shape="cohere", top_n=get("rerank_top_n", 5))
    if provider == "sentence_transformers":
        return LocalReranker(model=get("model", "BAAI/bge-reranker-v2-m3"), device=get("device"), hf_endpoint=get("hf_endpoint", "https://hf-mirror.com"))
    raise ValueError(f"未知 reranker provider: {provider}(支持:dashscope / siliconflow / cohere / sentence_transformers / off)")


# ──────────────────────────────────────────────────────────────────────────
# §3 retrieve:一次完整检索
# ──────────────────────────────────────────────────────────────────────────


def _to_hit(row: dict, score: float) -> RetrievalHit:
    """store.hybrid_search 返回的 dict → RetrievalHit(把 vector 等大字段挡在 extra 外)。"""
    keep_out = {"id", "symbol", "kind", "file", "start_line", "end_line", "text", "vector", "_relevance_score"}
    return RetrievalHit(
        id=row.get("id", ""),
        symbol=row.get("symbol", ""),
        kind=row.get("kind", ""),
        file=row.get("file", ""),
        start_line=int(row.get("start_line") or 0),
        end_line=int(row.get("end_line") or 0),
        text=row.get("text", ""),
        score=score,
        extra={k: v for k, v in row.items() if k not in keep_out},
    )


def retrieve(
    query: str,
    repo: str,
    embedder: Embedder,
    store: VectorStore,
    reranker: Reranker | None = None,
    *,
    top_k: int = 5,
    candidate_top_n: int = 50,
    where: str | None = None,
) -> RetrievalResult:
    """混合检索 + 重排:query → 向量 → hybrid(BM25+向量+RRF)取候选 → cross-encoder 重排 → top_k。

    返回 RetrievalResult(hits, out_mode)。out_mode 可观测:
      hybrid+rerank(正常)/ hybrid(无 reranker)/ rerank-failed:hybrid(reranker 报错降级)/ empty(无候选)。
    """
    # Stage 1:hybrid 召回(LanceDB 原生 BM25 + 向量 + RRF)
    qvec = embedder.embed_query(query)
    candidates = store.hybrid_search(repo, qvec, query, limit=candidate_top_n, where=where)
    if not candidates:
        return RetrievalResult([], "empty")

    # 无 reranker:直接按 RRF 分取 top_k
    if reranker is None:
        return RetrievalResult([_to_hit(c, float(c.get("_relevance_score", 0.0))) for c in candidates[:top_k]], "hybrid")

    # Stage 2:cross-encoder 重排(对候选的 fts_text 打分——短、装得下上下文,且语义信号比全文体更聚焦)
    docs = [c.get("fts_text") or c.get("text", "") for c in candidates]
    try:
        ranked = reranker.rerank(query, docs, top_n=top_k)
    except Exception as e:
        logger.warning("reranker 失败,降级用 hybrid 顺序: %s", e)
        return RetrievalResult([_to_hit(c, float(c.get("_relevance_score", 0.0))) for c in candidates[:top_k]], "rerank-failed:hybrid")

    hits = [_to_hit(candidates[idx], score) for idx, score in ranked]
    return RetrievalResult(hits, "hybrid+rerank")