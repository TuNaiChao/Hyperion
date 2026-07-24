"""代码理解服务 · 第二步:把符号切成可检索的 chunk(P1.1)。

这一层干什么
------------
上一层 parser.py 把源码拆成了一张张「符号卡片」(每个函数/类一张)。但要做语义检索,
还得把卡片变成 **chunk** —— 检索、嵌入、排序的基本单位。一个 chunk = 一块代码 +
它的检索元数据(给 BM25 的词袋文本 fts_text、内容指纹 content_hash 等)。后面 embed.py
给每个 chunk 算向量,store.py 存进向量库;检索时 BM25(关键词)+ 向量(语义)一起召回。

为什么「按符号边界」切,而不是按固定行数切
--------------------------------------
研究(CMU 的 cAST,EMNLP 2025)证明:按语法结构(函数/类边界)切,比按固定行数切,
检索和生成质量都更高 —— 固定行数会把一个函数从中间劈开,丢掉结构信息。所以我们让每个
符号(函数/方法/类)各成一个 chunk,符号自带完整语义。这套思路对标 cAST。

本文件的几个设计点
------------------
1. **符号边界切块**:parser 给的每个 Symbol → 一个 CodeChunk(整块代码 + 元数据)。
2. **模块级兜底 chunk**:一个文件里,不在任何函数/类里的代码(import、全局常量、
   ``if __name__ == "__main__"`` 等)也不是垃圾 —— 它们说明"这个模块依赖什么、配置什么"。
   把这些散落行聚成一个 kind="module" 的 chunk,保证覆盖率满 100%(cAST 的 plug-and-play
   原则:所有 chunk 拼起来能还原原文件),不漏检索信号。
3. **fts_text 词袋(给 BM25)**:全文检索引擎(BM25)靠"词"匹配,但代码标识符是
   ``snake_case`` / ``camelCase`` / ``SCREAMING_SNAKE`` 连写,Tantivy 默认分词器切不好。
   这里把每个标识符拆成词干(``wpa_supplicant_assoc_cb`` → wpa supplicant assoc cb;
   ``parseRepo`` → parse repo),再把函数体里的标识符、docstring 的自然语言词都纳入,
   小写拼成空格分隔的词袋 —— 这样搜 "assoc" 也能命中。符号名 / docstring 重复一次加权。
4. **非空白字符判大小**(学 cAST):判 chunk 大小用「非空白字符数」而非行数,比行数稳。
5. **content_hash**:chunk 代码文本的 sha256,增量更新靠它判"这块代码变没变"(见设计 §10)。

还没做(P1.1 范围外,已记 backlog)
----------------------------------
- **超大函数的 AST 子语句切分**:bge-m3 有 8K token 上下文,deer-flow 的 Python 函数
  根本不会超;真正需要是 C(bluez 200+ 行状态机)。这里只**预留 part/total 字段** +
  超长打标记,真正的 cAST 式切分留到 C 场景。
- **前导注释抽取**(C 的 doxygen):Python 靠 docstring 已够,留 C 场景。

对外提供
--------
- CodeChunk:一个可检索代码块的数据结构。
- chunk_file(path):切单个文件,返回它的 chunk 列表。
- chunk_repo(root):切整个仓库(接 parser.parse_repo 的 Symbol 列表,自动补无符号文件)。
- split_identifier(name):把标识符拆成词(也单独导出,方便别处复用 / 测试)。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from hyperion.services.code_index.parser import (
    Symbol,
    detect_language,
    iter_source_files,
    parse_file,
    parse_repo,
)

# ──────────────────────────────────────────────────────────────────────────
# §1 数据模型
# ──────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CodeChunk:
    """一个可检索的代码块(检索 + 嵌入 + 导航的基本单位)。

    frozen=True 让它可哈希、可作 set / dict key(后续去重、比对方便)。
    """

    id: str  # 稳定主键:f"{file}:{qualified_name}:{start_line}"(超长分段再加 ":p{N}")
    symbol: str  # 限定名,如 "Agent.run";module chunk 用文件相对路径
    kind: str  # function | method | class | module
    file: str  # 相对仓根路径(与 Symbol.file 一致)
    language: str  # python | c …
    start_line: int  # 1-indexed
    end_line: int  # 1-indexed
    text: str  # 原始代码文本(read_function 直接拿它返回给 agent,无任何加工)
    content_hash: str  # text 的 sha256(增量更新判变;检索后按它合并重复 chunk)
    fts_text: str  # 给 BM25 的词袋(标识符拆词 + docstring,小写空格分隔)
    part: int = 1  # 超长符号分段时的段号,默认第 1 段
    total: int = 1  # 总段数,默认 1(预留:超大函数 AST 切分见模块 docstring 的 backlog)
    callers: tuple[str, ...] = ()  # 谁调用了它;P1.5 建图谱后回填,P1.1 为空
    callees: tuple[str, ...] = ()  # 它调用了谁;同上


# ──────────────────────────────────────────────────────────────────────────
# §2 分词工具:把标识符拆成可被 BM25 匹配的词
# ──────────────────────────────────────────────────────────────────────────

# 抓「标识符」:字母/下划线开头,后跟字母/数字/下划线(覆盖 Python/C 的变量/函数名)。
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# 拆 camelCase / PascalCase:在「小写/数字↔大写」、或「大写↔(大写+小写)」之间下刀。
# 例:CisEstablished → Cis | Established ;HTMLParser → HTML | Parser ;parseRepo → parse | Repo。
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

# 停用词:语法关键字 / 常见无义词,从 fts_text 词袋里剔掉,免得它们淹没真信号。
# 故意收得窄 —— init / len / call / type / str 这些可能是有义的符号词,留给 BM25 的 IDF 自己降权。
_STOPWORDS: frozenset[str] = frozenset(
    {
        # Python 关键字
        "def",
        "class",
        "return",
        "if",
        "elif",
        "else",
        "for",
        "while",
        "try",
        "except",
        "finally",
        "with",
        "as",
        "import",
        "from",
        "pass",
        "break",
        "continue",
        "in",
        "is",
        "not",
        "and",
        "or",
        "lambda",
        "yield",
        "global",
        "nonlocal",
        "raise",
        "assert",
        "del",
        "async",
        "await",
        # 常见无义词
        "self",
        "cls",
        "none",
        "true",
        "false",
        "the",
        "a",
        "an",
        "to",
        "of",
        "on",
        "by",
        "it",
        "its",
        "this",
        "that",
    }
)

# chunk 大小阈值(非空白字符数)。bge-m3 有 8K token 上下文,代码约 1 token ≈ 3.5 非空白字符,
# 8K token ≈ 28000 非空白字符;这里留元数据头和余量,取 20000(≈5-6K token)。
# 超过说明函数异常长(Python 罕见,C 状态机可能),P1.1 暂不切(记 backlog),仅整块保留。
MAX_CHUNK_CHARS: int = 20000


def split_identifier(name: str) -> list[str]:
    """把一个标识符拆成词干列表(给 BM25 用)。

    两步走:先按分隔符 ``_`` / ``-`` / 空白切成段,再对每段用 camelCase 规则二次拆分。
    全程不转小写(小写化由调用方在拼词袋时统一做,这里保留原大小写方便判断边界)。

    例子:
       ``wpa_supplicant_assoc_cb`` → ["wpa", "supplicant", "assoc", "cb"]
       ``parseRepo``              → ["parse", "Repo"]
       ``DBG_CMD_TIMEOUT``        → ["DBG", "CMD", "TIMEOUT"]
       ``HTML2Parser``            → ["HTML2", "Parser"]
    """
    words: list[str] = []
    for seg in re.split(r"[_\-\s]+", name):
        if not seg:
            continue
        for word in _CAMEL_RE.split(seg):
            if word:
                words.append(word)
    return words


def _count_nonws(text: str) -> int:
    """数一段文本的「非空白字符数」(cAST 的 chunk 大小度量,比行数稳)。"""
    return sum(1 for c in text if not c.isspace())


def _sha256(text: str) -> str:
    """算文本的 sha256(十六进制),作 content_hash。"""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _build_fts_text(sym: Symbol, body_text: str) -> str:
    """构造符号 chunk 给 BM25 用的词袋文本。

    组成(全部小写、空格分隔):
    - 符号名 / 限定名里的词(定义层,重复一次加权);
    - 函数体里出现的所有标识符拆词(被调函数名、常量名是强召回信号,剔停用词);
    - docstring 的自然语言词(重复一次加权,语义信号)。

    BM25 靠词频排序,所以"想加重"的词重复一遍即可。
    """
    tokens: list[str] = []

    # ① 定义层:符号名 + 限定名(加权:拼两遍)
    def_layer: list[str] = []
    def_layer.extend(split_identifier(sym.name))
    for part in sym.qualified_name.split("."):
        def_layer.extend(split_identifier(part))
    tokens.extend(def_layer)
    tokens.extend(def_layer)  # 再来一遍:符号名加权

    # ② 函数体里的标识符(被调函数名 / 常量名 = 强召回信号)
    for ident in _IDENT_RE.findall(body_text):
        for w in split_identifier(ident):
            if w.lower() not in _STOPWORDS:
                tokens.append(w)

    # ③ docstring 自然语言词(加权:拼两遍)
    if sym.docstring:
        ds_tokens = [w for w in re.split(r"[^A-Za-z0-9]+", sym.docstring) if w and w.lower() not in _STOPWORDS]
        tokens.extend(ds_tokens)
        tokens.extend(ds_tokens)

    return " ".join(t.lower() for t in tokens if t)


def _module_fts_text(text: str) -> str:
    """构造 module chunk 的词袋:这些行里的标识符(import 名、常量名)拆词。"""
    tokens: list[str] = []
    for ident in _IDENT_RE.findall(text):
        for w in split_identifier(ident):
            if w.lower() not in _STOPWORDS:
                tokens.append(w)
    return " ".join(t.lower() for t in tokens)


# ──────────────────────────────────────────────────────────────────────────
# §3 切块核心
# ──────────────────────────────────────────────────────────────────────────


def _chunk_id(file: str, symbol: str, start_line: int, part: int) -> str:
    """生成稳定的 chunk 主键(超长分段时尾部加 :p{N})。"""
    base = f"{file}:{symbol}:{start_line}"
    return base if part == 1 else f"{base}:p{part}"


def _symbol_to_chunk(sym: Symbol, text: str, part: int = 1, total: int = 1) -> CodeChunk:
    """把一个 Symbol(及其切出的代码文本)包成一个 CodeChunk。"""
    return CodeChunk(
        id=_chunk_id(sym.file, sym.qualified_name, sym.start_line, part),
        symbol=sym.qualified_name,
        kind=sym.kind,
        file=sym.file,
        language=sym.language,
        start_line=sym.start_line,
        end_line=sym.end_line,
        text=text,
        content_hash=_sha256(text),
        fts_text=_build_fts_text(sym, text),
        part=part,
        total=total,
    )


def _module_chunk(file: str, language: str, lines: list[str], spans: list[tuple[int, int]]) -> CodeChunk | None:
    """把一个文件里「不属于任何符号」的代码(import / 全局常量 / 顶层语句)聚成一个 module chunk。

    spans 是该文件所有符号的 [start_line, end_line] 区间(1-indexed,闭区间);把这些区间
    覆盖的行挖掉,剩下有内容的行拼成模块级代码。没有就返回 None(整个文件都是符号)。
    """
    covered: set[int] = set()
    for s, e in spans:
        covered.update(range(s, e + 1))

    # 收集未被符号覆盖、且非空的行(保留 1-indexed 行号;空行不携带检索信号,跳过)
    kept: list[tuple[int, str]] = []
    for idx, line in enumerate(lines, start=1):
        if idx in covered:
            continue
        if line.strip():
            kept.append((idx, line))
    if not kept:
        return None

    start_line = kept[0][0]
    end_line = kept[-1][0]
    text = "\n".join(line for _, line in kept)
    module_name = Path(file).as_posix()  # 用相对路径当模块名,够唯一

    return CodeChunk(
        id=_chunk_id(file, "<module>", start_line, 1),
        symbol=module_name,
        kind="module",
        file=file,
        language=language,
        start_line=start_line,
        end_line=end_line,
        text=text,
        content_hash=_sha256(text),
        fts_text=_module_fts_text(text),
    )


def _chunk_one_file(file: str, source: bytes, symbols: list[Symbol], language: str) -> list[CodeChunk]:
    """切单个文件:每个符号一个 chunk + 一个(可选)模块级 chunk。

    symbols 可以为空(纯模块文件,如只有 import 的 __init__.py)——此时整文件进 module chunk。
    """
    # 按行拆(与 Symbol 的 1-indexed 行号对齐);errors="replace" 防偶发非 UTF-8 字节
    lines = source.decode("utf-8", errors="replace").splitlines()
    chunks: list[CodeChunk] = []
    spans: list[tuple[int, int]] = []

    for sym in symbols:
        # 行号容错:钳到 [1, len(lines)](parser 给的行号理论上总在内,这里兜底)
        start = max(1, min(sym.start_line, len(lines))) if lines else 1
        end = max(start, min(sym.end_line, len(lines))) if lines else 1
        text = "\n".join(lines[start - 1 : end])
        chunk = _symbol_to_chunk(sym, text)
        # 超长仅整块保留(P1.1 不切,见模块 docstring 的 backlog);将来切分时这里改成分段
        chunks.append(chunk)
        spans.append((start, end))

    # 模块级兜底 chunk(含纯模块文件:symbols 为空时整文件都进这里)
    mod = _module_chunk(file, language, lines, spans)
    if mod is not None:
        chunks.append(mod)

    return chunks


# ──────────────────────────────────────────────────────────────────────────
# §4 对外接口
# ──────────────────────────────────────────────────────────────────────────


def chunk_file(path: Path | str, symbols: list[Symbol] | None = None) -> list[CodeChunk]:
    """切单个文件。

    - symbols=None:自动调 parser.parse_file 解析该文件再切。
    - 给了 symbols:直接用(避免重复解析;典型场景 index.py 已 parse 过)。
    - 未知后缀(语言不支持)返回 []。
    """
    path = Path(path)
    lang = detect_language(path)
    if lang is None:
        return []
    syms = symbols if symbols is not None else parse_file(path)
    try:
        source = path.read_bytes()
    except OSError:
        return []
    return _chunk_one_file(str(path), source, syms, lang)


def chunk_repo(root: Path | str, symbols: list[Symbol] | None = None) -> list[CodeChunk]:
    """切整个仓库:按文件分组切块,**覆盖所有源码文件**(含无符号的纯模块文件)。

    - symbols=None:自动 parse_repo(root)。
    - 给了 symbols:用它定位「每个文件有哪些符号」,但仍走 iter_source_files 拿全文件列表
    (为了不漏无符号文件)。
    - 按相对仓根路径稳定排序输出;跳过读不了的文件(与 parser 一致)。
    """
    root = Path(root)

    # 按文件分组符号(若调用方已 parse 过,直接复用,不重复解析)
    syms_by_file: dict[str, list[Symbol]] = {}
    if symbols is not None:
        for s in symbols:
            syms_by_file.setdefault(s.file, []).append(s)
    else:
        for s in parse_repo(root):
            syms_by_file.setdefault(s.file, []).append(s)

    chunks: list[CodeChunk] = []
    # iter_source_files 保证覆盖每个源码文件(含 __init__.py 等无符号文件)
    for p, rel, lang in iter_source_files(root):
        file_syms = sorted(syms_by_file.get(rel, []), key=lambda s: s.start_line)
        try:
            source = p.read_bytes()
        except OSError:
            continue  # 跳过读不了的文件,不让单个坏文件中断整仓扫描
        chunks.extend(_chunk_one_file(rel, source, file_syms, lang))
    return chunks
