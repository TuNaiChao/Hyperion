"""Config: load config.yaml, resolve $ENV vars, expose AppConfig.

Mirrors deer-flow's declarative config + $ENV resolution (see deer-flow
config.example.yaml and deerflow.config.app_config).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

_ENV_PATTERN = re.compile(r"^\$(\w+)$")


class ModelConfig(BaseModel):
    """One model entry. `extra='allow'` so provider-specific kwargs pass through."""

    model_config = ConfigDict(extra="allow")

    use: str
    name: str
    display_name: str | None = None
    description: str | None = None
    supports_thinking: bool = False
    supports_reasoning_effort: bool = False
    supports_vision: bool = False
    when_thinking_enabled: dict | None = None
    when_thinking_disabled: dict | None = None
    thinking: dict | None = None
    pricing: dict | None = None


class ToolConfig(BaseModel):
    """config.yaml 里一条工具的声明。

    设计:声明式 + 反射 —— 这里只声明"去哪里加载"(use 字段),真正的工具实现
    在 registry 用 resolve_variable 按 'module:variable' 动态导入。extra='allow'
    让 yaml 里的额外键(如 max_results)原样保留,供工具按需读取。
    """

    model_config = ConfigDict(extra="allow")

    name: str    # 工具唯一名,也是 agent 看到的工具名
    group: str   # 分组(sandbox / file:read / file:write ...),工作流按组挂载
    use: str     # 'module.path:variable' 反射目标,如 hyperion.tools.sandbox:bash_tool


class SandboxConfig(BaseModel):
    """沙箱 provider 与可调参数。extra='allow' 给未来 provider 的特有键留口子。"""

    model_config = ConfigDict(extra="allow")

    use: str = "hyperion.platform.sandbox.provider:LocalSandboxProvider"  # provider 反射目标
    workspace: str = "data/sandbox/workspace"   # bash 默认 cwd、输出落点
    bash_command_timeout: float = 600.0         # 单条命令超时(秒),超时杀整组进程
    bash_output_max_chars: int = 20000          # bash 输出截断阈值
    read_file_output_max_chars: int = 50000     # read_file 输出截断阈值
    ls_output_max_chars: int = 20000            # ls 输出截断阈值


class AppConfig(BaseModel):
    """顶层配置(models / model_roles / tools / sandbox)。"""

    model_config = ConfigDict(extra="allow")

    models: list[ModelConfig] = Field(default_factory=list)
    model_roles: dict[str, str] = Field(default_factory=dict)
    tools: list[ToolConfig] = Field(default_factory=list)            # 声明式工具列表
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)    # 沙箱 provider + 参数

    # YAML 会把"键下面只有注释"的空段(如 config.yaml 现在的 tools:)解析成 None;
    # 而 pydantic 把显式 None 当成"有值"而非"用默认值",会校验失败。
    # 这里在赋值前把 None 强转成空集合,让空段也能正常解析。
    @field_validator("models", "tools", mode="before")
    @classmethod
    def _coerce_none_to_list(cls, v: Any) -> Any:
        return v if v is not None else []

    @field_validator("model_roles", mode="before")
    @classmethod
    def _coerce_none_to_dict(cls, v: Any) -> Any:
        return v if v is not None else {}

    def get_model(self, name: str) -> ModelConfig | None:
        for m in self.models:
            if m.name == name:
                return m
        return None


def _resolve_env(value):
    if isinstance(value, str):
        m = _ENV_PATTERN.match(value.strip())
        if m:
            return os.environ.get(m.group(1), "")
    return value


def _walk_resolve(obj):
    if isinstance(obj, dict):
        return {k: _walk_resolve(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk_resolve(v) for v in obj]
    return _resolve_env(obj)


_CONFIG_CACHE: AppConfig | None = None


def _default_config_path() -> Path:
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        candidate = parent / "config" / "config.yaml"
        if candidate.exists():
            return candidate
    return Path.cwd() / "config" / "config.yaml"


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load and parse config.yaml into AppConfig (with $ENV resolved)."""
    global _CONFIG_CACHE
    p = Path(path) if path else _default_config_path()
    if not p.exists():
        raise FileNotFoundError(f"config not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    cfg = AppConfig(**_walk_resolve(raw))
    _CONFIG_CACHE = cfg
    return cfg


def get_app_config() -> AppConfig:
    """Cached access to the parsed config."""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is None:
        load_config()
    return _CONFIG_CACHE
