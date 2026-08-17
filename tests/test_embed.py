"""P1.2 embed.py 离线测试(不依赖网络 / 模型下载)。

覆盖不调真模型的部分:expand_chunk_text 拼头、_normalize 归一化、create_embedder 工厂分支。
真调用(远端 DashScope / 本地 sentence-transformers)的实测靠手动验证,不放单测(花钱 + 慢)。
"""

from __future__ import annotations

import numpy as np
import pytest

from rootrecall.services.code_index.chunker import CodeChunk
from rootrecall.services.code_index.embed import (
    DEFAULT_BASE_URL,
    DEFAULT_REMOTE_MODEL,
    RemoteEmbedder,
    _normalize,
    create_embedder,
    expand_chunk_text,
)


def _chunk(language: str = "python", symbol: str = "foo", kind: str = "function") -> CodeChunk:
    """造一个测试用 chunk(字段随便填够,只测 embed 层逻辑,不碰真实代码)。"""
    is_py = language == "python"
    return CodeChunk(
        id=f"f:x.{language}:1",
        symbol=symbol,
        kind=kind,
        file=f"src/x.{language}",
        language=language,
        start_line=1,
        end_line=2,
        text=f"def {symbol}(): pass" if is_py else f"void {symbol}() {{}}",
        content_hash="h",
        fts_text=symbol,
    )


# ── expand_chunk_text ──────────────────────────────────────────────────────


def test_expand_chunk_python_uses_hash_comment():
    out = expand_chunk_text(_chunk("python", "disconnect_cb"))
    assert out.startswith("# file: src/x.python · symbol: disconnect_cb · kind: function · lang: python")
    assert "def disconnect_cb(): pass" in out


def test_expand_chunk_c_uses_slash_comment():
    out = expand_chunk_text(_chunk("c", "init_adapter"))
    assert out.startswith("// file: src/x.c · symbol: init_adapter · kind: function · lang: c")


def test_expand_chunk_unknown_lang_falls_back_to_hash():
    # 未注册的语言(如 rust)兜底用 #
    out = expand_chunk_text(_chunk("rust", "main"))
    assert out.startswith("# ")


# ── _normalize ─────────────────────────────────────────────────────────────


def test_normalize_1d_unit_length():
    nv = _normalize(np.array([3.0, 4.0], dtype=np.float32))
    assert np.isclose(np.linalg.norm(nv), 1.0)
    assert np.allclose(nv, [0.6, 0.8])


def test_normalize_2d_each_row_unit():
    m = np.array([[3.0, 4.0], [1.0, 0.0]], dtype=np.float32)
    nm = _normalize(m)
    assert np.allclose(np.linalg.norm(nm, axis=1), 1.0)


def test_normalize_zero_vector_does_not_explode():
    # 全零向量不应除零崩溃(原样返回,不出 NaN)
    nv = _normalize(np.array([0.0, 0.0], dtype=np.float32))
    assert not np.isnan(nv).any()


# ── create_embedder 工厂 ───────────────────────────────────────────────────


def test_factory_remote_from_dict():
    emb = create_embedder(
        {
            "provider": "openai_compatible",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "sk-fake",
            "model": "text-embedding-v4",
            "dimensions": 1024,
        }
    )
    assert isinstance(emb, RemoteEmbedder)
    assert emb._model == "text-embedding-v4"  # 白盒:确认配置传到位
    assert emb._dimensions == 1024


def test_factory_remote_uses_defaults_when_minimal():
    emb = create_embedder({"provider": "openai_compatible", "api_key": "sk-fake"})
    assert isinstance(emb, RemoteEmbedder)
    assert emb._base_url == DEFAULT_BASE_URL
    assert emb._model == DEFAULT_REMOTE_MODEL


def test_factory_remote_without_apikey_raises():
    with pytest.raises(ValueError, match="api_key"):
        create_embedder({"provider": "openai_compatible", "base_url": "x", "api_key": ""})


def test_factory_unknown_provider_raises():
    with pytest.raises(ValueError, match="未知 embedding provider"):
        create_embedder({"provider": "milvus", "api_key": "x"})
