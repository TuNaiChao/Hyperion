"""bug-RCA 漏斗主编排:file → function → line(Agentless 复刻,R2 批3)。

这一层干什么(面向小白)
------------------------
委托给 opencode 之前,先用一个**确定性、可复现**的漏斗圈出"bug 大概在这几个函数",
而不是让 opencode 在整库里自由探索(费 token、不可控)。漏斗分三层,每层用 LLM 在
越来越精的视图上 rerank:

  file-level    :LLM 看目录树选相关文件;
  function-level:LLM 看候选文件的函数骨架(只签名),标相关 function/class;
  line-level    :LLM 看候选函数的带行号代码,标嫌疑行。
产物:一组 LocAnchor[(file, function, line, why)],喂给委托前的 assemble。

为什么三层而非一步到位:整库直接喂 LLM 太贵且不可控;逐层收窄(文件→函数→行),
每层视图精简、token 可控、可复现。对标 Agentless 三段(fl/FL.py)。

复用:parser.Symbol + skeleton(render_file_tree/render_skeleton)+ loc_translate
(transfer_locs/line_wrap_content)。LLM 用 create_chat_model(role="locator");
输出是行格式文本(文件名/锚点),非 JSON,直接逐行 parse(不像 delegate 要抠 JSON)。

v1 取舍:file-level 先纯 LLM 路;语义 retrieval 双路融合留 backlog(需建 code_index
索引;调研 §8 建议双路,但 MVP 单路 LLM 即可跑通)。

调研依据:r2-bug-rca-research.md §4(映射)+ §8(伪代码);Agentless fl/FL.py。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from hyperion.platform.models import create_chat_model
from hyperion.services.code_index.loc_translate import (
    line_wrap_content,
    merge_intervals,
    transfer_locs,
)
from hyperion.services.code_index.parser import Symbol, parse_repo
from hyperion.services.code_index.skeleton import render_file_tree, render_skeleton


@dataclass(frozen=True)
class LocAnchor:
    """漏斗产出的一个嫌疑锚点(给 assemble / 报告用)。"""

    file: str  # 相对仓根路径
    function: str | None  # 所属函数名(qualified,如 "scan_only_handler");纯 line 锚点可能 None
    line: int  # 嫌疑行(1-indexed);function 锚点用其 start_line
    why: str  # 原锚点文本(如 "function: scan_only_handler" / "line: 2452")


# ──────────────────────────────────────────────────────────────────────────
# §1 LLM 调用辅助(行格式输出,逐行 parse)
# ──────────────────────────────────────────────────────────────────────────


def _llm_pick(prompt: str, *, temperature: float = 0.0, max_retries: int = 2) -> str:
    """调 locator 角色模型拿文本。retry:第 1 次 temp=0,失败(空/异常)升温到 1.0(Agentless 策略)。

    为什么 role="locator":工厂按角色从 model_roles 路由(对齐 R1 extract.py 用
    role="memory_extractor"),以后换模型只改 config 的 model_roles.locator。
    """
    last_err: Exception | None = None
    for attempt in range(max_retries + 1):
        temp = temperature if attempt == 0 else 1.0
        try:
            model = create_chat_model(role="locator", temperature=temp)
            resp = model.invoke(prompt)
            text = resp.content if isinstance(resp.content, str) else str(resp.content)
            if text.strip():
                return text
        except Exception as e:  # noqa: BLE001 —— LLM 调用可能各种失败,retry 兜底
            last_err = e
    if last_err:
        raise last_err
    return ""


def _parse_lines(text: str) -> list[str]:
    """LLM 文本输出 → 干净的非空行列表(去序号 / markdown 符号 / 空白)。"""
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip().strip("`").strip()
        line = re.sub(r"^[\d]+[.)]\s*", "", line)  # 去序号 "1. "
        line = re.sub(r"^[-*]\s+", "", line)  # 去列表符号 "- " / "* "
        if line and not line.startswith("```"):
            out.append(line)
    return out


# ──────────────────────────────────────────────────────────────────────────
# §2 三段漏斗(每段一个 prompt + LLM rerank)
# ──────────────────────────────────────────────────────────────────────────


_FILE_PROMPT = """\
你是代码 bug 定位专家。下面是一个 bug 的线索 + 一个仓库的文件目录树。
请选出**最可能需要查看/修改以定位这个 bug 的文件**,最多 {max_files} 个,按相关度从高到低。

### Bug 线索 ###
{trigger}
###

### 仓库目录树 ###
{tree}
###

只输出文件相对路径,每行一个,不要解释、不要序号。"""


def _stage_file_level(trigger: str, files: list[str], *, max_files: int = 5) -> list[str]:
    """file-level:LLM 看目录树选文件。返回选中的真实文件路径(保序,严格匹配)。"""
    tree = render_file_tree(files)
    prompt = _FILE_PROMPT.format(trigger=trigger, tree=tree, max_files=max_files)
    picked = _parse_lines(_llm_pick(prompt))
    real = set(files)  # 只留仓库里真实存在的(对标 Agentless correct_file_paths 严格子串)
    return [p for p in picked if p in real][:max_files]


_FUNC_PROMPT = """\
你是代码 bug 定位专家。下面是 bug 线索 + 候选文件的函数骨架(只签名,无 body)。
请选出**最可能与这个 bug 相关的函数/类**,用锚点格式输出,每行一个:
  function: <函数名 或 Class.method>
  class: <类名>

