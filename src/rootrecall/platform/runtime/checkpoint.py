# src/rootrecall/platform/runtime/checkpoint.py
"""Checkpointer 工厂(对标 deer-flow runtime/checkpointer/provider.py,瘦身版)。

这是什么(面向小白):
  agent 跑到一半挂了(或主动停了),下次想从断点接着跑 —— 得把每步状态存盘,这叫 checkpointer。
  LangGraph 自带两个现成后端:
    - InMemorySaver:存内存,进程退出就没了(只适合测试)。
    - SqliteSaver:存本地 sqlite 文件,进程重启能续(生产用)。
  本模块是个「薄工厂」:按 config 选后端,提供单例(长驻复用)和上下文管理器(一次性)。
  只做 memory + sqlite;postgres 推 R5。设计见 docs/设计/runtime-harness-design.md §4.6。
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Generator
from pathlib import Path

from langgraph.types import Checkpointer

from rootrecall.platform.config import get_app_config

logger = logging.getLogger(__name__)

# 没装 langgraph-checkpoint-sqlite 时的提示(它是个独立包,不随 langgraph 自带)
_SQLITE_INSTALL = "langgraph-checkpoint-sqlite 未装。安装:uv add langgraph-checkpoint-sqlite"
# 默认 sqlite 落盘路径(可被 config.runtime.checkpoint_path 覆盖)
_DEFAULT_SQLITE_PATH = "data/runtime/checkpoint.sqlite"


def _resolve_runtime_cfg() -> tuple[str, str | None]:
    """从 config.yaml 的 runtime 块读 checkpointer 配置。

    返回 (backend, sqlite_path)。runtime 块或 RuntimeConfig 还没加时,默认 (sqlite, None)
    —— 这样 checkpoint.py 在 RuntimeConfig 落地前后都能工作(getattr 容错)。
    """
    try:
        cfg = get_app_config()
    except Exception:
        return "sqlite", None
    rt = getattr(cfg, "runtime", None)
    if rt is None:
        return "sqlite", None
    backend = getattr(rt, "checkpoint_backend", "sqlite") or "sqlite"
    path = getattr(rt, "checkpoint_path", None)
    return backend, path


@contextlib.contextmanager
def _checkpointer_cm(backend: str, sqlite_path: str | None) -> Generator[Checkpointer, None, None]:
    """创建并最终清理一个 checkpointer 的上下文管理器(内部用)。"""
    if backend == "memory":
        from langgraph.checkpoint.memory import InMemorySaver

        logger.info("checkpointer: InMemorySaver(内存,不持久)")
        yield InMemorySaver()
        return

    if backend == "sqlite":
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
        except ImportError as exc:  # pragma: no cover - 依赖缺失时的清晰报错
            raise ImportError(_SQLITE_INSTALL) from exc

        path = sqlite_path or _DEFAULT_SQLITE_PATH
        # 确保父目录存在(SqliteSaver 不会自己建目录)
        Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        # SqliteSaver.from_conn_string 本身是个上下文管理器(with 退出时关连接)
        with SqliteSaver.from_conn_string(path) as saver:
            saver.setup()  # 建表(首次)
            logger.info("checkpointer: SqliteSaver(%s)", path)
            yield saver
        return

    raise ValueError(f"未知 checkpointer 后端: {backend!r}(只支持 memory / sqlite;postgres 推 R5)")


# ── 单例(进程级复用,长驻 agent 用)──────────────────────────────
_checkpointer: Checkpointer | None = None
_checkpointer_ctx: contextlib.AbstractContextManager[Checkpointer] | None = None
_checkpointer_lock = threading.Lock()


def get_checkpointer() -> Checkpointer:
    """全局单例 checkpointer(首次调用按 config 创建,之后复用)。

    长驻 agent / CLI 用这个:连接开一次,进程退出前复用。
    config 解析放在锁外(避免和别的 provider 交叉死锁),再双重检查。
    """
    global _checkpointer, _checkpointer_ctx
    if _checkpointer is not None:
        return _checkpointer
    backend, path = _resolve_runtime_cfg()
    with _checkpointer_lock:
        if _checkpointer is not None:  # 双重检查:等锁期间可能已被别人建好
            return _checkpointer
        ctx = _checkpointer_cm(backend, path)
        cp = ctx.__enter__()
        _checkpointer_ctx = ctx
        _checkpointer = cp
    return _checkpointer


@contextlib.contextmanager
def checkpointer_context() -> Generator[Checkpointer, None, None]:
    """一次性 checkpointer(with 块退出即关连接;CLI 脚本 / 测试用,不缓存)。

    用法:
        with checkpointer_context() as cp:
            graph.invoke(input, config={"configurable": {"thread_id": "t1"}})
    """
    backend, path = _resolve_runtime_cfg()
    with _checkpointer_cm(backend, path) as cp:
        yield cp


def reset_checkpointer() -> None:
    """重置单例:关连接、清缓存(config 改了 / 测试要干净环境时用)。"""
    global _checkpointer, _checkpointer_ctx
    with _checkpointer_lock:
        if _checkpointer_ctx is not None:
            try:
                _checkpointer_ctx.__exit__(None, None, None)
            except Exception:
                logger.warning("checkpointer 清理出错", exc_info=True)
            _checkpointer_ctx = None
        _checkpointer = None
