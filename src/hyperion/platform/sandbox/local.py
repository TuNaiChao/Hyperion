"""本地沙箱实现 —— 直接在宿主机文件系统上执行(开发期)。

P0 的沙箱就是这一份:它不是容器隔离,而是"在你自己的机器上跑,但加了三道安全闸":
  ① env 刮除(见 env_policy):不把 API key 透传给子进程;
  ② 命令超时 + 进程组 kill:防止 agent 起的命令挂死;
  ③ 输出有界:防止一条命令刷爆 LLM 上下文。
P6 会新增 DockerSandbox(真正隔离),两者实现同一个 Sandbox 接口,工具无感切换。

对应 deer-flow 的 LocalSandbox(deer-flow/backend/.../sandbox/local/local_sandbox.py)。
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
from pathlib import Path

from hyperion.platform.sandbox.base import Sandbox
from hyperion.platform.sandbox.env_policy import build_sandbox_env


def _truncate(text: str, max_chars: int) -> str:
    """超过 max_chars 就截断,并附一行说明。"""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n...[已截断,原文 {len(text)} 字符,此处只显示前 {max_chars}]"


def _kill_process_group(proc: subprocess.Popen) -> None:
    """杀掉整个进程组(因为起命令时用了 start_new_session=True,proc.pid 即组长)。"""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()  # 进程已退出或没权限组杀,兜底杀单个进程


class _BoundedCapture:
    """带内存上限的管道异步读取器（解决子进程输出过大导致的内存爆炸与死锁问题）。

    ========== 【设计背景：为什么需要这个类？】 ==========
    在执行 Shell 命令（尤其是编译、静态分析等耗时任务）时，子进程可能会输出几十 MB 甚至数 GB 的日志。
    1. 痛点一（内存爆炸）：如果直接使用 `subprocess.communicate()`，全部输出会被加载到内存，极易导致 OOM。
    2. 痛点二（死锁）：如果为了限制内存，读取到上限后就强行停止从管道读取，会导致子进程的**管道写端被阻塞**，子进程无法继续写入，从而永久挂起（死锁）。

    ========== 【核心设计：异步抽干 + 有界缓冲】 ==========
    本类通过启动一个**守护后台线程**（Daemon Thread），持续不断地轮询读取管道数据：
    1. 内存上限内（< max_chars）：将读取到的内容切片保留到 `_chunks` 缓冲区中。
    2. 达到内存上限后（>= max_chars）：标记 `truncated = True`，**后续数据继续循环读取，但直接丢弃**。
    —— 通过“一直读但不存”的方式，既能限制内存占用，又能彻底“抽干”管道，确保子进程顺畅运行直到结束。

    ========== 【使用流程与注意事项】 ==========
    1. 实例化：传入已打开的子进程管道（如 `proc.stdout`）和最大字符限制。
    2. 启动：务必在子进程启动后，调用 `.start()` 开启后台读取。
    3. 回收：调用 `.join()` 等待后台线程结束（通常配合 `proc.wait()` 使用）。
    4. 获取结果：调用 `.text()` 获取截断后的完整输出字符串（注意：如果被截断，通过 `.truncated` 属性可知晓）。

    Args:
        pipe: 子进程的标准输出或错误管道（需支持 .read() 方法）。
        max_chars: 允许捕获的最大字符数；超出部分将被截断并丢弃。
    """

    def __init__(self, pipe, max_chars: int):
        self._pipe = pipe
        self._max = max_chars
        self._chunks: list[str] = []
        self._size = 0
        self.truncated = False
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while True:
            chunk = self._pipe.read(4096) # 阻塞读,到 EOF 返回 ""
            if not chunk:
                break
            if not self.truncated and self._size < self._max:
                remaining = self._max - self._size
                piece = chunk[:remaining]
                self._chunks.append(piece)
                self._size += len(piece)
                if len(chunk) > remaining:
                    self.truncated = True
            # 超过上限的部分:循环继续读但不存,纯粹为了抽干管道

    def start(self) -> None:
        self._thread.start()

    def join(self) -> None:
        self._thread.join()

    def text(self) -> str:
        return "".join(self._chunks)


def _iter_files(root: Path):
    """遍历 root 下所有文件(跳过无权限目录交给调用方 try)。"""
    for p in sorted(root.rglob("*")):
        if p.is_file():
            yield p


class LocalSandbox(Sandbox):
    """宿主机文件系统沙箱(开发期)。"""

    def __init__(
        self,
        workspace: Path,
        bash_command_timeout: float = 600.0,
        bash_output_max_chars: int = 20000,
        read_file_output_max_chars: int = 50000,
        ls_output_max_chars: int = 20000,   
    ):
        self.workspace = workspace
        self.bash_command_timeout = bash_command_timeout
        self.bash_output_max_chars = bash_output_max_chars
        self.read_file_output_max_chars = read_file_output_max_chars
        self.ls_output_max_chars = ls_output_max_chars
        self.workspace.mkdir(parents=True, exist_ok=True)  # 工作区不存在就建

    # ---- 命令执行 ----
    def execute_command(
        self,
        command: str,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> str:
        timeout = self.bash_command_timeout if timeout is None else timeout
        try:
            proc = subprocess.Popen(
                ["bash", "-c", command],
                cwd=str(self.workspace),          # 默认在工作区里跑
                env=build_sandbox_env(env),       # 闸①:刮掉密钥的干净环境
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,         # stderr 合并进 stdout,一次性看全
                stdin=subprocess.DEVNULL,         # 读 stdin 的命令立即拿 EOF,不挂住
                start_new_session=True,           # 闸②前提:独立进程组,便于整组 kill
                text=True,
            )
        except FileNotFoundError:
            return "错误:找不到 bash 可执行文件。"

        capture = _BoundedCapture(proc.stdout, self.bash_output_max_chars)
        capture.start()  # 后台抽干输出(闸③:有界)

        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_process_group(proc)            # 闸②:超时杀整组(含其子进程)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            timed_out = True

        capture.join()  # 子进程结束后管道关闭,抽干线程到 EOF 自然退出
        out = capture.text()
        if capture.truncated:
            out += f"\n...[输出超过 {self.bash_output_max_chars} 字符,已截断]"
        if timed_out:
            out += f"\n[命令超时({timeout}s),已终止进程组]"
        out += f"\n[exit code: {proc.returncode}]"
        return out.strip()

    # ---- 文件读写 ----
    def read_file(
        self,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> str:
        from hyperion.platform.sandbox._search import is_probably_binary
        # 二进制守卫:放最前,挡 .o/.so/.png 等(NUL 或非 UTF-8 即拒,指向 bash)
        try:
            if is_probably_binary(Path(path)):
                return (f"错误:'{path}' 是二进制文件,read_file 只支持 UTF-8 文本。"
                        f"用 bash 工具查看(如 `file {path}` 或 `xxd {path}`)。")
        except FileNotFoundError:
            return f"错误:文件不存在: {path}"
        except OSError:
            pass  # 守卫本身读不了 → 交给下面 open 的异常处理

        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except FileNotFoundError:
            return f"错误:文件不存在: {path}"
        except UnicodeDecodeError:
            return (f"错误:'{path}' 不是有效 UTF-8(可能是二进制),read_file 只支持文本。"
                    f"用 bash 工具查看。")

        # 可选行范围(1-indexed,闭区间)
        if start_line is not None or end_line is not None:
            lines = text.splitlines()
            start = (start_line or 1) - 1  # 转成 0-indexed
            end = end_line or len(lines)
            text = "\n".join(lines[start:end])
        return _truncate(text, self.read_file_output_max_chars)

    def write_file(self, path: str, content: str, append: bool = False) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)  # 父目录不存在就建
        mode = "a" if append else "w"
        with open(p, mode, encoding="utf-8") as f:
            f.write(content)

    # ---- 浏览 / 搜索 ----
    def list_dir(self, path: str, max_depth: int = 2) -> list[str]:
        base = Path(path)
        if not base.exists():
            return [f"错误:路径不存在: {path}"]
        lines: list[str] = []
        self._walk_tree(base, 0, max_depth, lines)
        if not lines:
            return [f"(空: {path})"]
        return _truncate("\n".join(lines), self.ls_output_max_chars).splitlines()

    def _walk_tree(self, p: Path, depth: int, max_depth: int, lines: list[str]) -> None:
        if depth > max_depth:
            return
        try:
            entries = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name))
        except (PermissionError, NotADirectoryError):
            return
        for entry in entries:
            indent = "  " * depth
            mark = "/" if entry.is_dir() else ""
            lines.append(f"{indent}{entry.name}{mark}")
            if entry.is_dir() and depth < max_depth:
                self._walk_tree(entry, depth + 1, max_depth, lines)

    def glob(self, path: str, pattern: str, *, max_results: int = 200) -> list[str]:
        base = Path(path)
        results: list[str] = []
        try:
            for p in sorted(base.rglob(pattern)):
                results.append(str(p))
                if len(results) >= max_results:
                    break
        except (PermissionError, OSError):
            pass
        return results

    def grep(self, path: str, pattern: str, *, max_results: int = 100) -> list[str]:
        """在 path 下做行级搜索(正则 + 内建 ignore + 二进制/大小守卫)。返回 'path:line: content'。"""
        from hyperion.platform.sandbox._search import find_grep_matches
        res = find_grep_matches(Path(path), pattern, max_results=max_results)
        return [f"{m.path}:{m.line}: {m.content}" for m in res.matches]