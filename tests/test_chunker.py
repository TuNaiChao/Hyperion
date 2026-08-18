"""#58 chunker 超长符号切分测试(离线,不依赖网络 / 模型)。

直接测 _symbol_to_chunks 的切分逻辑(核心算法)+ _chunk_one_file 的接线(循环换没换对)。
真建索引(远端 embedder)的回归靠手动 `uv run rootrecall index ... --force`(花钱 + 慢),不放单测。
另含:parser 作用域限定名(函数局部符号)+ chunk_repo 撞 id 去重护栏(真 bug 回归,2026-08-18)。
"""

from __future__ import annotations

from rootrecall.services.code_index.chunker import (
    MAX_CHUNK_CHARS,
    _chunk_one_file,
    _symbol_to_chunks,
    chunk_repo,
)
from rootrecall.services.code_index.parser import GRAMMARS, Symbol, _parse_bytes


def _sym(
    start: int,
    end: int,
    *,
    qname: str = "big_fn",
    kind: str = "function",
    language: str = "c",
    file: str = "big.c",
) -> Symbol:
    """造一个测试用 Symbol(字段填够,只测切分逻辑,不碰真实 tree-sitter 解析)。"""
    return Symbol(
        name=qname,
        qualified_name=qname,
        kind=kind,
        language=language,
        file=file,
        start_line=start,
        end_line=end,
        signature="(void)",
        docstring="does big things",
    )


# ── _symbol_to_chunks:超长符号按行区间二次切分 ──────────────────────────────


def test_oversize_symbol_splits_into_subchunks():
    """500 行 × ~109 字符 = ~54K(> MAX_CHUNK_CHARS 16000)→ 应切成 ≥3 段,各不超阈值。"""
    n = 500
    lines = [f"int var_{i:04d} = {i};  " + "a" * 90 for i in range(n)]
    body_text = "\n".join(lines)
    assert len(body_text) > MAX_CHUNK_CHARS  # 前提:确实超阈值

    chunks = _symbol_to_chunks(_sym(1, n), lines, 1, n)

    assert len(chunks) >= 2
    total = len(chunks)

    # 每个 sub-chunk 都 ≤ 阈值 + total 字段一致
    for c in chunks:
        assert len(c.text) <= MAX_CHUNK_CHARS
        assert c.total == total

    # part 编号 1..total 连续
    assert [c.part for c in chunks] == list(range(1, total + 1))

    # id:part1 无 :p1(沿用 _chunk_id 的 part<=1 规则),其余 :p2/:p3…
    assert chunks[0].id == "big.c:big_fn"
    for idx, c in enumerate(chunks[1:], start=2):
        assert c.id == f"big.c:big_fn:p{idx}"

    # 行号连续(相邻段首尾相接)+ 覆盖整个 [1, n]
    assert chunks[0].start_line == 1
    assert chunks[-1].end_line == n
    for prev, cur in zip(chunks, chunks[1:], strict=False):  # strict=False:chunks[1:] 故意短一位
        assert cur.start_line == prev.end_line + 1

    # 各段 text == 它行号区间的原文;拼接还原原 body
    for c in chunks:
        assert c.text == "\n".join(lines[c.start_line - 1 : c.end_line])
    assert "\n".join(c.text for c in chunks) == body_text

    # content_hash 两两不同 + fts_text 非空(每段都带符号名词袋,可被召回)
    assert len({c.content_hash for c in chunks}) == total
    assert all(c.fts_text.strip() for c in chunks)


def test_normal_symbol_stays_single_chunk():
    """小符号(远小于阈值)→ 仍是整块一个 chunk,part=1/total=1,id 不带 :p。"""
    lines = [f"    int x = {i};" for i in range(20)]  # ~180 字符
    chunks = _symbol_to_chunks(_sym(1, 20, qname="small_fn"), lines, 1, 20)

    assert len(chunks) == 1
    assert chunks[0].part == 1 and chunks[0].total == 1
    assert chunks[0].id == "big.c:small_fn"


def test_single_mega_line_cannot_split_stays_one():
    """单行本身就超阈值(vendor 头巨宏,极罕见):按行无法再切 → 原样保留(记 backlog 的局限)。

    断言"不崩 + 返 1 个 chunk",并显式记录它仍超阈值(行区间切分到此为止)。
    """
    lines = ["#define GIANT_MACRO " + "x" * (MAX_CHUNK_CHARS + 5000)]
    chunks = _symbol_to_chunks(_sym(1, 1, qname="GIANT_MACRO", kind="macro"), lines, 1, 1)

    assert len(chunks) == 1
    assert chunks[0].part == 1 and chunks[0].total == 1
    assert len(chunks[0].text) > MAX_CHUNK_CHARS  # 文档化:单巨行无法按行切,仍超(罕见,非 wpa 场景)


# ── _chunk_one_file:接线(循环换成 _symbol_to_chunks 后仍正确) ────────────────


def test_chunk_one_file_wiring_splits_oversize_symbol():
    """_chunk_one_file 走 _symbol_to_chunks:超长符号切成多段 + 无遗漏的 module chunk。"""
    n = 400
    body = "\n".join(f"  call_{i}(arg);" + " " * 90 for i in range(n))  # ~104 字符/行 × 400 ≈ 41K
    sym = _sym(1, n, qname="driver_init", file="src/driver.c")

    chunks = _chunk_one_file("src/driver.c", body.encode(), [sym], "c")
    funcs = [c for c in chunks if c.kind == "function"]

    assert len(funcs) >= 2  # 超长符号被切成多段
    assert all(len(c.text) <= MAX_CHUNK_CHARS for c in funcs)
    # 所有行被符号覆盖 → 不该有 module chunk
    assert not any(c.kind == "module" for c in chunks)


