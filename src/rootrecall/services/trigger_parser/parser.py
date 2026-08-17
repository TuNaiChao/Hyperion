"""issue 文档 → 纯文本。

出口(面向小白):`parse_issue(path)` 读一个 .md/.txt/.pdf 文档 → IssueDoc(纯文本 + 来源)。

**不做 regex 关键字抽取**(2026-07-31 踩坑 #2):代码符号 ≠ 日志散文形,regex 抽不准
(实测 demo2:issue 抽出的是代码符号 scan_res_handler,日志里是散文 radio work 'p2p-scan',
子串不匹配)。现代 LLM 自己会从文本抽检索目标(OpenHands/SWE-agent 无显式 NER 阶段);
要抽时由 delegate(opencode)或经 `rootrecall_search_codebase` 工具搞,不在 RootRecall 侧重建。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class IssueDoc:
    """一个 issue 文档的解析结果(读 .md/.txt/.pdf → 纯文本)。"""

    text: str  # 全文纯文本(喂 trigger / 报告)
    source: str = ""  # 来源路径(溯源)


def parse_issue(path: Path | str) -> IssueDoc:
    """读一个 issue 文档(.md/.txt/.pdf)→ IssueDoc(纯文本 + 来源)。

    PDF 用 pypdf(已在 uv.lock,无需新依赖);md/txt 直接读。抽不出文本(扫描件/加密 PDF)→
    text 为空,上层可降级提示手敲 trigger(记 backlog:真频繁遇扫描件再加 pymupdf4llm/markitdown)。
    """
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".pdf":
        text = _extract_pdf_text(p)
    else:  # md / txt / 其它文本类
        text = p.read_text(encoding="utf-8", errors="replace")
    return IssueDoc(text=text, source=str(p))


def _extract_pdf_text(path: Path) -> str:
    """pypdf 逐页抽文本(已在 uv.lock)。扫描件/加密/缺库 → 返空串(上层降级手敲)。"""
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""  # pypdf 没装 → 空串(上层提示手敲 trigger)
    try:
        reader = PdfReader(str(path))
    except Exception:  # noqa: BLE001 —— 损坏/加密 PDF 各种失败,返空让上层降级
        return ""
    pages: list[str] = []
    for page in reader.pages:
        try:
            t = page.extract_text() or ""
        except Exception:  # noqa: BLE001 —— 单页失败不连坐,跳过该页
            t = ""
        if t:
            pages.append(t)
    return "\n\n".join(pages)
