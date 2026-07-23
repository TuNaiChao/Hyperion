"""沙箱提供方:本地沙箱(宿主文件系统,开发期);后续 P6 加 Docker 沙箱(生产期)。

详见 docs/architecture.md §4.4。
"""

from hyperion.platform.sandbox.base import PathMapping, Sandbox
from hyperion.platform.sandbox.env_policy import build_sandbox_env, is_blocked_env_name
from hyperion.platform.sandbox.local import LocalSandbox
from hyperion.platform.sandbox.provider import (
    LocalSandboxProvider,
    SandboxProvider,
    get_sandbox_provider,
    reset_sandbox_provider,
)

__all__ = [
    "PathMapping",
    "Sandbox",
    "build_sandbox_env",
    "is_blocked_env_name",
    "LocalSandbox",
    "LocalSandboxProvider",
    "SandboxProvider",
    "get_sandbox_provider",
    "reset_sandbox_provider",
]