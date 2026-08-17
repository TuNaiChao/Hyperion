"""L2 精确导航层 —— clangd 经 multilspy 驱动(P1.5)。

=== 面向小白:这层是干什么的 ===
L1 检索(parser/chunker/embed/retrieval)是"模糊"的——你问"谁调用了 disconnect_cb",
它按意思猜,可能漏。这层用 **clangd**(C/C++ 的"语言服务器",就是 IDE 里"转到定义/
查找引用"背后那个引擎)给你**精确**的调用点:每一处、连宏展开、跨文件、系统头都准。

为什么用 multilspy:它是微软的 Python **LSP 客户端**库,把"和语言服务器对话"的脏活
(JSON-RPC 收发、initialize 握手、文件同步、超时、同步包装)全包了。我们只在它之上写
一层薄薄的 `ClangdServer`——告诉它"怎么把 clangd 起起来、加什么参数"。

⚠️ multilspy 0.0.15(最新发版)**不自带 clangd**(自带 python/rust/go/... 九种,无 C/C++);
其 main 分支虽有官方 clangd 适配器,但 ① 未发 PyPI ② cmd 不带 flag(加不了 --limit-references=0)
③ 有硬编码 completionProvider 断言易碎。故我们在 released 0.0.15 上自写,pin 干净、完全自控。
详见 docs/设计/p1-code-understanding-design.md §5。

=== 硬前提 ===
仓库根要有 compile_commands.json(autotools 用 `bear -- make V=1` 或
`compiledb --parse make -nW V=1`;cmake 用 `cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON`)。
没有它,clangd 不知道每个文件该怎么编译,references 质量骤降(见 §5.7 降级)。
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
import shlex
import shutil
import subprocess
import threading
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from multilspy.language_server import LanguageServer, SyncLanguageServer
from multilspy.lsp_protocol_handler.server import ProcessLaunchInfo
from multilspy.multilspy_config import Language, MultilspyConfig
from multilspy.multilspy_logger import MultilspyLogger

from rootrecall.platform.config import get_app_config

# ──────────────────────────────────────────────────────────────────────────
# 找 clangd / compile_commands.json(纯检测,不启动 server)
# ──────────────────────────────────────────────────────────────────────────

def find_clangd(config=None) -> str | None:
    """找 clangd 可执行:config.lsp.clangd_path 优先,否则 PATH 里 shutil.which。"""
    cfg = config or get_app_config()
    lsp = getattr(cfg.code_index, "lsp", None)
    configured = getattr(lsp, "clangd_path", None) if lsp else None
    return configured or shutil.which("clangd")


def clangd_version(clangd_path: str) -> str | None:
    """跑 `clangd --version` 拿第一行(版本);失败返 None。给 health 用。"""
    try:
        out = subprocess.run(
            [clangd_path, "--version"], capture_output=True, text=True, timeout=5
        )
        return out.stdout.splitlines()[0].strip() if out.stdout else None
    except (OSError, subprocess.SubprocessError):
        return None


def find_compile_commands(repo_root: str, config=None) -> str | None:
    """找 compile_commands.json:config 强制的目录优先,否则仓库根 / build/。"""
    cfg = config or get_app_config()
    lsp = getattr(cfg.code_index, "lsp", None)
    forced = getattr(lsp, "compile_commands_dir", None) if lsp else None
    root = Path(repo_root)
    candidates = []
    if forced:
        candidates.append(Path(forced) / "compile_commands.json")
    candidates += [root / "compile_commands.json", root / "build" / "compile_commands.json"]
    # 注:clangd 真实查找是"从源文件目录向上找",这里只查最常见两处做 health 提示。
    return next((str(p) for p in candidates if p.is_file()), None)


@dataclass
class LSPHealth:
    """lsp health 的检查结果(给 CLI 打印)。"""

    repo_root: str
    clangd_path: str | None
    clangd_version: str | None
    compile_commands: str | None

    @property
    def ok(self) -> bool:
        return bool(self.clangd_path and self.compile_commands)

    def render(self) -> str:
        lines = [f"repo_root: {self.repo_root}"]
        if self.clangd_path:
            lines.append(f"  ✓ clangd: {self.clangd_path}  ({self.clangd_version or '?'})")
        else:
            lines.append("  ✗ clangd: 未安装/找不到")
        if self.compile_commands:
            lines.append(f"  ✓ compile_commands.json: {self.compile_commands}")
        else:
            lines.append("  ✗ compile_commands.json: 未找到")
        if self.ok:
            lines.append("  → L2 精确导航就绪。")
        else:
            lines.append("  → 修复:bash scripts/setup.sh 装 clangd+bear;")
            lines.append("           生成 compile_commands:autotools `bear -- make V=1`,")
            lines.append("           cmake `cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON`(再软链到仓库根)。")
        return "\n".join(lines)


def lsp_health(repo_root: str) -> LSPHealth:
    """不启动 server,只检测 clangd + compile_commands 是否就位。"""
    cfg = get_app_config()
    clangd = find_clangd(cfg)
    return LSPHealth(
        repo_root=str(repo_root),
        clangd_path=clangd,
        clangd_version=clangd_version(clangd) if clangd else None,
        compile_commands=find_compile_commands(repo_root, cfg),
    )


# ──────────────────────────────────────────────────────────────────────────
# ClangdServer:multilspy 的 clangd 适配器(本项目唯一原创 LSP 代码块)
# ──────────────────────────────────────────────────────────────────────────

class ClangdServer(LanguageServer):
    """clangd 经 multilspy 驱动的语言服务器。

    照 multilspy 自带的 rust_analyzer/gopls 模板写(rust-analyzer 与 clangd 同为
    "stdio + 编译型"语言服务,结构最像),只实现三件事,其余全继承 multilspy:
      ① __init__:找 clangd + 拼带 flag 的命令行 + super().__init__(language_id="cpp")
      ② _get_initialize_params:填 rootPath/rootUri/workspaceFolders + 客户端能力
      ③ start_server:注册通知处理器 → 起 server → initialize → initialized → yield
    """

    def __init__(
        self,
        logger,
        repository_root_path: str,
        *,
        clangd_path: str,
        extra_args: tuple[str, ...] = (),
        compile_commands_dir: str | None = None,
    ):
        # multilspy 的 MultilspyConfig.code_language 是给 create() 工厂分发用的;我们绕过工厂
        # 直接实例化,这字段在我们路径里不被读,填个合法占位即可(枚举里没 C/C++,随便给一个)。
        dummy_cfg = MultilspyConfig(code_language=Language.PYTHON)
        cmd = self._build_cmd(clangd_path, extra_args, compile_commands_dir)
        super().__init__(
            dummy_cfg,
            logger,
            repository_root_path,
            ProcessLaunchInfo(cmd=cmd, cwd=repository_root_path),
            "cpp",  # language_id;clangd 按扩展名(.c/.cpp/.h)自己区分 C/C++,填 cpp 无妨
        )
        self.server_ready = asyncio.Event()

    @staticmethod
    def _build_cmd(
        clangd_path: str, extra_args: tuple[str, ...], compile_commands_dir: str | None
    ) -> str:
        """拼 clangd 启动命令行(返回 shell 字符串)。

        ⚠️ multilspy 0.0.15 的 ProcessLaunchInfo.cmd 是 shell 字符串(经 create_subprocess_shell
        启动),不是 list——必须 shlex.join,否则路径/参数带空格会被 shell 拆错。
        """
        flags = [
            "--background-index",  # 后台索引,持久化到 .cache/clangd/index,二次启动快
            "--limit-references=0",  # ★ 不截断 references(默认上限 1000,高频符号会不够,见 §5.6)
            "--limit-results=0",  # 同理 workspace/symbol 也不截断
            "--header-insertion=never",  # 导航场景不要自动插 include
            f"-j={os.cpu_count() or 4}",  # 索引并行度
        ]
        if compile_commands_dir:
            flags.append(f"--compile-commands-dir={compile_commands_dir}")
        flags.extend(extra_args)
        return shlex.join([clangd_path, *flags])

    def _get_initialize_params(self, repository_absolute_path: str):
        """LSP initialize 请求参数。

        刻意不声明 workspace.configuration——否则 clangd 会反发 workspace/configuration 请求
        要 clangd 配置,我们不想处理它;不声明就不会问,clangd 走默认即可。

        注:返回值不标 InitializeParams(TypedDict)——直接 return 字典会被类型检查器
        判成"裸 dict 不符合键清单"。赋给 `params: Any` 拿"免检通行证"(和 multilspy
        自己从 json.load 读配置同款做法),调用处 send.initialize(params) 就不报了。
        """
        root_uri = Path(repository_absolute_path).as_uri()
        params: Any = {
            "processId": os.getpid(),
            "rootPath": repository_absolute_path,
            "rootUri": root_uri,
            "capabilities": {
                "textDocument": {
                    "synchronization": {"didSave": True},
                    "hover": {"contentFormat": ["markdown", "plaintext"]},
                    "definition": {},
                    "references": {},
                }
            },
            "workspaceFolders": [
                {"uri": root_uri, "name": os.path.basename(repository_absolute_path)}
            ],
        }
        return params

    @asynccontextmanager
    async def start_server(self) -> AsyncGenerator[ClangdServer, None]:
        """起 clangd,握手(initialize → initialized),yield 自身,退出时 shutdown/stop。

        刻意不抄 multilspy main 那段对 completionProvider 的硬编码断言(换 clangd 版本就崩);
        也不阻塞等"索引就绪"(clangd initialized 后即可响应,后台索引异步进行,首次 references
        偶尔少召回由上层工具的重试兜——见 §5.5)。
        """

        async def _noop(_params):
            return None

        async def _exec_client_cmd(_params):  # clangd 偶尔发 workspace/executeClientCommand,回空列表
            return []

        async def _log(msg):  # clangd 的 window/logMessage 转成日志
            self.logger.log(f"clangd: {msg}", logging.INFO)

        # 注册 server→client 的通知/请求处理器(必须在 server.start() 之前注册好)
        self.server.on_request("client/registerCapability", _noop)
        self.server.on_request("workspace/executeClientCommand", _exec_client_cmd)
        self.server.on_notification("window/logMessage", _log)
        self.server.on_notification("textDocument/publishDiagnostics", _noop)
        self.server.on_notification("$/progress", _noop)
        self.server.on_notification("language/status", _noop)
        self.server.on_notification("experimental/serverStatus", _noop)

        async with super().start_server():
            self.logger.log("Starting clangd server process", logging.INFO)
            await self.server.start()
            init_resp = await self.server.send.initialize(
                self._get_initialize_params(self.repository_root_path)
            )
            self.logger.log(f"clangd initialized: {bool(init_resp)}", logging.INFO)
            self.server.notify.initialized({})
            self.server_ready.set()
            try:
                yield self
            finally:
                await self.server.shutdown()
                await self.server.stop()


# ──────────────────────────────────────────────────────────────────────────
# 进程级单例(照搬 platform/sandbox/provider.py 的双检锁模式)
# ──────────────────────────────────────────────────────────────────────────

_SERVERS: dict[str, SyncLanguageServer] = {}
_LOCK = threading.Lock()


def get_lsp_server(repo_root: str) -> SyncLanguageServer:
    """懒起、缓存一个常驻 clangd 进程(每个 repo_root 一个),返回其同步门面。

    clangd 起一次要数秒(建索引),不能每个工具调用都重启,故进程级常驻:
    首次进 start_server() ctx(起 loop+线程+clangd+握手),atexit 注册优雅退出。
    镜像 get_sandbox_provider。
    """
    repo_root = str(Path(repo_root).resolve())
    if repo_root in _SERVERS:  # 快路径:已起直接返回
        return _SERVERS[repo_root]

    cfg = get_app_config()
    lsp = getattr(cfg.code_index, "lsp", None)
    clangd = find_clangd(cfg)
    if not clangd:
        raise RuntimeError(
            "clangd 未安装或 PATH 里找不到。装它:`bash scripts/setup.sh`"
            "(Linux: apt install clangd;macOS: Xcode Command Line Tools)。"
        )

    server = ClangdServer(
        MultilspyLogger(),
        repo_root,
        clangd_path=clangd,
        extra_args=tuple(getattr(lsp, "extra_args", []) or []),
        compile_commands_dir=getattr(lsp, "compile_commands_dir", None),
    )
    sync = SyncLanguageServer(server, timeout=int(getattr(lsp, "request_timeout", 15.0)))
    cm = sync.start_server()
    cm.__enter__()  # 阻塞到 clangd initialize 握手完成(秒级;大仓后台索引异步进行)
    atexit.register(cm.__exit__, None, None, None)  # 进程退出优雅 shutdown(否则 daemon 线程被强杀)
    with _LOCK:
        _SERVERS[repo_root] = sync
    return sync


def reset_lsp_server(repo_root: str | None = None) -> None:
    """重置单例(测试用:换 repo 或强制重建)。注:不主动 shutdown 已起的进程。"""
    with _LOCK:
        if repo_root is None:
            _SERVERS.clear()
        else:
            _SERVERS.pop(str(Path(repo_root).resolve()), None)
