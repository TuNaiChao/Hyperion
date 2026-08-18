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

切分策略(#58,2026-08)
----------------------
- **超长符号按行区间二次切分(已实现)**:一个符号默认一个 chunk;但巨 C 文件单符号可能 ~300KB
  (driver_nl80211.c),整块超 embedder 输入上限(DashScope 33000 字符)→ 建索引 400。所以超
  ``MAX_CHUNK_CHARS`` 的符号按行区间贪心切成多段 sub-chunk,各带 part/total + 独立 content_hash
  + 真实行号(见 ``_symbol_to_chunks``)。
- **还没做的**:真·AST 子语句级切分(行中间下刀、保语法结构)—— 行区间切分已够避开 embedder
  上限,AST 级是质量优化,留 backlog;单行就超阈值(vendor 头巨宏,极罕见)无法按行再切,原样保留。
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
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from rootrecall.services.code_index.parser import (
    Symbol,
    detect_language,
    iter_source_files,
    parse_file,
    parse_repo,
)

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────
# §1 数据模型
# ──────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CodeChunk:
    """一个可检索的代码块(检索 + 嵌入 + 导航的基本单位)。

    frozen=True 让它可哈希、可作 set / dict key(后续去重、比对方便)。
    """

    id: str  # 稳定主键:f"{file}:{qualified_name}"(超长分段再加 ":p{N}");不含 start_line——含行号对重构太敏感(重排函数顺序→全部 id 变→全量重嵌),行号作普通列。决策 #8
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

# 单个 chunk 的字符数上限(总字符数,含空白)。DashScope text-embedding-v4 单条输入硬上限
# 33000 字符(超了报 400 "Range of input length should be [1, 33000]");embed 还会加元数据头
# (embed.py:expand_chunk_text),留足余量取 16000。超长符号(巨 C 函数 / vendor 头)按行区间
# 二次切到 ≤ 此值,见 _symbol_to_chunks(#58)。值更小 → embedding 信号更聚焦但 chunk 更多。
MAX_CHUNK_CHARS: int = 16000


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


def _chunk_id(file: str, symbol: str, part: int = 1) -> str:
    """生成稳定的 chunk 主键(超长分段时尾部加 :p{N})。

    不含 start_line:含行号对重构太敏感(重排函数顺序→全部 id 变→触发全量重嵌)。
    qualified_name 在同文件内唯一(含 parent.method 消歧)。C 的 struct/函数同名边缘情况
    留 P1.5 C 子调研用 kind 区分。决策 #8。
    """
    base = f"{file}:{symbol}"
    return base if part <= 1 else f"{base}:p{part}"


def _symbol_to_chunk(sym: Symbol, text: str, part: int = 1, total: int = 1) -> CodeChunk:
    """把一个 Symbol(及其切出的代码文本)包成一个 CodeChunk。"""
    return CodeChunk(
        id=_chunk_id(sym.file, sym.qualified_name, part),
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


def _module_chunks(file: str, language: str, lines: list[str], spans: list[tuple[int, int]]) -> list[CodeChunk]:
    """把「不属于任何符号」的代码(import / 全局常量 / 顶层语句 / 无符号的整个头文件)聚成
    module chunk;**超长则按行区间贪心切成多段**(#58:vendor 头如 qca-vendor.h ~300KB、无任何
    被解析的符号 → 整文件落这里,不切会超 embedder 33000 上限报 400)。

    spans 是该文件所有符号的 [start_line, end_line] 区间(1-indexed,闭区间);把这些区间
    覆盖的行挖掉,剩下有内容的行 = 模块级代码。没有就返回 [](整个文件都是符号)。

    切法:kept 行未必连续(跳过了符号覆盖区),按顺序贪心累积到 ≤ MAX_CHUNK_CHARS 封口开新段,
    各段带 part/total + 独立 content_hash + 真实行号(逻辑同 _symbol_to_chunks)。
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
        return []

    module_name = Path(file).as_posix()  # 用相对路径当模块名,够唯一

    # 贪心按行累积成 ≤ MAX_CHUNK_CHARS 的段
    groups: list[list[tuple[int, str]]] = []   # 每段 = [(line_no, line), ...]
    buf: list[tuple[int, str]] = []
    for item in kept:
        if buf and len("\n".join(line for _, line in buf + [item])) > MAX_CHUNK_CHARS:
            groups.append(buf)
            buf = [item]
        else:
            buf.append(item)
    if buf:
        groups.append(buf)

    total = len(groups)
    chunks: list[CodeChunk] = []
    for idx, grp in enumerate(groups, start=1):
        text = "\n".join(line for _, line in grp)
        chunks.append(
            CodeChunk(
                id=_chunk_id(file, "<module>", idx),
                symbol=module_name,
                kind="module",
                file=file,
                language=language,
                start_line=grp[0][0],
                end_line=grp[-1][0],
                text=text,
                content_hash=_sha256(text),
                fts_text=_module_fts_text(text),
                part=idx,
                total=total,
            )
        )
    return chunks


def _symbol_to_chunks(sym: Symbol, lines: list[str], start: int, end: int) -> list[CodeChunk]:
    """把一个符号切成 1 个或多个 CodeChunk(超长符号按行区间二次切分,#58)。

    为什么:#58 前一个符号 = 一个 chunk,巨 C 文件(driver_nl80211.c 单符号 ~300KB)整块
    超过 embedder 输入上限(DashScope 33000 字符)→ 建索引报 400。这里按行区间把超长符号
    切成 ≤ MAX_CHUNK_CHARS 的 sub-chunk,各带 part/total + 独立 content_hash + 真实行号。

    切法(贪心):从符号首行起逐行累积,加入下一行会超 MAX_CHUNK_CHARS 就先封口开新段。
    不超阈值 → 仍是整块一个 chunk(沿用原行为,绝大多数函数走这条)。
    单行本身就超阈值(极罕见,如 vendor 头里的巨宏)无法按行再切,该段原样保留(记 backlog)。
    """
    body = lines[start - 1 : end]  # 该符号的代码行(0-indexed 切片 ↔ 1-indexed [start, end])
    full_text = "\n".join(body)

    # 不超阈值:整块一个 chunk(沿用原行为)
    if len(full_text) <= MAX_CHUNK_CHARS:
        return [_symbol_to_chunk(sym, full_text)]

    # 超阈值:贪心按行累积,凑到不超 MAX_CHUNK_CHARS 就切一刀
    parts: list[str] = []                # 各 sub-chunk 文本
    ranges: list[tuple[int, int]] = []   # 各 sub-chunk 真实行号 [s,e](1-indexed)
    buf: list[str] = []                  # 当前累积段
    buf_start = start                    # 当前段起始行号
    for offset, line in enumerate(body):
        line_no = start + offset
        # buf 非空且加入这行会超阈值 → 先封口,buf 重开为这行
        if buf and len("\n".join(buf + [line])) > MAX_CHUNK_CHARS:
            parts.append("\n".join(buf))
            ranges.append((buf_start, line_no - 1))
            buf = [line]
            buf_start = line_no
        else:
            buf.append(line)
    if buf:  # 收尾封口
        parts.append("\n".join(buf))
        ranges.append((buf_start, end))

    total = len(parts)
    return [
        CodeChunk(
            id=_chunk_id(sym.file, sym.qualified_name, idx),
            symbol=sym.qualified_name,
            kind=sym.kind,
            file=sym.file,
            language=sym.language,
            start_line=s,
            end_line=e,
            text=text,
            content_hash=_sha256(text),
            fts_text=_build_fts_text(sym, text),
            part=idx,
            total=total,
        )
        for idx, (text, (s, e)) in enumerate(zip(parts, ranges, strict=True), start=1)
    ]


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
        # #58:超长符号(巨 C 函数 / vendor 头)按行区间二次切分,否则整块超 embedder 上限报 400
        for chunk in _symbol_to_chunks(sym, lines, start, end):
            chunks.append(chunk)
        spans.append((start, end))


    # 模块级兜底 chunk(含纯模块文件:symbols 为空时整文件都进这里);超长按行区间再切(#58)
    chunks.extend(_module_chunks(file, language, lines, spans))

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

    # 护栏:同批出现重复 chunk id(同文件同名符号,C 的 struct/函数同名边缘)会炸增量
    # upsert(LanceDB 拒绝同批多行撞同一目标行),全量路径也会静默写出重复行。
    # id 是主键,表里本来只能活一条:保留首见 + 警告,把静默腐蚀变成有日志的丢弃。
    seen: set[str] = set()
    deduped: list[CodeChunk] = []
    for c in chunks:
        if c.id in seen:
            logger.warning("chunk id 重复,丢弃后见者: %s(parser 限定名撞车,查该文件的同类符号)", c.id)
            continue
        seen.add(c.id)
        deduped.append(c)
    return deduped
