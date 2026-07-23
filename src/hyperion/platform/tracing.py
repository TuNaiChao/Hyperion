"""可观测性:Langfuse 回调构造(可选)。

Langfuse 走 LangChain CallbackHandler;挂在图调用根(config['callbacks']),一个 run 一条
trace,所有 node/LLM/tool 是子 span。仅当三个 LANGFUSE_* 环境变量齐备时启用,否则返回空
列表(零开销、不阻断运行)。Langfuse v4 的 CallbackHandler 只在 on_chain_start(parent_run_id=None)
时读取 trace 属性,所以 metadata 也必须挂在图根 RunnableConfig.metadata。

对应 deer-flow 的 tracing/factory.py + tracing/metadata.py。
"""

from __future__ import annotations

import os
from typing import Any


def _langfuse_configured() -> bool:
    """三个 LANGFUSE_* 环境变量都非空才算配置了(见 .env.example)。"""
    return all(
        os.environ.get(k)
        for k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST")
    )


def build_tracing_callbacks() -> list[Any]:
    """返回要挂在图根的回调列表。

    未配置 Langfuse 或未安装 langfuse 包时,返回 [](静默降级,不报错)。
    要启用:`uv add langfuse` + 在 .env 填三个 LANGFUSE_* 变量。
    """
    if not _langfuse_configured():
        return []
    try:
        from langfuse import Langfuse
        from langfuse.langchain import CallbackHandler as LangfuseCallbackHandler
    except ImportError:
        # 配了 key 但没装包:静默跳过,不阻断运行
        return []
    # 初始化 Langfuse 全局单例(CallbackHandler 内部用它上报)
    Langfuse(
        secret_key=os.environ["LANGFUSE_SECRET_KEY"],
        public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
        host=os.environ["LANGFUSE_HOST"],
    )
    return [LangfuseCallbackHandler(public_key=os.environ["LANGFUSE_PUBLIC_KEY"])]


def tracing_metadata(
    *,
    thread_id: str | None = None,
    user_id: str | None = None,
    model_name: str | None = None,
) -> dict[str, Any]:
    """构造 Langfuse trace 属性,挂到 RunnableConfig.metadata。

    未配置 Langfuse 时返回 {}(调用方可无条件 merge)。
    """
    if not _langfuse_configured():
        return {}
    meta: dict[str, Any] = {"langfuse_user_id": user_id or "default"}
    if thread_id:
        meta["langfuse_session_id"] = thread_id  # 一个 thread = Langfuse 一个 session
    if model_name:
        meta["langfuse_tags"] = [f"model:{model_name}"]
    return meta
