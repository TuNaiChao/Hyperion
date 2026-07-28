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
from typing import Any

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


# ──────────────────────────────────────────────────────────────────────────
# L2 精确导航工具(clangd/LSP):find_references / goto_definition / hover
# 经 services/code_index/lsp.py 的 ClangdServer + get_lsp_server 单例。
# ──────────────────────────────────────────────────────────────────────────

def _lsp_repo_root() -> Path:
    """LSP 服务的 repo 根(= clangd 工作区根,通常 = workspace 目录)。

    config.lsp.compile_commands_dir 强制时用它;否则回退 workspace(和 L1 工具一致)。
    """
    cfg = get_app_config()
    lsp = getattr(cfg.code_index, "lsp", None)
    cc = getattr(lsp, "compile_commands_dir", None) if lsp else None
    return Path(cc) if cc else _workspace()


def _symbol_column(file_path: Path, line_1: int, symbol: str) -> int | None:
    """在 file 的第 line_1 行(1-based)找 symbol 首次出现的列(0-based)。

    LSP 要精确 (line, column) 指在符号上;工具只收 file+line+symbol,这里把 symbol
    在该行的列位置解出来。找不到(行里没这符号)返 None。
    """
    try:
        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    if line_1 < 1 or line_1 > len(lines):
        return None
    idx = lines[line_1 - 1].find(symbol)
    return None if idx < 0 else idx  # 注:LSP column 按 UTF-16 code unit,纯 ASCII == 字符索引;CJK 近似(记 backlog)


def _rel(path: str, ws: Path) -> str:
    """绝对路径显示成相对 workspace 的(短、稳);不在 workspace 下就原样。"""
    try:
        return str(Path(path).resolve().relative_to(ws.resolve()))
    except (ValueError, OSError):
        return path


def _render_locations(locs: Any, ws: Path) -> str:
    """把 LSP Location 列表渲染成 'file:line:col  <首行片段>' 文本(给 LLM 看)。"""
    if not locs:
        return ""
    seen: set[tuple[str, int]] = set()  # 按 (file, line) 去重(宏展开会多次命中同行)
    rows: list[str] = []
    for loc in locs:
        uri = loc.get("uri", "")
        rng = loc.get("range", {}).get("start", {})
        line, col = rng.get("line", 0), rng.get("character", 0)
        path = uri[7:] if uri.startswith("file://") else uri  # 去 file:// 前缀
        if (path, line) in seen:
            continue
        seen.add((path, line))
        snippet = ""
        try:
            tlines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
            if 0 <= line < len(tlines):
                snippet = "  " + tlines[line].strip()[:120]
        except OSError:
            pass
        rows.append(f"{_rel(path, ws)}:{line + 1}:{col + 1}{snippet}")
    return "\n".join(rows)


