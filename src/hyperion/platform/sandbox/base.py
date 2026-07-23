"""沙箱抽象层 —— agent 工具(文件/命令)操作的统一界面。

为什么需要这层抽象(依赖倒置):
  agent 的 bash/read_file 等工具不直接调 subprocess/os,而是依赖这个 `Sandbox`
  接口。P0 用 LocalSandbox(直接在宿主机文件系统跑,开发期);P6 换成 DockerSandbox
  (容器隔离,生产期)时,工具代码一行都不用改。这就是"加新沙箱零改工具"。

对应 deer-flow 的 `Sandbox` ABC(deer-flow/backend/.../sandbox/sandbox.py),这里
按 P0 实际需要做了精简(去掉 download_file/update_file 等)。

详见 docs/architecture.md §4.4。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class PathMapping:
    """一条"虚拟路径 -> 宿主路径"的映射。

    本地沙箱用不到(P0 直接操作宿主路径);它给 P6 的 Docker 沙箱预备:
    容器内 agent 看到 /mnt/user-data/workspace,实际映射到宿主某目录,
    既隐藏宿主真实路径,又让 agent 写的文件落在我们指定的位置。
    """

    container_path: str # 容器/agent 视角的虚拟路径,如 /mnt/user-data/workspace
    local_path: str # 宿主真实路径
    read_only: bool = False


class Sandbox(ABC):
    """文件系统 + 命令执行的统一界面,所有 agent 工具都依赖它。"""

    @abstractmethod # ABC + @abstractmethod:纯接口,强制子类必须实现全部方法,忘实现会当场报错。
    def execute_command(
        self,
        command: str,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> str:
        """执行一条 bash 命令,返回合并后的 stdout+stderr(已做长度截断)。

        Args:
            command: 要执行的 bash 命令字符串。
            env:     额外注入的环境变量(会覆盖被刮除的宿主变量)。
            timeout: 超时秒数;None 表示用沙箱默认值。
        """

    @abstractmethod
    def read_file(
        self,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> str:
        """读取文本文件,可选只读 1-indexed 的行范围 [start_line, end_line]。"""

    @abstractmethod
    def write_file(self, path: str, content: str, append: bool = False) -> None:
        """写文本到文件;append=True 追加而非覆盖。"""

    @abstractmethod
    def list_dir(self, path: str, max_depth: int = 2) -> list[str]:
        """列出目录树(限制深度),返回树形格式的若干行。"""

    @abstractmethod # 方法签名里的 *:它之后的参数(max_results)必须用关键字传,防止调用方按位置误填
    def glob(self, path: str, pattern: str, *, max_result: int = 200) -> list[str]:
        """在 path 下按 fnmatch 模式匹配文件,返回路径列表。"""

    @abstractmethod
    def grep(self, path: str, pattern: str, *, max_result: int = 100) -> list[str]:
        """在 path 下做行级搜索(字面子串匹配),返回 "路径:行号: 行内容"。"""
