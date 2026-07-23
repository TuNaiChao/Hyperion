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


def _load_python() -> Language:
    """装载 Python 的 tree-sitter grammar(离线,grammar 已打进 wheel)。

    tree_sitter_python.language() 返回 grammar 的 capsule 指针,
    Language(...) 包一层得到可用的 Language 对象。这步**不联网**。
    """
    import tree_sitter_python  # 懒导入:未装该 grammar 包时,本模块其余语言仍可用

    return Language(tree_sitter_python.language())


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
            name_node = node.child_by_field_name(grammar.name_field)
            if name_node is not None:  # 无名定义(lambda 等)跳过
                simple = _node_text(name_node, source)
                params_node = node.child_by_field_name(grammar.params_field)
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
                    )
                )
        # —— 类节点:记录,入栈,下钻,出栈弹 ——
        elif node.type == grammar.class_node:
            name_node = node.child_by_field_name(grammar.name_field)
            simple = _node_text(name_node, source) if name_node else "<anonymous>"
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


def parse_repo(
    root: Path | str, languages: list[str] | None = None
) -> list[Symbol]:
    """递归解析整个仓库,返回所有符号。

    - languages=None:解析所有已注册语言;给 ["python"] 只解析 Python。
    - 跳过 _SKIP_DIRS 里的目录和隐藏目录(.git / .venv …)。
    - 符号的 file 字段是**相对仓根**的路径,适合做稳定的索引键
      (仓挪位置不影响 file)。
    """
    root = Path(root)
    want = set(languages) if languages else set(GRAMMARS)

    # 收集要处理的后缀
    suffixes: set[str] = set()
    for lang in want:
        suffixes.update(GRAMMARS[lang].suffixes)

    symbols: list[Symbol] = []
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
        try:
            source = p.read_bytes()
        except OSError:
            continue  # 跳过读不了的文件,不让单个坏文件中断整仓扫描
        # file 用相对路径:索引稳定、便于跨机 / 跨位置复用
        symbols.extend(_parse_bytes(source, GRAMMARS[lang], str(rel)))
    return symbols
