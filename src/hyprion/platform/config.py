"""Config: load config.yaml, resolve $ENV vars, expose AppConfig.

Mirrors deer-flow's declarative config + $ENV resolution (see deer-flow
config.example.yaml and deerflow.config.app_config).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

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


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    models: list[ModelConfig] = Field(default_factory=list)
    model_roles: dict[str, str] = Field(default_factory=dict)
    tools: list[dict] = Field(default_factory=list)

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