### Bug 线索 ###
{trigger}
###

### 候选文件骨架 ###
{skeleton}
###

只输出锚点行,不要解释。"""


def _stage_function_level(
    trigger: str, pred_files: list[str], symbols_by_file: dict[str, list[Symbol]]
) -> dict[str, list[tuple[int, int, str]]]:
    """function-level:候选文件骨架 → LLM 标 function/class 锚点 → 行区间。
    返回 {file: [(start, end, why)]}(context_window=0,扩窗在 line-level 做)。"""
    out: dict[str, list[tuple[int, int, str]]] = {}
    for fn in pred_files:
        syms = symbols_by_file.get(fn, [])
        if not syms:
            continue
        prompt = _FUNC_PROMPT.format(trigger=trigger, skeleton=render_skeleton(syms))
        loc_lines = _parse_lines(_llm_pick(prompt))
        intervals = transfer_locs(loc_lines, syms, context_window=0)
        if intervals:
            out[fn] = intervals
    return out


_LINE_PROMPT = """\
你是代码 bug 定位专家。下面是 bug 线索 + 相关函数的带行号代码片段。
请选出**最可能是 bug 根因所在的具体行**,用锚点格式输出,每行一个:
  line: <行号>

### Bug 线索 ###
{trigger}
###

### 相关代码(带行号)###
{code}
###

只输出 line: <行号> 锚点,不要解释。"""


def _stage_line_level(
    trigger: str,
    coarse: dict[str, list[tuple[int, int, str]]],
    symbols_by_file: dict[str, list[Symbol]],
    repo_root: Path,
) -> list[LocAnchor]:
    """line-level:候选函数带行号显示 → LLM 标 line → LocAnchor。
    function-level 锚点本身也作 anchor(若 LLM 没给更细 line)。"""
    anchors: list[LocAnchor] = []
    for fn, intervals in coarse.items():
        syms = symbols_by_file.get(fn, [])
        try:
            source = (repo_root / fn).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        merged = merge_intervals([(s, e) for s, e, _ in intervals])
        code = line_wrap_content(source, merged, sticky_scroll=True, symbols=syms)
        prompt = _LINE_PROMPT.format(trigger=trigger, code=code)
        loc_lines = _parse_lines(_llm_pick(prompt))
        line_iv = transfer_locs(loc_lines, syms, context_window=0)
        seen: set[tuple[str, int]] = set()
        # line 锚点 → anchor
        for s, _e, why in line_iv:
            key = (fn, s)
            if key in seen:
                continue
            seen.add(key)
            anchors.append(LocAnchor(file=fn, function=_function_at(syms, s), line=s, why=why))
        # function/class 锚点兜底作 anchor(若 line-level 没覆盖到)
        for s, _e, why in intervals:
            if why.lower().startswith(("function:", "class:")):
                key = (fn, s)
                if key not in seen:
                    seen.add(key)
                    anchors.append(LocAnchor(file=fn, function=_name_from_why(why), line=s, why=why))
    return anchors


def _function_at(syms: list[Symbol], line_no: int) -> str | None:
    """找 line_no 所属的最内层 function/method Symbol 的 qualified_name。"""
    containing = [s for s in syms if s.start_line <= line_no <= s.end_line and s.kind in ("function", "method")]
    if containing:
        return min(containing, key=lambda s: s.end_line - s.start_line).qualified_name
    return None


def _name_from_why(why: str) -> str:
    """从 'function: foo' / 'class: Bar' 锚点取名字部分。"""
    return why.split(":", 1)[1].strip() if ":" in why else why


# ──────────────────────────────────────────────────────────────────────────
# §3 总入口
# ──────────────────────────────────────────────────────────────────────────


def localize(
    repo_root: Path | str,
    trigger: str,
    *,
    top_n_files: int = 3,
    max_files_llm: int = 5,
) -> list[LocAnchor]:
    """漏斗主入口:repo + trigger → list[LocAnchor]。

    repo_root    :仓库根。
    trigger      :bug 线索(日志摘要 / 问题描述 / 漏洞报告关键句)—— 由 workflow 的 ingest 步产出。
    top_n_files  :file-level 后取前 N 个文件进 function-level(Agentless 默认 3)。
    max_files_llm:file-level 让 LLM 最多选几个(Agentless 默认 5)。
    """
    repo_root = Path(repo_root)
    symbols = parse_repo(repo_root)
    symbols_by_file: dict[str, list[Symbol]] = {}
    for s in symbols:
        symbols_by_file.setdefault(s.file, []).append(s)
    files = sorted(symbols_by_file.keys())

    # Stage 1: file-level(LLM 路;语义 retrieval 双路融合留 backlog)
    pred_files = _stage_file_level(trigger, files, max_files=max_files_llm)[:top_n_files]
    if not pred_files:
        return []

    # Stage 2: function-level
    coarse = _stage_function_level(trigger, pred_files, symbols_by_file)
    if not coarse:
        return []

    # Stage 3: line-level
    return _stage_line_level(trigger, coarse, symbols_by_file, repo_root)
