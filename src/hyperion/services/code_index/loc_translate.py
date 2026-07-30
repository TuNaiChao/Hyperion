"""行区间翻译 + 行号化显示(漏斗 line-level 核心,Agentless 复刻)。

这一层干什么(面向小白)
------------------------
漏斗最后一层 line-level:LLM 已从骨架选出嫌疑函数/类/行(输出 `function: foo` /
`class: Bar` / `line: 42` 这种锚点),但要把这些"名字/行号"翻译成**实际代码的行区间**,
再带行号显示给 LLM 做最后 rerank。本文件干这三件事:

  1. transfer_locs  :把 LLM 锚点(function:/class:/line:)按 parser.Symbol 翻译成
                      行区间,并按 context_window 向外扩窗(看上下文)。
  2. merge_intervals:把重叠/相邻的行区间合并成不重叠的(免得行号化显示重复)。
  3. line_wrap_content:只显示指定区间内的行(带行号),区间之间插 `...`;
                      sticky_scroll 在每个可见区间头部先打出外层 class/def 作用域
                      (模仿 VSCode 粘性滚动),让 LLM 不丢"当前行属于哪个函数"——
                      这是 Agentless line-level 准确率的关键差异点。

为什么基于 Symbol 而非 Agentless 的 structure dict:Hyperion parser.Symbol 已含
start_line/end_line/kind/qualified_name,直接查;Agentless 的正则 Python 解析 + 三层
嵌套 dict 在此简化为"按 (kind, name) 在 list[Symbol] 里找"。

调研依据:r2-bug-rca-research.md §4 + Agentless preprocess_data.py:113-322
(transfer_arb_locs_to_locs)、11-86(line_wrap_content,含 sticky_scroll:43-76)、
89-110(merge_intervals)。
"""

from __future__ import annotations

import re

from hyperion.services.code_index.parser import Symbol

# 作用域关键字开头(Python class/def + C struct/enum/union),sticky_scroll 用
_SCOPE_RE = re.compile(r"^\s*(class|def|struct|enum|union)\b")
# C 函数定义开头近似:返回类型 + 名 + ( 在行首(粗匹配,sticky 用,不必精确)
_C_FUNC_RE = re.compile(r"^\s*[\w\*\s]+\**\s*\w+\s*\(")


def merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """合并重叠/相邻的 [start, end] 行区间(1-indexed,闭区间)。

    对标 Agentless merge_intervals(preprocess_data.py:89-110)。
    输入无需排序;返回排序后的不重叠区间列表(相邻 1 行也算相邻,合并)。
    """
    if not intervals:
        return []
    sorted_iv = sorted(intervals)
    merged = [sorted_iv[0]]
    for start, end in sorted_iv[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + 1:  # 重叠或相邻(差 1 行也合并)
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def transfer_locs(
    loc_lines: list[str],
    symbols: list[Symbol],
    *,
    context_window: int = 10,
) -> list[tuple[int, int, str]]:
    """把 LLM 输出的锚点行翻译成带上下文的行区间。

    loc_lines:LLM 输出的锚点,每行形如 "function: foo" / "function: Class.method" /
               "class: Bar" / "line: 42"(对标 Agentless coarse_locs 格式)。
    symbols  :该文件的 parser.Symbol 列表(按 kind/name 查 start_line/end_line)。
    context_window:每个锚点向前后扩多少行(看上下文);Agentless 默认 10。
    返回:[(start_line, end_line, why)] —— why 是原锚点文本,供后续去重/解释。
          区间未合并(调用方按需 merge_intervals)。
    """
    out: list[tuple[int, int, str]] = []
    # 名索引:qualified_name 直查;simple name 可能多个同名(歧义)
    by_qname = {s.qualified_name: s for s in symbols}
    by_name: dict[str, list[Symbol]] = {}
    for s in symbols:
        by_name.setdefault(s.name, []).append(s)
    current_class: str | None = None  # "class: Foo" 后裸 "function: bar" 在 Foo 里找

    for raw in loc_lines:
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("line:"):
            try:
                ln = int(line.split(":", 1)[1].strip())
                out.append((ln, ln, line))
            except ValueError:
                continue
        elif low.startswith("class:"):
            name = line.split(":", 1)[1].strip()
            current_class = name
            for s in symbols:
                if s.kind == "class" and s.name == name:
                    out.append((s.start_line, s.end_line, line))
        elif low.startswith("function:"):
            name = line.split(":", 1)[1].strip()
            sym = _find_function(name, by_qname, by_name, current_class)
            if sym is not None:
                out.append((sym.start_line, sym.end_line, line))

    # 扩窗:每个区间向前后加 context_window 行(看上下文,不越界)
    if context_window > 0:
        out = [(max(1, s - context_window), e + context_window, why) for s, e, why in out]
    return out


def _find_function(
    name: str,
    by_qname: dict[str, Symbol],
    by_name: dict[str, list[Symbol]],
    current_class: str | None,
) -> Symbol | None:
    """按名字找函数符号,支持点号(Class.method)/ 当前类上下文 / 裸名。

    对标 Agentless transfer_arb_locs_to_locs 的 function 分支(preprocess_data.py:163-250):
      1. 点号或全限定:按 qualified_name 直查(Class.method);
      2. 当前类上下文:裸 method 名 → 在 current_class 里找;
      3. 裸名:按 simple name 查,唯一匹配才接受(多个同名 = 歧义,跳过)。
    """
    if name in by_qname:
        return by_qname[name]
    if current_class and f"{current_class}.{name}" in by_qname:
        return by_qname[f"{current_class}.{name}"]
    cands = [s for s in by_name.get(name, []) if s.kind in ("function", "method")]
    return cands[0] if len(cands) == 1 else None


def line_wrap_content(
    source: str,
    intervals: list[tuple[int, int]],
    *,
    sticky_scroll: bool = True,
    symbols: list[Symbol] | None = None,
) -> str:
    """只显示指定区间内的行(带行号),区间之间插 ...;可选 sticky_scroll。

    对标 Agentling line_wrap_content(preprocess_data.py:11-86)。行号格式 `{n}| {line}`;
    sticky_scroll:每个可见区间头部先打出当前行外层 class/def 作用域(模仿 VSCode 粘性
    滚动),让 LLM 不丢"当前行属于谁"——Agentless line-level 准确率的关键差异点。
    symbols:传该文件的 Symbol 列表让 sticky 精准定位所属函数(推荐);不传则退化正则。

    source   :文件全文。
    intervals:要显示的 [start,end] 行区间(1-indexed,闭)。内部会先 merge_intervals。
    """
    lines = source.splitlines()
    merged = merge_intervals(intervals)
    if not merged or not lines:
        return ""
    parts: list[str] = []
    for idx, (start, end) in enumerate(merged):
        start = max(1, start)
        end = min(len(lines), end)
        if start > len(lines):
            continue
        if idx > 0:
            parts.append("    ... (省略) ...")
        if sticky_scroll:
            for sl, text in _active_scope(lines, start, symbols, end):
                parts.append(f"{sl}| {text.strip()}  // <- 外层作用域(sticky)")
        for ln in range(start, end + 1):
            parts.append(f"{ln}| {lines[ln - 1]}")
    return "\n".join(parts)


def _active_scope(
    lines: list[str],
    line_no: int,
    symbols: list[Symbol] | None = None,
    end: int | None = None,
) -> list[tuple[int, str]]:
    """找 line_no 所属 / 前方的外层函数/类作用域(给 sticky_scroll 用)。

    优先用 symbols(tree-sitter 抽的,准),两步:
      1. 前瞻:区间 [line_no, end] 内第一个 function/class 定义行 —— 最常见情况,
         区间头部在函数前的注释/空行,要看的是后面的函数。
      2. 包含:line_no 已在某函数体内(定义在区间前),显示所属最内层。
    无 symbols 时退化正则(宽松,C 的 if()/调用 会误报,仅 fallback)。
    """
    if symbols:
        # 1. 前瞻:区间内第一个 function/class 定义
        ahead = [
            s for s in symbols
            if s.start_line >= line_no and (end is None or s.start_line <= end)
            and s.kind in ("function", "method", "class")
        ]
        if ahead:
            first = min(ahead, key=lambda s: s.start_line)
            idx = min(first.start_line, len(lines)) - 1
            return [(first.start_line, lines[idx])]
        # 2. 包含:line_no 在某函数体内(定义在区间前)
        containing = [
            s for s in symbols
            if s.start_line <= line_no <= s.end_line and s.kind in ("function", "method", "class")
        ]
        if containing:
            inner = min(containing, key=lambda s: s.end_line - s.start_line)
            idx = min(inner.start_line, len(lines)) - 1
            return [(inner.start_line, lines[idx])]
        return []
    # fallback:无 symbols 时用正则(可能误报,仅兜底)
    scope: list[tuple[int, str]] = []
    for i in range(line_no - 1, 0, -1):
        text = lines[i]
        if _SCOPE_RE.match(text) or _C_FUNC_RE.match(text):
            scope.append((i + 1, text))
            if len(scope) >= 3:
                break
    scope.reverse()  # 从外到内
    return scope
