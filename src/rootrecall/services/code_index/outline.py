"""代码大纲 —— read_file 的"折叠摘要"引擎(复用 P1.0 parser)。

这一层干什么(面向小白)
----------------------
agent 读一个大文件时,别把全文糊它脸上。先把函数/类的"签名行"留着,函数体折叠成一行
省略号,末尾告诉模型"哪些行被折叠了、想看就用 read_file(start_line=, end_line=) 捞回来"。
这就是 oh-my-pi 的 read 无 selector 自动摘要(它是 Rust + 每 AST 节点;我们用 Python +
Symbol 粒度,够用且复用现成 parser)。

设计来源:
  - oh-my-pi  crates/pi-ast/src/summary.rs   —— BFS unfold(select_folded_spans)、
         "超限就跳过该子树但继续兄弟"的饿死防护
  - oh-my-pi  read.ts:385-399 (formatSummaryElisionFooter) —— elision footer 给真实范围
详见 docs/设计/p1-code-understanding-design.md §4.4。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rootrecall.services.code_index.parser import Symbol, detect_language, parse_file

# 摘要参数(借 omp SummaryOptions,Python 栈适当放宽)
SUMMARY_MIN_LINES = 50      # 文件短于这个就不摘要,直接原文(小文件摘要没意义)
SUMMARY_TARGET_LINES = 50   # BFS unfold 的目标可见行数(尽量展到这么多)
SUMMARY_HARD_LIMIT = 100    # 可见行硬上限(绝不超,保 context)
FOOTER_RANGE_SAMPLES = 2    # footer 里给几个真实折叠范围当例子


@dataclass
class Summary:
    text: str                                # 渲染好的带行号文本(含 elision 标记)
    elided_lines: int                        # 总共折叠了多少行
    elided_ranges: list[tuple[int, int]]     # 被折叠的 [start, end](1-indexed 闭)


def summarize_file(path: Path) -> Summary | None:
    """对 path 生成摘要。不可摘要(非代码/无 grammar/解析失败/小文件)→ 返回 None,调用方回退原文。"""
    path = Path(path)
    if detect_language(path) is None:
        return None  # 非代码文件(散文/配置),不摘要
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    if len(lines) < SUMMARY_MIN_LINES:
        return None  # 小文件不摘要

    symbols = parse_file(path)
    if not symbols:
        return None  # 没解析出符号(grammar 不全等),回退原文

    spans = _elidable_spans(symbols)              # 候选可折叠区间
    keep_folded = _bfs_select(lines, spans)        # BFS 决定哪些保持折叠
    return _render(lines, spans, keep_folded)


def _elidable_spans(symbols: list[Symbol]) -> list[tuple[int, int]]:
    """从符号列表算"可折叠体"的 [start, end](1-indexed 闭)。

    规则:符号体 = start_line+1 .. end_line(签名行 start_line 留可见,体折叠)。
    体不足 2 行的不算;按 start 排序、去嵌套重叠(取最外层,借 omp 森林取 root)。
    """
    raw = []
    for s in symbols:
        body_start = s.start_line + 1
        body_end = s.end_line
        if body_end - body_start >= 1:  # 至少 2 行的体才值得折叠
            raw.append((body_start, body_end))
    raw.sort()
    spans: list[tuple[int, int]] = []
    last_end = 0
    for s, e in raw:
        if s > last_end:        # 不嵌套在上一段里 → 取
            spans.append((s, e))
            last_end = e
    return spans


def _bfs_select(lines: list[str], spans: list[tuple[int, int]]) -> set[tuple[int, int]]:
    """BFS unfold(借 omp select_folded_spans,summary.rs:118-162)。

    初始全部折叠;按 span 从小到大逐个试展开,累计可见行,直到 ≥ 目标。
    关键边界(omp summary.rs:150-152):展开某 span 会超硬上限 → 跳过它但继续兄弟
    (防单个超大函数把整个大纲饿死)。
    返回"保持折叠"的 span 集合。
    """
    folded = list(spans)
    visible = len(lines) - sum(e - s + 1 for s, e in folded)  # 基线可见行
    keep_folded: set[tuple[int, int]] = set(folded)
    # BFS:小函数先展(信息密度高)
    for span in sorted(folded, key=lambda se: (se[1] - se[0], se[0])):
        if visible >= SUMMARY_TARGET_LINES:
            break
        size = span[1] - span[0] + 1
        if visible + size > SUMMARY_HARD_LIMIT:
            continue  # 超硬上限 → 跳过这个,继续兄弟(omp 饿死防护)
        keep_folded.discard(span)
        visible += size
    return keep_folded


def _render(lines: list[str], spans: list[tuple[int, int]],
            keep_folded: set[tuple[int, int]]) -> Summary:
    """把折叠决策渲染成带行号的文本 + 统计折叠行/范围。"""
    folded_at: dict[int, tuple[int, int]] = {}   # 行号 → 它属于的折叠 span
    elided_ranges: list[tuple[int, int]] = []
    elided_lines = 0
    for span in spans:
        if span in keep_folded:
            s, e = span
            elided_ranges.append((s, e))
            elided_lines += e - s + 1
            for ln in range(s, e + 1):
                folded_at[ln] = span

    out: list[str] = []
    skip_until = 0  # 折叠段中间的行跳过,只在段首渲染一行省略号
    for idx, line in enumerate(lines, 1):
        if idx <= skip_until:
            continue
        span = folded_at.get(idx)
        if span is not None:  # 一定是段首(中间的已被 skip_until 跳过)
            s, e = span
            out.append(f"{s:>5}|… ({s}-{e} 共 {e - s + 1} 行折叠)")
            skip_until = e
        else:
            out.append(f"{idx:>5}|{line}")

    return Summary(text="\n".join(out), elided_lines=elided_lines,
                   elided_ranges=sorted(elided_ranges))


def elision_footer(elided_ranges: list[tuple[int, int]], elided_lines: int) -> str:
    """生成 elision footer:给真实折叠范围当例子(借 omp formatSummaryElisionFooter,issue #1046)。

    没这个 footer,模型会瞎猜折叠了啥、或干脆全文重读。
    """
    if not elided_ranges:
        return ""
    samples = elided_ranges[:FOOTER_RANGE_SAMPLES]
    hints = ", ".join(f"start_line={s} end_line={e}" for s, e in samples)
    more = "" if len(elided_ranges) <= FOOTER_RANGE_SAMPLES else \
        f"(另有 {len(elided_ranges) - FOOTER_RANGE_SAMPLES} 段)"
    return f"…[已折叠 {elided_lines} 行;如需细读用 read_file 取这些范围:{hints}]{more}…"