def test_chunk_one_file_normal_symbol_unaffected():
    """正常大小符号:#58 改动对其零影响(仍是 1 chunk)。回归保护。"""
    body = "\n".join(f"  line_{i}();" for i in range(10))
    sym = _sym(1, 10, qname="normal_fn", file="x.c")
    chunks = _chunk_one_file("x.c", body.encode(), [sym], "c")
    funcs = [c for c in chunks if c.kind == "function"]
    assert len(funcs) == 1
    assert funcs[0].part == 1 and funcs[0].total == 1


def test_chunk_one_file_splits_oversize_module_no_symbols():
    """无符号的大文件(vendor 头场景,如 qca-vendor.h):整文件落 module chunk,#58 按行切成多段。

    这是 #58 真正的肇事路径(头文件无被解析符号 → 整文件一个 module chunk → 超 embedder 上限)。
    """
    n = 1500  # 每行 ~120 字符 × 1500 ≈ 180K,远超 16000
    body = "\n".join(f"#define MACRO_{i:04d} (1 + {i})   " + "x" * 95 for i in range(n))
    chunks = _chunk_one_file("vendor.h", body.encode(), [], "c")  # symbols=[] → 全进 module
    mods = [c for c in chunks if c.kind == "module"]

    assert len(mods) >= 2
    assert all(len(c.text) <= MAX_CHUNK_CHARS for c in mods)
    total = len(mods)
    assert [c.part for c in mods] == list(range(1, total + 1))
    # 行号覆盖整个文件(段间首尾顺序保留)
    assert mods[0].start_line == 1
    assert mods[-1].end_line == n
    # id:part1 无 :p1,其余 :p2/:p3…
    assert mods[0].id == "vendor.h:<module>"
    for idx, c in enumerate(mods[1:], start=2):
        assert c.id == f"vendor.h:<module>:p{idx}"


# ── parser 作用域限定名(2026-08-18 真 bug 回归:函数局部同名符号撞 chunk id) ──
#
# 事故形状:tests/test_mcp_tools.py 里 4 个测试函数各自定义 class _FakeGraph,
# 旧 parser 只跟 class 栈 → 4 个都叫 "_FakeGraph" → 4 条 chunk 同 id 进一个 upsert 批,
# LanceDB 直接拒:"Ambiguous merge inserts are prohibited"。修法 = 作用域栈把函数也压进去。


def _qnames(source: str) -> list[tuple[str, str]]:
    """解析一段 Python 源码,返回 [(qualified_name, kind)],按出现顺序。"""
    syms = _parse_bytes(source.encode(), GRAMMARS["python"], "test_scope.py")
    return [(s.qualified_name, s.kind) for s in syms]


def test_parser_qualifies_function_local_classes():
    """两个函数各自定义同名局部类 → 限定名带函数前缀,不再撞车(修的就是这个)。"""
    out = _qnames(
        "def test_a():\n"
        "    class _FakeGraph:\n"
        "        def m(self):\n"
        "            pass\n"
        "def test_b():\n"
        "    class _FakeGraph:\n"
        "        pass\n"
    )
    assert out == [
        ("test_a", "function"),
        ("test_a._FakeGraph", "class"),
        ("test_a._FakeGraph.m", "method"),
        ("test_b", "function"),
        ("test_b._FakeGraph", "class"),
    ]
    # 唯一性:同文件限定名两两不同(chunk id = file:qname,这是 id 唯一的前提)
    names = [q for q, _ in out]
    assert len(names) == len(set(names))


def test_parser_nested_function_and_toplevel_unchanged():
    """嵌套函数带外层函数前缀;顶层/类内/类内类等老场景零变化(回归保护)。"""
    out = _qnames(
        "def outer():\n"
        "    def helper():\n"
        "        pass\n"
        "class Top:\n"
        "    def method(self):\n"
        "        pass\n"
        "def top_fn():\n"
        "    pass\n"
    )
    assert out == [
        ("outer", "function"),
        ("outer.helper", "function"),  # 嵌套函数也是 function(直接外层不是 class)
        ("Top", "class"),
        ("Top.method", "method"),
        ("top_fn", "function"),
    ]


# ── chunk_repo 撞 id 去重护栏 ────────────────────────────────────────────────


def test_chunk_repo_dedupes_colliding_ids(tmp_path, caplog):
    """防御层:真给两个同 id 的 Symbol(C 的 struct/函数同名边缘,parser 修不完的),
    chunk_repo 保留首见、丢弃后见、发警告 —— 不再让重复 id 流向 upsert。"""
    f = tmp_path / "edge.c"
    f.write_text("int dup_name(void) { return 1; }\n", encoding="utf-8")
    dup = _sym(1, 1, qname="dup_name", kind="macro", file="edge.c")
    chunks = chunk_repo(tmp_path, symbols=[dup, dup])  # 同 id 两条

    ids = [c.id for c in chunks if c.kind == "macro"]
    assert ids == ["edge.c:dup_name"]  # 只活首见一条
    assert any("重复" in r.message for r in caplog.records)  # 且有日志,静默腐蚀变有声丢弃
