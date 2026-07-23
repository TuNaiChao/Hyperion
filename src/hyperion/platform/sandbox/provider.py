"""沙箱 provider 抽象 + 线程安全单例。

为什么需要 provider 这一层(在 Sandbox 之上再加一层):
  Sandbox 是"一个沙箱实例";provider 是"按需拿到沙箱实例的工厂 + 缓存"。它解决两个
  问题:① 让 config.yaml 用 `use:` 换 provider(本地↔Docker)时,调用方代码不变;
  ② 单例缓存——工具每次调用都 get_sandbox(),不能每次新建(会丢工作区状态、重复建目录)。

对应 deer-flow 的 SandboxProvider + get_sandbox_provider()
(deer-flow/backend/.../sandbox/sandbox_provider.py)。锁内解析类、锁外构造实例,
防插件代码在构造时回调 get_sandbox_provider 造成自死锁。
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from pathlib import Path

from hyperion.platform.config import AppConfig, get_app_config
from hyperion.platform.reflection import resolve_class
from hyperion.platform.sandbox.base import Sandbox
from hyperion.platform.sandbox.local import LocalSandbox


class SandboxProvider(ABC):
    """沙箱实例的工厂 + 缓存接口。"""

    @abstractmethod
    def get_sandbox(self) -> Sandbox:
        """返回(并缓存)当前要用的沙箱实例。"""


class LocalSandboxProvider(SandboxProvider):
    """本地沙箱 provider:从 AppConfig.sandbox 取参,懒建并缓存一个 LocalSandbox。"""

    def __init__(self, config: AppConfig | None = None):
        cfg = config or get_app_config()
        self._sb = cfg.sandbox          # 先存配置,实例惰性建
        self._sandbox: Sandbox | None = None
        self._lock = threading.Lock()

    def get_sandbox(self) -> Sandbox:
        # 双检锁:已建就直接返回,避免每次进入锁
        if self._sandbox is None:
            with self._lock:
                if self._sandbox is None:
                    self._sandbox = LocalSandbox(
                        workspace=Path(self._sb.workspace),
                        bash_command_timeout=self._sb.bash_command_timeout,
                        bash_output_max_chars=self._sb.bash_output_max_chars,
                        read_file_output_max_chars=self._sb.read_file_output_max_chars,
                        ls_output_max_chars=self._sb.ls_output_max_chars,
                    )
        return self._sandbox


# ---- 进程级单例 ----
_provider_lock = threading.Lock()
_provider: SandboxProvider | None = None


def get_sandbox_provider(config: AppConfig | None = None) -> SandboxProvider:
    """全局沙箱 provider 单例。

    首次调用:按 config.sandbox.use 用反射解析 provider 类;后续直接返回缓存。
    锁内只解析类,构造(new 实例)放锁外——防止 provider 构造时回调本函数自死锁
    (照搬 deer-flow,为将来可能回调的插件 provider 留安全余量)。
    """
    global _provider
    if _provider is not None:
        return _provider
    cfg = config or get_app_config()
    with _provider_lock:
        if _provider is not None:  # 二次检查:并发时另一线程可能已建好
            return _provider
        provider_class = resolve_class(cfg.sandbox.use, SandboxProvider)
    # 构造放锁外
    provider = provider_class(cfg)
    with _provider_lock:
        if _provider is None:
            _provider = provider
    return _provider


def reset_sandbox_provider() -> None:
    """重置单例(测试用:换个 workspace 重建)。"""
    global _provider
    with _provider_lock:
        _provider = None