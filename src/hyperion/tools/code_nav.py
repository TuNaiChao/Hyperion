"""代码导航工具 —— agent 用来按"符号/语义"在代码里找路(@tool 包装)。

三个工具(面向小白):
  - grep_symbol    按名字/正则找"符号定义"在哪个 file:line(像 IDE 的 Cmd-Shift-O)
  - read_function  读一个符号的完整定义体 + 元数据(像选中整个函数)
  - search_code    语义搜索:自然语言 → 混合检索 → top-k 代码块(包 P1.3 retrieve)

前两个复用 P1.0 parser(不依赖索引,没建库也能用);第三个包 P1.3 检索(需先建索引)。

约定(和 tools/sandbox.py 一致):
  - @tool(name, parse_docstring=True) —— docstring 的 Args 段自动变 JSON schema;
  - 首参 description: str 给模型说"为什么调用";
  - 重依赖(retrieval/store/parser)在函数内懒导入,失败返错误串不抛(借 deer-flow)。

详见 docs/设计/p1-code-understanding-design.md §4.5/§4.6。
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from langchain_core.tools import tool

from hyperion.platform.config import get_app_config


def _workspace() -> Path:
    """当前工作区目录(grep_symbol/read_function 在这下面找)。

    从 config.sandbox.workspace 读——与 LocalSandbox 构造用的是同一个值
    (provider.py 直接把 cfg.sandbox.workspace 传给 LocalSandbox)。不访问 Sandbox
    实例的 .workspace:那是 LocalSandbox 的属性,不在抽象基类 Sandbox 上(Docker 沙
    箱 P6 会有容器/宿主两套路径,不该把本地假设塞进 ABC)。
    """
    return Path(get_app_config().sandbox.workspace)


def _resolve_repo() -> str:
    """search_code 查哪个 repo(= LanceDB 表名)。
    显式 config.code_index.repo 优先;缺省回退 workspace 目录名。"""
    cfg = get_app_config()
    repo = getattr(cfg.code_index, "repo", None)
    return repo or _workspace().name


@lru_cache(maxsize=1)
def _retrieval_bundle():
    """懒构造、缓存 (embedder, store, reranker)——只建一次,镜像 sandbox 工具的 _sandbox() 模式。

    reranker 可能为 None(provider=off)。
    """
    from hyperion.services.code_index.embed import create_embedder
    from hyperion.services.code_index.retrieval import create_reranker
    from hyperion.services.code_index.store import LanceDBStore

    cfg = get_app_config()
    embedder = create_embedder(cfg.code_index.embedding)
    vs_cfg = getattr(cfg.code_index, "vector_store", None)
    vs_path = getattr(vs_cfg, "path", "data/code_index") if vs_cfg else "data/code_index"
    store = LanceDBStore(vs_path)
    reranker = create_reranker(getattr(cfg.code_index, "reranker", None))
    return embedder, store, reranker


@lru_cache(maxsize=256)
def _symbols_for_file(abs_path: str, mtime: float):
    """parse 一个文件的符号(以 (路径, mtime) 为缓存键;文件改了 mtime 变 → 自动重 parse)。"""
    from hyperion.services.code_index.parser import parse_file
    return parse_file(abs_path)


# ──────────────────────────────────────────────────────────────────────────
# grep_symbol:按名/正则找符号定义
# ──────────────────────────────────────────────────────────────────────────

@tool("grep_symbol", parse_docstring=True)
def grep_symbol_tool(description: str, name: str, path: str | None = None,
                     regex: bool = False, max_results: int = 50) -> str:
    """按名字(或正则)找符号定义——function/class/method 在哪个 file:line。
    像 IDE 的"转到符号"(Cmd-Shift-O)。不依赖索引,直接解析源码。

    Args:
        description: 为什么要找这个符号(简短)。
        name: 符号名或正则。regex=False(默认)做大小写不敏感子串匹配(匹配简单名 name 或限定名 qualified_name);regex=True 把 name 当正则。
        path: 只在这个目录/文件下找(绝对路径);不填则在整个工作区找。
        regex: True 表示 name 是正则。
        max_results: 最多返回多少条(默认 50)。
    """
    from hyperion.services.code_index.parser import iter_source_files

    ws = _workspace()
    if path:
        scope = Path(path)
        if scope.is_file():
            file_list = [scope]
        elif scope.is_dir():
            file_list = [p for p, _r, _l in iter_source_files(scope)]
        else:
            return f"错误:path 不存在: {path}"
    else:
        file_list = [p for p, _r, _l in iter_source_files(ws)]

    if regex:
        try:
            matcher = re.compile(name, re.IGNORECASE)
        except re.error as e:
            return f"错误:非法正则 /{name}/: {e}"

        def hit(sym):
            return bool(matcher.search(sym.name) or matcher.search(sym.qualified_name))
    else:
        needle = name.lower()

        def hit(sym):
            return needle in sym.name.lower() or needle in sym.qualified_name.lower()

    ws_resolved = ws.resolve()
    out: list[str] = []
    for fpath in file_list:
        try:
            mtime = fpath.stat().st_mtime
        except OSError:
            continue
        for sym in _symbols_for_file(str(fpath), mtime):
            if hit(sym):
                try:  # 显示相对工作区的路径(稳定、短)
                    disp = str(Path(sym.file).resolve().relative_to(ws_resolved))
                except ValueError:
                    disp = sym.file
                sig = sym.signature or ""
                out.append(f"{disp}:{sym.start_line}  {sym.kind}  {sym.qualified_name}{sig}")
                if len(out) >= max_results:
                    out.append(f"...[已达上限 {max_results};收窄 name/path 查更多]")
                    return "\n".join(out)
    return "\n".join(out) if out else f"未找到匹配 '{name}' 的符号。"


# ──────────────────────────────────────────────────────────────────────────
# read_function:读一个符号的完整定义体
# ──────────────────────────────────────────────────────────────────────────

@tool("read_function", parse_docstring=True)
def read_function_tool(description: str, symbol: str, file: str) -> str:
    """读一个符号(函数/类/方法)的完整定义体 + 元数据。
    先用 grep_symbol 拿到 file,再把 qualified_name 填到这里精确定位。不依赖索引。

    Args:
        description: 为什么要读这个符号(简短)。
        symbol: 符号的 qualified_name(如 'Agent.run' 或 'disconnect_cb')。先精确限定名,再简单名,最后子串兜底。
        file: 符号所在文件的绝对路径(从 grep_symbol 结果里取)。
    """
    from hyperion.services.code_index.parser import parse_file

    fpath = Path(file)
    if not fpath.is_file():
        return f"错误:文件不存在: {file}"

    syms = parse_file(fpath)
    target = next((s for s in syms if s.qualified_name == symbol), None)  # ① 精确限定名
    if target is None:
        target = next((s for s in syms if s.name == symbol), None)        # ② 精确简单名
    if target is None:
        low = symbol.lower()
        target = next((s for s in syms if low in s.qualified_name.lower()), None)  # ③ 子串
    if target is None:
        return (f"错误:在 {file} 中找不到符号 '{symbol}'。"
                f"先用 grep_symbol 查它的确切 qualified_name 和 file。")

    try:
        lines = fpath.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as e:
        return f"错误:读不了 {file}: {e}"

    body = "\n".join(lines[target.start_line - 1: target.end_line])
    meta = (f"# {target.kind} {target.qualified_name}"
            f"  @ {file}:{target.start_line}-{target.end_line}"
            + (f"\n# signature: {target.signature}" if target.signature else "")
            + (f"\n# docstring: {target.docstring}" if target.docstring else ""))
    return f"{meta}\n{body}"


# ──────────────────────────────────────────────────────────────────────────
# search_code:语义混合检索(包 P1.3 retrieve)
# ──────────────────────────────────────────────────────────────────────────

@tool("search_code", parse_docstring=True)
def search_code_tool(description: str, query: str, top_k: int = 5) -> str:
    """语义搜索代码:自然语言查询 → 混合检索(BM25+向量+RRF+rerank)→ top-k 代码块。
    返回每条的 file:line + 得分 + 首行片段。需先建索引(uv run hyperion index)。

    Args:
        description: 为什么要搜(简短)。
        query: 自然语言查询,如"蓝牙断连处理"或"RRF 融合是怎么做的"。
        top_k: 返回几条(默认 5)。
    """
    from hyperion.services.code_index.retrieval import retrieve

    try:
        embedder, store, reranker = _retrieval_bundle()
    except Exception as e:
        return f"错误:检索依赖初始化失败(检查 config.code_index / .env): {e}"

    repo = _resolve_repo()
    try:
        if store.count(repo) == 0:  # 表不存在或为空
            return (f"仓库 '{repo}' 还没建索引(或表为空)。先建索引:"
                    f"`uv run hyperion index {repo} <仓库路径>`。")
    except Exception:
        return (f"仓库 '{repo}' 还没建索引。先建索引:"
                f"`uv run hyperion index {repo} <仓库路径>`。")

    try:
        result = retrieve(query, repo, embedder, store, reranker, top_k=top_k)
    except Exception as e:
        return f"错误:检索失败: {e}"

    if not result.hits:
        return f"未找到与 '{query}' 相关的代码(检索路径: {result.out_mode})。"

    ws_resolved = _workspace().resolve()
    out = [f"检索路径: {result.out_mode}  ·  top-{len(result.hits)}"]
    for h in result.hits:
        try:
            disp = str(Path(h.file).resolve().relative_to(ws_resolved))
        except (ValueError, OSError):
            disp = h.file
        first_line = h.text.splitlines()[0][:120] if h.text.splitlines() else ""
        out.append(f"\n--- {disp}:{h.start_line}-{h.end_line}  ({h.kind} {h.symbol})  score={h.score:.3f}")
        out.append(first_line)
    return "\n".join(out)
