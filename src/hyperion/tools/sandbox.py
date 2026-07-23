"""agent 可调用的沙箱工具(@tool 包装)。

这些工具把 LocalSandbox 的能力暴露成 LangChain BaseTool,供 ReAct agent 调用。
每个工具函数的约定:
  - 用 @tool(name, parse_docstring=True) 装饰 —— docstring 的 Args 段自动变成 JSON schema;
  - 首参 description: str 是给模型用的"为什么调用此命令"(deer-flow 同款约定,提升可解释性);
  - 内部用 get_sandbox_provider().get_sandbox() 拿到当前沙箱(调用时解析,不在 import 期)。

对应 deer-flow 的 sandbox/tools.py(deer-flow/backend/.../sandbox/tools.py),P0 精简版。
"""

from __future__ import annotations

from langchain_core.tools import tool

from hyperion.platform.sandbox import get_sandbox_provider


def _sandbox():
    """获取当前沙箱单例(调用时解析,避开 import 期副作用)。"""
    return get_sandbox_provider().get_sandbox()


@tool("bash", parse_docstring=True)
def bash_tool(description: str, command: str) -> str:
    """在沙箱里执行一条 bash 命令。

    长时间运行的任务(如启动服务)务必放到后台并重定向输出,例如:
    `your-command > server.log 2>&1 &`。
    命令默认在沙箱工作区里执行;超时会被终止进程组;输出有长度上限。

    Args:
        description: 用几个字说明为什么要跑这条命令(必填,先于 command 给出)。
        command: 要执行的 bash 命令。文件路径尽量用绝对路径。
    """
    return _sandbox().execute_command(command)


@tool("read_file", parse_docstring=True)
def read_file_tool(
    description: str,
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    """读取一个文本文件的内容,可选只看某段行范围。

    Args:
        description: 为什么要读这个文件(简短)。
        path: 文件绝对路径。
        start_line: 起始行号(1-indexed,含)。不填则从头读。
        end_line: 结束行号(1-indexed,含)。不填则读到尾。
    """
    return _sandbox().read_file(path, start_line=start_line, end_line=end_line)


@tool("write_file", parse_docstring=True)
def write_file_tool(description: str, path: str, content: str, append: bool = False) -> str:
    """把文本写入文件(默认覆盖;append=True 则追加到末尾)。父目录不存在会自动创建。

    Args:
        description: 为什么要写这个文件(简短)。
        path: 目标文件绝对路径。
        content: 要写入的文本内容。
        append: True 表示追加而非覆盖。
    """
    _sandbox().write_file(path, content, append=append)
    return f"OK: 已写入 {path}{'(追加)' if append else '(覆盖)'}"


@tool("str_replace", parse_docstring=True)
def str_replace_tool(
    description: str,
    path: str,
    old_str: str,
    new_str: str,
    replace_all: bool = False,
) -> str:
    """对文件做字面子串替换(非正则)。默认只替换第一处;replace_all=True 替换全部。

    Args:
        description: 为什么要改这个文件(简短)。
        path: 目标文件绝对路径。
        old_str: 要被替换的子串(必须精确匹配,含空白/缩进)。
        new_str: 替换成的内容。
        replace_all: True 替换全部匹配处;False 只替换第一处。
    """
    # 最小实现:读全文 -> 替换 -> 写回。注意 read_file 有 50000 字符截断,
    # 超大文件会丢数据;待 P2 对齐 deer-flow 的 read-before-write hash 门(见 backlog)。
    sb = _sandbox()
    text = sb.read_file(path)
    count = text.count(old_str)
    if count == 0:
        return f"错误:在 {path} 中找不到该子串,未做修改。"
    new_text = text.replace(old_str, new_str, -1 if replace_all else 1)
    sb.write_file(path, new_text)
    done = count if replace_all else 1
    return f"OK: 在 {path} 替换了 {done} 处(文件中共匹配 {count} 处)。"


@tool("ls", parse_docstring=True)
def ls_tool(description: str, path: str) -> str:
    """列出一个目录的树形结构(默认最多 2 层)。

    Args:
        description: 为什么要列这个目录(简短)。
        path: 目录绝对路径。
    """
    lines = _sandbox().list_dir(path)
    return "\n".join(lines) if lines else f"(空目录: {path})"