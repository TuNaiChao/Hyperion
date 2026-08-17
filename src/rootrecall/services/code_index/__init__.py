"""代码理解服务:tree-sitter 解析 → 切块 → 嵌入 → 检索(P1 分阶段搭建)。

本包是三条工作流共享的「代码理解地基」。P1 分子阶段:
  P1.0 parser      符号抽取
  P1.1 chunker     符号边界切块 + fts_text 分词
  P1.2 embed       embedding 向量化(provider 抽象:远端 OpenAI 兼容默认 / 本地可选)
  P1.3 store       LanceDB 混合检索(BM25 + 向量 + RRF)
  P1.4 code_nav    grep_symbol / read_function / search_code 工具
  P1.5 code_graph  caller/callee 图(延后)
"""

from rootrecall.services.code_index.chunker import (
    CodeChunk,
    chunk_file,
    chunk_repo,
    split_identifier,
)
from rootrecall.services.code_index.embed import (
    DEFAULT_BASE_URL,
    DEFAULT_LOCAL_MODEL,
    DEFAULT_REMOTE_MODEL,
    Embedder,
    LocalEmbedder,
    RemoteEmbedder,
    create_embedder,
    expand_chunk_text,
)
from rootrecall.services.code_index.parser import (
    GRAMMARS,
    LanguageGrammar,
    Symbol,
    detect_language,
    iter_source_files,
    parse_file,
    parse_repo,
)

__all__ = [
    # parser(P1.0)
    "GRAMMARS",
    "LanguageGrammar",
    "Symbol",
    "detect_language",
    "iter_source_files",
    "parse_file",
    "parse_repo",
    # chunker(P1.1)
    "CodeChunk",
    "chunk_file",
    "chunk_repo",
    "split_identifier",
    # embed(P1.2)
    "DEFAULT_BASE_URL",
    "DEFAULT_LOCAL_MODEL",
    "DEFAULT_REMOTE_MODEL",
    "Embedder",
    "LocalEmbedder",
    "RemoteEmbedder",
    "create_embedder",
    "expand_chunk_text",
]
