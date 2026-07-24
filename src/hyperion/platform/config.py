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

    name: str  # 工具唯一名,也是 agent 看到的工具名
    group: str  # 分组(sandbox / file:read / file:write ...),工作流按组挂载
    use: str  # 'module.path:variable' 反射目标,如 hyperion.tools.sandbox:bash_tool


class SandboxConfig(BaseModel):
    """沙箱 provider 与可调参数。extra='allow' 给未来 provider 的特有键留口子。"""

    model_config = ConfigDict(extra="allow")

    use: str = "hyperion.platform.sandbox.provider:LocalSandboxProvider"  # provider 反射目标
    workspace: str = "data/sandbox/workspace"  # bash 默认 cwd、输出落点
    bash_command_timeout: float = 600.0  # 单条命令超时(秒),超时杀整组进程
    bash_output_max_chars: int = 20000  # bash 输出截断阈值
    read_file_output_max_chars: int = 50000  # read_file 输出截断阈值
    ls_output_max_chars: int = 20000  # ls 输出截断阈值


class EmbedderConfig(BaseModel):
    """embedding 配置(provider 抽象,见 services/code_index/embed.py)。

    两种 provider:
      - openai_compatible:远端 OpenAI 兼容 API(DashScope / SiliconFlow / OpenAI / vLLM),
        用 base_url + api_key + model,和调 chat 一样配 key。
      - sentence_transformers:本地(需 `uv sync --extra embedding-local`)。
    extra='allow' 给未来新 provider 的特有键留口子。
    """

    model_config = ConfigDict(extra="allow")

    provider: str = "openai_compatible"  # openai_compatible | sentence_transformers
    model: str = "text-embedding-v4"  # 远端模型名;本地模式填 HF 名(如 Qwen/Qwen3-Embedding-0.6B)
    # —— 远端 OpenAI 兼容 ——
    base_url: str | None = None  # DashScope: .../compatible-mode/v1;SiliconFlow: api.siliconflow.cn/v1
    api_key: str | None = None  # $DASHSCOPE_API_KEY 等($ENV 解析后填入)
    dimensions: int | None = None  # Qwen3 系列可调(64-2048);bge-m3 必须留 None(传了报 400)
    batch_limit: int = 10  # 远端每请求文本条数上限(DashScope v4=10)
    # —— 通用 ——
    normalize: bool = True  # 客户端 L2 归一化,保 cosine 一致
    # —— 本地 sentence_transformers ——
    max_seq_length: int = 8192  # ⚠️ 本地必须显式设(ST 默认 512 会静默截断长代码)
    device: str | None = None  # cpu / cuda / mps;None 让库自选
    batch_size: int = 16  # 本地批编码大小(CPU 按内存调)
    hf_endpoint: str | None = "https://hf-mirror.com"  # 本地下载镜像;None 不设
    query_instruction: str | None = "query"  # Qwen3 用 prompt_name="query";bge-m3 留 None


class CodeIndexConfig(BaseModel):
    """代码理解服务配置(P1 分阶段搭建)。

    P1.2 先有 embedding;vector_store / retrieval / repo_map 字段随对应阶段补
    (见 docs/p1-code-understanding-design.md §9)。extra='allow' 让后续阶段子段能增量加。
    """

    model_config = ConfigDict(extra="allow")

    embedding: EmbedderConfig = Field(default_factory=EmbedderConfig)


class AppConfig(BaseModel):
    """顶层配置(models / model_roles / tools / sandbox)。"""

    model_config = ConfigDict(extra="allow")

    models: list[ModelConfig] = Field(default_factory=list)
    model_roles: dict[str, str] = Field(default_factory=dict)
    tools: list[ToolConfig] = Field(default_factory=list)  # 声明式工具列表
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)  # 沙箱 provider + 参数
    code_index: CodeIndexConfig = Field(default_factory=CodeIndexConfig)  # 代码理解服务(P1)

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


def _walk_resolve(obj: Any) -> Any:
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
    assert _CONFIG_CACHE is not None  # load_config() 已给 _CONFIG_CACHE 赋值;这行给类型检查器收窄类型
    return _CONFIG_CACHE
