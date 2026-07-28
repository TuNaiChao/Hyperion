"""搜索内核 —— grep 工具的"发动机"(纯算法,零第三方依赖)。

这一层干什么(面向小白)
----------------------
给一个目录 + 一个模式(正则或字面),在目录里逐文件逐行找匹配,返回 `path:行号: 行内容`。
核心不是"能搜",而是"搜得安全"——加了几道闸,防止 agent 一条命令把整个仓库(含
.git / 二进制 / 几 MB 的压缩文件)扫进 context,或被一条坏正则拖死。

设计来源(直接照搬,已验证):
  - deer-flow  backend/sandbox/search.py    —— IGNORE_PATTERNS / is_binary_file /
         find_grep_matches / truncate_line / symlink 守卫 / 大文件守卫 / ReDoS 守卫
  - oh-my-pi   crates/pi-natives/grep.rs    —— 正则兜底(搜索永不整次失败)、二进制 NUL 语义
详见 docs/设计/p1-code-understanding-design.md §4.2。
"""

from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────────
# §1 内建 ignore 黑名单(跳过这些目录/文件,不扫进结果)
# ──────────────────────────────────────────────────────────────────────────
# 不解析 .gitignore(deer-flow 也没做;omp 用 Rust ignore crate)。P1.4 不引新依赖,
# 硬编码通用黑名单够用;.gitignore 解析(pathspec 库)记 backlog #1。

# 字面量目录/文件名(整段匹配):放进 frozenset,O(1) 查。
_EXACT_IGNORE_NAMES: frozenset[str] = frozenset({
    ".git", ".hg", ".svn", ".idea", ".vscode",
    "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".venv", "venv", "env", ".tox",
    "dist", "build", "target", "out", ".next", ".nuxt",
    "data", "lancedb", ".lancedb",  # 本项目:索引/数据目录
    ".cache", ".gradle", "eggs", ".eggs",
})

# 通配模式(按文件名 match):预编译成一条联合正则,每项只 match 一次。
_GLOB_IGNORE_RE: re.Pattern[str] = re.compile(
    "|".join(fnmatch.translate(p) for p in (
        "*.pyc", "*.pyo", "*.so", "*.o", "*.a", "*.dll", "*.dylib",
        "*.wasm", "*.png", "*.jpg", "*.jpeg", "*.gif", "*.pdf",
        "*.zip", "*.tar", "*.gz", "*.tgz", "*.class", "*.lock",
    ))
)


def should_ignore_name(name: str) -> bool:
    """这个目录项要不要跳过?—— 先 O(1) set 查,再 1 次 regex。"""
    return name in _EXACT_IGNORE_NAMES or _GLOB_IGNORE_RE.match(name) is not None


# ──────────────────────────────────────────────────────────────────────────
# §2 守卫:二进制 / 超大文件 / 超长行
# ──────────────────────────────────────────────────────────────────────────

def is_probably_binary(path: Path, sniff: int = 8192) -> bool:
    """读前 sniff(默认 8192)字节判断是不是二进制。

    判定(双保险,借 omp binary.ts + deer-flow is_binary_file):
      - 含 NUL 字节(b'\\0')→ 二进制(最可靠信号,git/ripgrep 都用它);
      - 否则尝试 strict UTF-8 解码,失败 → 二进制(挡非 UTF-8 / 乱码);
      - OSError → fail-closed,当二进制跳过(读不了就不碰)。
    """
    try:
        head = path.read_bytes()[:sniff]
    except OSError:
        return True  # 读不了 → 当二进制,跳过更安全
    if b"\x00" in head:
        return True
    try:
        head.decode("utf-8")  # strict:非 UTF-8 抛 UnicodeDecodeError
    except UnicodeDecodeError:
        return True
    return False


DEFAULT_MAX_FILE_SIZE_BYTES = 1_000_000  # 单文件大小上限 1MB(借 deer-flow);超限跳过
_MAX_LINE_CHARS = 2000                    # 单行字符上限(借 deer-flow);超限跳过该行,防 ReDoS


def truncate_line(line: str, limit: int = 200) -> str:
    """超长行尾部截断成 limit 字符 + '...'(借 deer-flow + omp)。"""
    return line if len(line) <= limit else line[:limit] + "..."


# ──────────────────────────────────────────────────────────────────────────
# §3 正则兜底:一条坏正则不让整个搜索炸掉(借 omp grep.rs:987-1055)
# ──────────────────────────────────────────────────────────────────────────

def _compile_pattern(pattern: str, *, case_sensitive: bool, literal: bool) -> re.Pattern[str]:
    """编译正则。literal=True 或正则非法 → re.escape 降级字面匹配(搜索永不整次失败)。"""
    flags = 0 if case_sensitive else re.IGNORECASE
    if literal:
        return re.compile(re.escape(pattern), flags)
    try:
        return re.compile(pattern, flags)
    except re.error:
        return re.compile(re.escape(pattern), flags)


# ──────────────────────────────────────────────────────────────────────────
# §4 核心搜索循环(借 deer-flow find_grep_matches)
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class GrepMatch:
    path: str
    line: int        # 1-indexed
    content: str


@dataclass
class GrepResult:
    matches: list[GrepMatch]
    truncated: bool = False


def find_grep_matches(
    root: Path,
    pattern: str,
    *,
    glob: str | None = None,
    literal: bool = False,
    case_sensitive: bool = False,
    max_results: int = 100,
    max_file_size: int = DEFAULT_MAX_FILE_SIZE_BYTES,
) -> GrepResult:
    """在 root 下逐文件逐行搜 pattern,返回 GrepResult。

    - glob:只搜匹配此文件名模式的文件(如 '*.c');None 搜所有。
    - literal=True 走字面匹配(内部仍用正则,但 pattern 被 escape)。
    - max_results:命中到这个数立即停,truncated=True。
    """
    root = Path(root)
    regex = _compile_pattern(pattern, case_sensitive=case_sensitive, literal=literal)
    glob_re = re.compile(fnmatch.translate(glob)) if glob else None

    matches: list[GrepMatch] = []
    # os.walk + 就地改 dirs[:]:prune 黑名单目录,不再下钻(比 rglob 快、可控)
    for dirpath, dirnames, filenames in os.walk(root):
        # ① prune 目录:就地过滤 dirnames,os.walk 就不进这些目录
        dirnames[:] = [d for d in dirnames if not should_ignore_name(d)]
        for fn in filenames:
            if should_ignore_name(fn):
                continue
            if glob_re and not glob_re.match(fn):  # ② glob 过滤(按文件名)
                continue
            fpath = Path(dirpath) / fn
            # ③ symlink 守卫:跳过符号链接 + 解析后不得逃出 root
            try:
                if fpath.is_symlink():
                    continue
                if not fpath.resolve().is_relative_to(root.resolve()):
                    continue
            except OSError:
                continue
            # ④ 大文件 / 二进制守卫
            try:
                if fpath.stat().st_size > max_file_size:
                    continue
            except OSError:
                continue
            if is_probably_binary(fpath):
                continue
            # ⑤ 逐行正则搜
            try:
                text = fpath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                if len(line) > _MAX_LINE_CHARS:
                    continue  # ReDoS 守卫
                if regex.search(line):
                    matches.append(GrepMatch(str(fpath), lineno, truncate_line(line)))
                    if len(matches) >= max_results:
                        return GrepResult(matches, truncated=True)
    return GrepResult(matches, truncated=False)
