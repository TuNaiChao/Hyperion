"""代码理解服务 · 第一步:解析源码 (P1.0)。

这一层干什么
------------
想象要让 agent "看懂"一个几十万行的代码库。直接把所有源码塞给它既贵又慢,
也没法精确回答"某个函数定义在哪、谁调用了它"。所以第一步是先把代码拆成
一张张"符号卡片":每个函数、方法、类各一张,记着——名字、属于哪个类、
定义在第几行到第几行、参数签名。后面的切块、向量化、检索全都建在这些卡片上。

什么是 tree-sitter
------------------
tree-sitter 是一个**代码解析器**:给它一段源码,它返回一棵"语法树"(AST)。
打个比方——语文课上给句子标"主语/谓语/宾语";tree-sitter 给代码标
"这是函数定义、这是类、这是参数、这是函数体"。我们再从这棵树里把需要的
"符号"摘出来,做成上面的卡片。

为什么选它(而不是 clang 这类严格编译器):
1. **容错**:真实 C 项目(bluez)宏多、条件编译多,还常缺头文件;严格解析器
   会大面积报错、解析不了。tree-sitter 即使代码有错,也尽量解析出能解析的部分,
   特别适合"粗扫整个仓库"。
2. **多语言**:一套 API 通吃 Python / C / JS …… 换语言只换一份"语法规则"。

本文件的三个设计要点
--------------------
1. **零联网**:每门语言用一个独立的「语法包」(如 tree-sitter-python),语法
   规则直接打包进安装包,运行时不用下载。(之前用过的 tree-sitter-language-pack
   会在运行时从 GitHub 下载语法,国内网络超时用不了,所以弃用了。)
2. **多语言靠配置,不靠改代码**:下面的 GRAMMARS 是张注册表,把"每种语言对应
   哪套语法规则、哪些节点算函数/类"列出来;核心的摘取逻辑对所有语言通用。
   以后要支持 C,只需在表里加一条 + 装一个 tree-sitter-c,核心代码不用动。
3. **读不了的文件就跳过**:语法错误的文件不会让程序崩(tree-sitter 尽量解析、
   解析不了的部分自动跳过);不认识的后缀名直接返回空。这样扫大仓库时,
   一两个坏文件卡不住整个流程。

对外提供
--------
- Symbol:一张"符号卡片"的数据结构。
- parse_file(path):解析单个文件,返回它的符号卡片列表。
- parse_repo(root):解析整个仓库目录,返回所有符号卡片。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tree_sitter import Language, Parser

# ──────────────────────────────────────────────────────────────────────────
# §1 数据模型
# ──────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Symbol:
    """单个源码符号的完整记录。

    frozen=True 让 Symbol 可哈希、可作为 dict key / 放进 set,后续去重、
    建图都方便。
    """

    name: str # 简单名,如 "run"
    qualified_name: str # 带作用域,如 "Agent.run"(消歧 / code_graph 用)
    kind: str # "function" | "method" | "class"(C 再加 struct/macro/typedef/enum)
    language: str # "python" | "c" …
    file: str # 文件路径(parse_file 透传原样;parse_repo 给相对仓根的路径)
    start_line: int # 整块定义起始行,1-indexed
    end_line: int # 整块定义结束行,1-indexed
    signature: str | None  # 形参文本,如 "(self, question)";class 为 None
    docstring: str | None = None  # 函数/类的 docstring(去引号纯文本);parser 同一遍 DFS 抽,无则 None


# ──────────────────────────────────────────────────────────────────────────
# §2 语言注册表(数据驱动:加新语言只动这一节)
# ──────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LanguageGrammar:
    """一门语言的 tree-sitter 抽取配置。

    把「哪些 AST 节点算函数 / 类」「名字和形参在哪个字段」抽象成数据,
    核心遍历逻辑就不用为每门语言写一遍。tree-sitter 各语法的节点类型名
    不同(function_definition / struct_specifier …),但「容器节点 +
    name 字段 + 可选 parameters 字段」这个模式是共通的。
    """

    name: str # 语言名,如 "python"
    suffixes: tuple[str, ...] # 文件后缀,如 (".py",)
    load: Callable[[], Language] # 离线装载器(返回 Language,零联网)
    function_node: str # 函数的 AST 节点类型
    class_node: str # 类的 AST 节点类型
    name_field: str # 符号名字所在字段
    params_field: str # 形参所在字段(类无此字段可留 "() 占位,但 Python class 不走这里)
    extract_docstring: Callable[[Any, bytes], str | None] | None = None
    # 从函数/类 AST 节点抽 docstring(去引号文本);Python 传实现,C 场景留 None
    extract_name: Callable[[Any, bytes], str | None] | None = None
    # 自定义符号名提取。None(默认)= 直接用 name_field 取名(Python 走这条);
    # 非 None = 用它取名 —— C 的函数名不在 function_definition 直接字段,嵌在 declarator 链里,
    # 必须用它。设计上对齐 extract_docstring(都是"用 Callable 抽象语言差异")。


def _load_python() -> Language:
    """装载 Python 的 tree-sitter grammar(离线,grammar 已打进 wheel)。

    tree_sitter_python.language() 返回 grammar 的 capsule 指针,
    Language(...) 包一层得到可用的 Language 对象。这步**不联网**。
    """
    import tree_sitter_python  # 懒导入:未装该 grammar 包时,本模块其余语言仍可用

    return Language(tree_sitter_python.language())


def _extract_python_docstring(node, source: bytes) -> str | None:
    """抽 Python 函数/类的 docstring —— body 里第一条语句若是纯字符串字面量,那就是 docstring。

    tree-sitter 里:函数/类的 body 字段是个 block(语句块);block 的第一个「有名字子节点」
    若是 expression_statement(表达式语句)、且里面是个 string 节点,就认作 docstring,取它的
    string_content(不带引号的纯文本)。没有 docstring 就返回 None。

    为什么要抽它:docstring 是这个符号的「自然语言说明」,是 BM25 检索的高信号词源
    (函数名常是缩写,docstring 才是"这个函数到底干嘛"的大白话)。
    """
    body = node.child_by_field_name("body")
    if body is None or not body.named_children:
        return None
    first = body.named_children[0]
    # docstring = body 第一条语句是个裸字符串字面量(赋值/调用不算)
    string_node = None
    if first.type == "expression_statement":
        kids = first.named_children
        if kids and kids[0].type == "string":
            string_node = kids[0]
    elif first.type == "string":  # 极少数情况:裸 string 作首语句
        string_node = first
    if string_node is None:
        return None
    # string 节点内部结构:string_start / string_content / string_end。取 string_content
    # 拿到不带引号的正文;取不到(空串等)就退化用整段 strip。
    for child in string_node.children:
        if child.type == "string_content":
            return _node_text(child, source)
    return _node_text(string_node, source).strip()


def _load_c() -> Language:
    """装载 C 的 tree-sitter grammar(离线,grammar 已打进 wheel)。

    和 _load_python 同理:tree_sitter_c.language() 返回 capsule 指针,
    Language(...) 包一层得到可用的 Language 对象。R2 引入 —— wpa_supplicant /
    bluez 都是 C,demo2 金标准就是 wpa。
    """
    import tree_sitter_c  # 懒导入:未装该 grammar 包时,其余语言仍可用

    return Language(tree_sitter_c.language())


def _extract_c_function_name(node, source: bytes) -> str | None:
    """抽 C 函数名 —— 名字不在 function_definition 的直接字段,而嵌在 declarator 链里:
    function_definition.declarator(function_declarator) → .declarator(identifier)。

    打个比方:Python 写 `def foo():` 名字 foo 直接挂在函数节点上;C 写
    `void foo(void) {}` 时,语法树先把 `foo(void)` 包成一个「函数声明符」
    (function_declarator),foo 是这个声明符里的名字。所以要沿 declarator 字段
    逐级下钻(可能穿过 function_declarator / pointer_declarator,如函数指针),
    取最内层标识符的文本。找不到返回 None(无名定义跳过)。
    """
    decl = node.child_by_field_name("declarator")
    while decl is not None:
        inner = decl.child_by_field_name("declarator")
        if inner is None:
            break
        decl = inner
    # 最内层应是 identifier(普通名)/ field_identifier(结构体字段名)/ type_identifier
    if decl is not None and decl.type in ("identifier", "field_identifier", "type_identifier"):
        return _node_text(decl, source)
    return None


# 注册表:加 C 时在此追加一条,并 `uv add tree-sitter-c`。
#   "c": LanguageGrammar(
#       name="c", suffixes=(".c", ".h"),
#       load=lambda: Language(tree_sitter_c.language()),
#       function_node="function_definition",
#       class_node="struct_specifier",  # C 的「类」近似 struct/enum,落地时按需扩展
#       name_field="name", params_field="parameters"),
GRAMMARS: dict[str, LanguageGrammar] = {
    "python": LanguageGrammar(
        name="python",
        suffixes=(".py",),
        load=_load_python,
        function_node="function_definition",
        class_node="class_definition",
        name_field="name",
        params_field="parameters",
        extract_docstring=_extract_python_docstring,
    ),
        "c": LanguageGrammar(
        name="c",
        suffixes=(".c", ".h"),
        load=_load_c,
        function_node="function_definition",
        # struct_specifier 节点匹配过宽 —— 每处 `struct wpa_supplicant *` 参数/声明都是它,
        # 抽出来全是类型引用噪声(实测 28370 个,几乎没真定义)。用 "_none_" 这种语法树里
        # 绝不会出现的节点名,让 C 永不抽 class。demo2 根因在函数,struct 非必需;
        # 真要抽 struct 定义,按「有无 body 字段」过滤即可,留 backlog。
        class_node="_none_",
        name_field="name",          # C 不抽 class 了,此字段对 C 实际不触发(函数名走 extract_name)
        params_field="declarator",  # signature = 完整声明符(含函数名,如 scan_only_handler(struct ...)),报告引用正合适
        extract_name=_extract_c_function_name,
    ),
}


# 解析全仓时要跳过的目录名(非源码:缓存、虚环境、构建产物、版本控制)。
_SKIP_DIRS: frozenset[str] = frozenset(
    {"__pycache__", "node_modules", ".venv", "venv", "build", "dist", ".git"}
)


def detect_language(path: Path) -> str | None:
    """按文件后缀推断语言;不支持的后缀返回 None。"""
    suffix = path.suffix.lower()
    for lang, grammar in GRAMMARS.items():
        if suffix in grammar.suffixes:
            return lang
    return None


# ──────────────────────────────────────────────────────────────────────────
# §3 核心抽取
# ──────────────────────────────────────────────────────────────────────────

# Parser 缓存:tree-sitter 的 Parser 构造有成本(装载 grammar),同一语言复用。
# 注:非线程安全——P1 阶段是单进程顺序建索引,无需加锁;并发索引(P6)再补。
_parsers: dict[str, Parser] = {}


def _get_parser(grammar: LanguageGrammar) -> Parser:
    """懒构造并缓存每门语言的 Parser。"""
    if grammar.name not in _parsers:
        _parsers[grammar.name] = Parser(grammar.load())
    return _parsers[grammar.name]


def _node_text(node, source: bytes) -> str:
    """从源码字节里切出某节点的文本。

    tree-sitter 节点只存字节偏移(start_byte/end_byte),不存文本本身,
    所以要配合原始 source 字节切片。errors="replace" 防偶发非 UTF-8 字节。
    """
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _extract_symbols(
    root_node, grammar: LanguageGrammar, source: bytes, file_path: str
) -> list[Symbol]:
    """递归遍历 AST,收集函数 / 类符号。

    采用「带状态 DFS」而非「先 query 出节点再 parent 回溯」:下行时维护
    外层类名栈 class_stack,遇到函数就按当前栈算限定名、判定 function 还是
    method;遇到类就先入栈再下钻、出栈时弹。这样限定名和归属一次成形,
    不用回溯、不用给每个祖先线程化 source。
    """
    symbols: list[Symbol] = []
    class_stack: list[str] = []  # 外层类名,从外到内

    def visit(node) -> None:
        # —— 函数节点:记录后继续下钻(抓嵌套定义:函数内的函数 / 类)——
        if node.type == grammar.function_node:
            # 取名:有 extract_name(C 的函数名嵌在 declarator 链)用它;否则直接取 name 字段(Python)。
            if grammar.extract_name is not None:
                simple = grammar.extract_name(node, source)
            else:
                name_node = node.child_by_field_name(grammar.name_field)
                simple = _node_text(name_node, source) if name_node else None
            if simple is not None:  # 无名定义(lambda 等)跳过
                params_node = node.child_by_field_name(grammar.params_field)
                docstring = (
                    grammar.extract_docstring(node, source)
                    if grammar.extract_docstring is not None
                    else None
                )
                symbols.append(
                    Symbol(
                        name=simple,
                        qualified_name=".".join(class_stack + [simple]),
                        kind="method" if class_stack else "function",
                        language=grammar.name,
                        file=file_path,
                        start_line=node.start_point[0] + 1,  # 0-indexed → 1-indexed
                        end_line=node.end_point[0] + 1,
                        signature=_node_text(params_node, source) if params_node else "()",
                        docstring=docstring,
                    )
                )
        # —— 类节点:记录,入栈,下钻,出栈弹 ——
        elif node.type == grammar.class_node:
            name_node = node.child_by_field_name(grammar.name_field)
            simple = _node_text(name_node, source) if name_node else "<anonymous>"
            docstring = (
                grammar.extract_docstring(node, source)
                if grammar.extract_docstring is not None
                else None
            )
            symbols.append(
                Symbol(
                    name=simple,
                    qualified_name=".".join(class_stack + [simple]),
                    kind="class",
                    language=grammar.name,
                    file=file_path,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    signature=None,
                    docstring=docstring,
                )
            )
            class_stack.append(simple)  # 进入类作用域:压栈

        # 对所有节点(含上面命中的)继续下钻 named_children
        for child in node.named_children:
            visit(child)

        if node.type == grammar.class_node:
            class_stack.pop()  # 离开类作用域:弹栈

    visit(root_node)
    return symbols


def _parse_bytes(source: bytes, grammar: LanguageGrammar, file_path: str) -> list[Symbol]:
    """纯解析核心:接字节、出符号。parse_file / parse_repo 都走这里。"""
    parser = _get_parser(grammar)
    tree = parser.parse(source)
    return _extract_symbols(tree.root_node, grammar, source, file_path)


# ──────────────────────────────────────────────────────────────────────────
# §4 对外接口
# ──────────────────────────────────────────────────────────────────────────


def parse_file(path: Path | str, language: str | None = None) -> list[Symbol]:
    """解析单个源码文件,返回其中的符号列表(可能为空)。

    - language=None:按后缀推断;.py → python;未知后缀返回 []。
    - 读不了(权限 / 不存在)返回 [],不抛异常。
    - file 字段透传 str(path),调用方决定要不要规范化为相对路径。
    """
    path = Path(path)
    lang = language or detect_language(path)
    if lang is None:
        return []
    try:
        source = path.read_bytes()
    except OSError:
        return []
    return _parse_bytes(source, GRAMMARS[lang], str(path))


def iter_source_files(
    root: Path | str, languages: list[str] | None = None
):
    """遍历仓库里的源码文件,yield (绝对路径, 相对仓根路径字符串, 语言名)。

    抽出来给 parse_repo / chunker 共用:chunker 要对「每个源码文件」切块
    (含没有函数/类的纯模块文件,如只放 import/常量的 __init__.py),不能只靠
    符号列表——否则会漏掉无符号文件,破坏 100% 覆盖(cAST 的 plug-and-play)。

    - languages=None:遍历所有已注册语言;给 ["python"] 只遍历 Python。
    - 跳过 _SKIP_DIRS 里的目录和隐藏目录(.git / .venv …)。
    - rel 用相对仓根路径(索引稳定;仓挪位置不影响)。
    """
    root = Path(root)
    want = set(languages) if languages else set(GRAMMARS)

    # 收集要处理的后缀
    suffixes: set[str] = set()
    for lang in want:
        suffixes.update(GRAMMARS[lang].suffixes)

    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in suffixes:
            continue
        # 路径里的目录段是否命中跳过名单 / 隐藏目录
        rel = p.relative_to(root)
        if any(part in _SKIP_DIRS or part.startswith(".") for part in rel.parts[:-1]):
            continue
        lang = detect_language(p)
        if lang not in want:
            continue
        yield p, str(rel), lang


def parse_repo(
    root: Path | str, languages: list[str] | None = None
) -> list[Symbol]:
    """递归解析整个仓库,返回所有符号。

    - languages=None:解析所有已注册语言;给 ["python"] 只解析 Python。
    - 跳过 _SKIP_DIRS 里的目录和隐藏目录(.git / .venv …)。
    - 符号的 file 字段是**相对仓根**的路径,适合做稳定的索引键(仓挪位置不影响 file)。
    - 文件遍历逻辑见 iter_source_files(与 chunker 共用)。
    """
    root = Path(root)
    symbols: list[Symbol] = []
    for p, rel, lang in iter_source_files(root, languages):
        try:
            source = p.read_bytes()
        except OSError:
            continue  # 跳过读不了的文件,不让单个坏文件中断整仓扫描
        symbols.extend(_parse_bytes(source, GRAMMARS[lang], rel))
    return symbols
