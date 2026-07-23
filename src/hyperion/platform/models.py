"""Multi-provider chat model factory.

Reflection over `use: module:Class` declared in config.yaml, with
thinking / vision / base_url normalization. Adding a new provider is usually
zero code — just a new config entry. Mirrors deer-flow's `create_chat_model`
(see deer-flow/backend/.../models/factory.py).

See docs/architecture.md §4.1.
"""

from __future__ import annotations

import logging

from langchain.chat_models import BaseChatModel
from langchain_openai.chat_models.base import BaseChatOpenAI

from hyperion.platform.config import AppConfig, get_app_config
from hyperion.platform.reflection import resolve_class

logger = logging.getLogger(__name__)

# Metadata fields consumed by us — never forwarded to the provider client
# (they would be diverted into model_kwargs and crash the request).
_META_FIELDS = {
    "use", "name", "display_name", "description",
    "supports_thinking", "supports_reasoning_effort",
    "when_thinking_enabled", "when_thinking_disabled", "thinking",
    "supports_vision", "pricing",
}

# Reasoning models' first streaming chunk can take 90~150s.
_DEFAULT_STREAM_CHUNK_TIMEOUT = 240.0


def _merge_thinking(when_thinking_enabled: dict | None, thinking: dict | None) -> dict:
    base = dict(when_thinking_enabled or {})
    if thinking:
        base = {**base, "thinking": {**(base.get("thinking") or {}), **thinking}}
    return base


def _normalize_base_url(model_class: type, kw: dict) -> None:
    """OpenAI-compatible clients use `base_url`; users often mis-write `api_base`."""
    if not issubclass(model_class, BaseChatOpenAI):
        return
    if "api_base" not in kw:
        return
    if "base_url" in kw or "openai_api_base" in kw:
        kw.pop("api_base", None)
        return
    kw["base_url"] = kw.pop("api_base")


def create_chat_model(
    name: str | None = None,
    *,
    thinking_enabled: bool = False,
    role: str | None = None,
    config: AppConfig | None = None,
    **overrides,
) -> BaseChatModel:
    """Build a chat model from config.yaml.

    Args:
        name: model logical name; None → role routing → models[0].
        role: 'planner' / 'summarizer' / ... (see config.model_roles).
        thinking_enabled: inject when_thinking_enabled kwargs if supported.
        overrides: per-caller sampling overrides (temperature/max_tokens ...).
    """
    cfg = config or get_app_config()
    if name is None:
        name = (cfg.model_roles.get(role) if role else None) or cfg.model_roles.get("default")
    if name is None and cfg.models:
        name = cfg.models[0].name
    mc = cfg.get_model(name)  # type: ignore[arg-type]
    if mc is None:
        raise ValueError(f"Model '{name}' not found in config.yaml") from None

    model_class = resolve_class(mc.use, BaseChatModel)
    kw = mc.model_dump(exclude_none=True, exclude=_META_FIELDS)
    kw.update({k: v for k, v in overrides.items() if v is not None})

    # thinking on/off normalization across provider families
    has_thinking = (mc.when_thinking_enabled is not None) or (mc.thinking is not None)
    effective_wte = _merge_thinking(mc.when_thinking_enabled, mc.thinking)
    if thinking_enabled and has_thinking:
        if not mc.supports_thinking:
            raise ValueError(
                f"Model '{name}' declares no supports_thinking; set it true to enable."
            ) from None
        if effective_wte:
            kw.update(effective_wte)
    elif not thinking_enabled:
        if mc.when_thinking_disabled:
            kw.update(mc.when_thinking_disabled)
        elif effective_wte:
            kw.setdefault("extra_body", {}).setdefault("thinking", {"type": "disabled"})

    _normalize_base_url(model_class, kw)

    if issubclass(model_class, BaseChatOpenAI):
        # Third-party OpenAI-compatible endpoints silently drop token usage otherwise.
        kw.setdefault("stream_usage", True)
        kw.setdefault("stream_chunk_timeout", _DEFAULT_STREAM_CHUNK_TIMEOUT)

    return model_class(**kw)
