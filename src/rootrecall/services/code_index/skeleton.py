"""符号 / 文件骨架渲染(漏斗 file-level + function-level 用,Agentless 复刻)。

这一层干什么(面向小白)
------------------------
Agentless 漏斗分三层定位 bug:file → function → line。前两层要把"候选文件"给 LLM 看,
但全文太长、太费 token。解决办法:给 LLM 看**骨架**而不是全文 ——
  - file-level   :看"项目目录树"(哪些文件、在哪),让它选相关文件;
  - function-level:看"函数签名列表"(每个函数叫啥、签名、在第几行),让它选相关函数。
本文件就渲染这两种骨架。直接复用 parser.Symbol(已抽好),纯文本,无外部依赖。

为什么不用 Agentless 的 libcst get_skeleton:libcst 只 Python;RootRecall 用 tree-sitter
多语言,且 Symbol 已含 name/signature/行范围,渲染骨架比源码 skeletonize 更简洁。
源码级 skeletonize(body 替换成 …)留 backlog(更精确但复杂,见 r2-bug-rca-research.md §4)。

调研依据:docs/调研/r2-bug-rca-research.md §4(可移植映射表)+ Agentless FL.py:29-187。
"""

from __future__ import annotations

from rootrecall.services.code_index.parser import Symbol


def render_file_tree(files: list[str], *, max_files: int | None = None) -> str:
    """把文件路径列表渲染成缩进目录树。

    给 file-level 漏斗用:LLM 看目录结构选相关文件(不看代码内容,省 token)。
    对标 Agentless show_project_structure(FL.py 调用它喂 LLM)。

    files:相对仓根的文件路径列表(如 ["wpa_supplicant/scan.c", ...]),通常从
           parse_repo 的 Symbol.file 去重得到。
    返回:缩进树形文本,目录用 `dir/`、文件用原名,同层按字母排序。
    """
    if max_files is not None:
        files = files[:max_files]
    # 按路径分段组织成嵌套 dict:目录 -> {子目录/文件}
    root: dict = {}
    for f in sorted(set(files)):
        node = root
        parts = f.split("/")
        for i, part in enumerate(parts):
            is_file = i == len(parts) - 1
            if is_file:
                node.setdefault("__files__", []).append(part)
            else:
                node = node.setdefault(part, {})
    lines: list[str] = []

    def walk(node: dict, prefix: str) -> None:
        dirs = sorted(k for k in node if k != "__files__")
        files_in = sorted(node.get("__files__", []))
        for d in dirs:  # 先目录后文件,常见目录树习惯
            lines.append(f"{prefix}{d}/")
            walk(node[d], prefix + "  ")
        for fn in files_in:
            lines.append(f"{prefix}{fn}")

    walk(root, "")
    return "\n".join(lines)


def render_skeleton(symbols: list[Symbol], *, max_sig_chars: int = 120) -> str:
    """把符号列表渲染成 LLM-friendly 骨架文本(给 function-level 漏斗选函数用)。

    每行:`<kind> <qualified_name><signature>  @ <file>:<start>-<end>`
    class 只给名(无签名);function/method 给名+签名。超长签名截断。
    对标 Agentless get_skeleton + obtain_relevant_functions prompt(FL.py:152-187)。

    为什么用 qualified_name 而非 name:function-level 漏斗要 LLM 输出 `function: Class.method`
    这种带作用域的锚点(见 loc_translate.py 的 transfer),qualified_name 正好匹配。
    """
    kind_tag = {"function": "func", "method": "meth", "class": "class"}
    lines: list[str] = []
    for s in symbols:
        tag = kind_tag.get(s.kind, s.kind)
        loc = f"{s.file}:{s.start_line}-{s.end_line}"
        if s.kind == "class":
            lines.append(f"{tag} {s.qualified_name}  @ {loc}")
        else:
            sig = s.signature or "()"
            # C 的 signature 是完整 declarator(含函数名 + 可能的前置指针 *,
            # 见 parser 的 params_field="declarator")。渲染时只取参数部分(首个 ( 起),
            # 避免 qualified_name + signature 重复函数名。
            paren = sig.find("(")
            if paren >= 0:
                sig = sig[paren:]
            if len(sig) > max_sig_chars:
                sig = sig[:max_sig_chars] + "…"  # 截断超长签名(C 函数指针参数会很长)
            lines.append(f"{tag} {s.qualified_name}{sig}  @ {loc}")
    return "\n".join(lines)
