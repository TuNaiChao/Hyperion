"""issue 文档 → 纯文本 + 薄 regex 关键字。

两个出口(面向小白):
  parse_issue(path)   读一个 .md/.txt/.pdf 文档 → IssueDoc(纯文本 + 关键字 + 来源)。
  extract_keywords(t) 一段文本 → 关键字列表(panic/地址/路径/标识符)。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class IssueDoc:
    """一个 issue 文档的解析结果。"""

    text: str  # 全文纯文本(喂 trigger / 报告)
    keywords: list[str] = field(default_factory=list)  # 检索关键字(喂方案A 检索预筛)
    source: str = ""  # 来源路径(溯源)


def parse_issue(path: Path | str) -> IssueDoc:
    """读一个 issue 文档(.md/.txt/.pdf)→ IssueDoc(纯文本 + 关键字)。

    PDF 用 pypdf(已在 uv.lock,无需新依赖);md/txt 直接读。抽不出文本(扫描件/加密 PDF)→
    text 为空,上层可降级提示手敲 trigger(记 backlog:真频繁遇扫描件再加 pymupdf4llm/markitdown)。
    """
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".pdf":
        text = _extract_pdf_text(p)
    else:  # md / txt / 其它文本类
        text = p.read_text(encoding="utf-8", errors="replace")
    return IssueDoc(text=text, keywords=extract_keywords(text), source=str(p))


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


# ── 薄 regex 关键字抽取 ──────────────────────────────────────────────
# 为什么不做重 NER:现代 LLM 自己会从文本抽检索目标(OpenHands/SWE-agent 无显式 NER 阶段,
# 2026 调研)。这里只挑"对 BM25/向量检索有硬信号"的 token,够方案A 预筛用即可。

# 内核崩溃 / 严重错误签名(出现即强信号)
_PANIC_SIG = re.compile(
    r"\b(?:panic|Oops|BUG:|WARNING:|RIP:|Call Trace|Segmentation fault|SIGSEGV|"
    r"Aborted|core dumped|deadlock|race|overflow|underflow|null\s+deref)\b",
    re.IGNORECASE,
)
# 16/64 进制地址(0x...):崩溃栈 / 指针,常对应代码里的宏 / 常量
_ADDR = re.compile(r"\b0x[0-9a-fA-F]{4,}\b")
# 文件路径:src/foo/bar.c / wpa_supplicant.c(代码定位的硬锚点)
_PATH = re.compile(r"(?:[\w./-]+/)*[\w-]+\.(?:c|h|cc|cpp|hpp|py|rs|go|java)")
# C 标识符(函数 / 结构 / 宏名):≥4 字符 [A-Za-z_][A-Za-z0-9_]
_IDENT = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{3,}\b")

# 常见英文停用词(纯小写、不含下划线、不像代码符号)——标识符抽取时跳过,免得 "this/that" 刷屏
_STOP = frozenset(
    {
        "this", "that", "with", "from", "have", "they", "were", "been", "then",
        "them", "what", "when", "will", "would", "could", "should", "there",
        "their", "these", "those", "which", "into", "over", "such", "some",
    }
)


def extract_keywords(text: str, *, max_kw: int = 24) -> list[str]:
    """薄 regex 抽检索关键字:panic 签名 / 0x 地址 / 路径 / C 标识符。去重保序,封顶 max_kw。

    不分词、不 NER —— 只把"对检索有硬信号"的 token 挑出来喂 retrieve()。现代 LLM 也能
    自己抽,这层只是给 BM25/向量一个比整段散文更聚焦的 query(预筛精度更高)。

    抽取优先级:先 panic 签名 / 地址 / 路径(强信号),再用标识符填到封顶 —— 偏好带下划线
    (典型 C 函数/宏,如 scan_only_handler)和 CamelCase(类型名),跳过纯小写英文词。
    """
    if not text:
        return []
    found: list[str] = []
    seen: set[str] = set()

    def add(tok: str) -> None:
        t = tok.strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            found.append(t)

    for m in _PANIC_SIG.finditer(text):
        add(m.group(0))
    for m in _ADDR.finditer(text):
        add(m.group(0))
    for m in _PATH.finditer(text):
        add(m.group(0))
    for m in _IDENT.finditer(text):
        tok = m.group(0)
        if tok.lower() in _STOP:
            continue
        # 偏好带下划线(典型 C 符号)或含大写(CamelCase 类型);纯小写英文词跳过(噪声多)
        if "_" in tok or any(c.isupper() for c in tok[1:]):
            add(tok)
        if len(found) >= max_kw:
            break
    return found[:max_kw]