def _do_lsp_request(kind: str, symbol: str, file: str, line: int,
                    description: str, max_results: int | None = None) -> str:
    """三个 LSP 工具的共用实现:解列号 → 取 clangd 单例 → 请求(空则重试)→ 渲染。

    任何环节失败都返**可操作错误串**(不抛、不静默空)——空结果会被 agent 误判"没人调用"。
    """
    import time

    from hyperion.services.code_index.lsp import get_lsp_server, lsp_health

    fpath = Path(file)
    if not fpath.is_file():
        return f"错误:文件不存在: {file}"

    ws = _lsp_repo_root()
    try:
        rel = fpath.resolve().relative_to(ws.resolve())  # multilspy 收相对 repo 根的路径
    except ValueError:
        rel = Path(file)  # 不在 repo 根下:退化用绝对(multilspy 的 os.path.join 仍能定位)

    col = _symbol_column(fpath, line, symbol)
    if col is None:
        return (f"错误:在第 {line} 行找不到符号 '{symbol}' 的列位置(行内容里没这个名字)。"
                f"先用 read_file 核对 {file}:{line} 的确切符号名/行号。")

    # health 提示(不启动 server):compile_commands 缺失给警告但继续(heuristic 模式)
    health = lsp_health(str(ws))
    warn = "" if health.compile_commands else (
        "  ⚠️ 未找到 compile_commands.json,clangd 走 heuristic 模式,references 质量降级。\n"
        "     生成:autotools `bear -- make V=1` / cmake `cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON`。\n"
    )

    try:
        sync = get_lsp_server(str(ws))
    except Exception as e:
        return f"错误:启动 clangd 失败: {e}\n  先跑 `uv run hyperion lsp health` 自检。"

    line0, col0 = line - 1, col  # LSP 0-based
    cfg = get_app_config()
    lsp_cfg = getattr(cfg.code_index, "lsp", None)
    retries = getattr(lsp_cfg, "index_retry", 1) if lsp_cfg else 1
    delay = getattr(lsp_cfg, "index_retry_delay", 0.3) if lsp_cfg else 0.3

    result = None
    for attempt in range(retries + 1):
        try:
            with sync.open_file(str(rel)):
                if kind == "references":
                    result = sync.request_references(str(rel), line0, col0)
                elif kind == "definition":
                    result = sync.request_definition(str(rel), line0, col0)
                else:  # hover
                    result = sync.request_hover(str(rel), line0, col0)
        except Exception as e:
            return f"错误:LSP {kind} 请求失败: {e}"
        # references/definition 有结果就停;空且还能重试 → 等一下再试(后台索引可能没建完)
        if kind == "hover" or result:
            break
        if attempt < retries:
            time.sleep(delay)

    if kind == "hover":
        if not result:
            return f"{warn}无 hover 信息(符号可能不是可悬停目标,或索引未就绪 → 重试)。"
        hover: Any = result  # result 是 multilspy Hover(TypedDict);标 Any 解耦,不绑死键形状
        contents = hover.get("contents", result) if isinstance(result, dict) else result
        if isinstance(contents, dict):
            text = contents.get("value", str(contents))
        elif isinstance(contents, list):
            text = "\n".join(c.get("value", str(c)) if isinstance(c, dict) else str(c) for c in contents)
        else:
            text = str(contents)
        return f"{warn}# hover: {symbol} @ {file}:{line}\n{text.strip()}"

    locs: Any = result or []
    footer = ""
    if max_results is not None and len(locs) > max_results:
        total = len(locs)
        locs = locs[:max_results]
        footer = f"\n...[共 {total} 处,已截断到 {max_results};调大 max_results 查全]"
    rendered = _render_locations(locs, ws)
    if not rendered:
        noun = "调用点/引用" if kind == "references" else "定义"
        return f"{warn}未找到 '{symbol}' 的{noun}(确认符号名/行号,或 clangd 索引未就绪 → 重试)。"
    label = "调用点/引用" if kind == "references" else "定义"
    return f"{warn}# {symbol} 的{label}({len(locs)} 处)@ 来自 {file}:{line}\n{rendered}{footer}"


@tool("find_references", parse_docstring=True)
def find_references_tool(description: str, symbol: str, file: str, line: int,
                         max_results: int = 50) -> str:
    """L2 精确查找:谁"引用/调用"了这个符号(clangd textDocument/references)。

    比 search_code/grep 精确:连宏展开、跨文件、系统头都准,不会漏同名。需仓库有
    compile_commands.json(见 `hyperion lsp health`)。

    Args:
        description: 为什么要查(简短)。
        symbol: 符号名(用来在该行定位列号)。
        file: 符号所在文件的绝对路径。
        line: 符号所在行(1-based,从 grep_symbol/read_function 结果取)。
        max_results: 最多返回多少条调用点(默认 50)。
    """
    return _do_lsp_request("references", symbol, file, line, description, max_results)


@tool("goto_definition", parse_docstring=True)
def goto_definition_tool(description: str, symbol: str, file: str, line: int) -> str:
    """L2 精确跳转:这个符号"定义"在哪(clangd textDocument/definition,含系统头宏展开)。

    Args:
        description: 为什么要跳(简短)。
        symbol: 符号名(用来定位列号)。
        file: 符号被使用处的文件绝对路径。
        line: 符号被使用处的行(1-based)。
    """
    return _do_lsp_request("definition", symbol, file, line, description)


@tool("hover", parse_docstring=True)
def hover_tool(description: str, symbol: str, file: str, line: int) -> str:
    """L2 查符号的类型/签名/宏展开/文档(clangd textDocument/hover)。

    Args:
        description: 为什么要查(简短)。
        symbol: 符号名(用来定位列号)。
        file: 符号所在文件绝对路径。
        line: 符号所在行(1-based)。
    """
    return _do_lsp_request("hover", symbol, file, line, description)