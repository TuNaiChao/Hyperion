# src/rootrecall/platform/runtime/middlewares/tool_output.py
"""ToolOutputBudgetMiddleware —— 工具输出预算中间件(超长工具结果外化 + 摘要)。

这是什么(面向小白):
  agent 调工具(grep / bash / read_file / delegate 返回)时,一次几万行的返回直接塞进上下文就爆。
  本中间件在「工具返回后、进模型前」拦一道:
    - 超过阈值(默认 30K 字符)→ 把全文写到磁盘(outputs_dir),模型可见换成一张
      「结构化摘要 + 文件路径」(摘要由 tool_output_synopsis 纯函数生成);模型需要细节时自己 read_file 取。
    - 磁盘写不了 → 降级 head + tail 截断(行边界对齐,不截断半行)。
  对标 deer-flow ToolOutputBudgetMiddleware,RootRecall 简化:无 sandbox 虚拟路径映射,直接写本地目录。
  设计见 docs/设计/runtime-harness-design.md §4.3。

  两条钩子(对齐 deer-flow):
    - wrap_tool_call:处理「新工具结果」—— 超阈值外化磁盘 + synopsis 摘要(主路径)。
    - wrap_model_call(建议 C 补):每轮兜底「漏网的大 ToolMessage」—— 断点续跑 / 改过阈值
      / 从旧 checkpoint 恢复时,历史里可能混进未经 wrap_tool_call 处理的大消息,这里每轮扫一遍,
      超 fallback_max_chars 的做 head+tail 截断(不外化:已外化的 synopsis 不重复处理)。
      synopsis(~3K)< fallback_max_chars(20K)→ 不二次压(累积靠摘要中间件整体吃掉,见建议 B)。
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Any, override

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

# synopsis 是同包 ../context/ 下的纯函数模块(从 deer-flow 整文件搬)
from rootrecall.platform.runtime.context.tool_output_synopsis import render_tool_output_preview

logger = logging.getLogger(__name__)


# ── 配置 ──────────────────────────────────────────────────────────
@dataclass
class ToolOutputBudgetConfig:
    """工具输出预算配置(对标 deer-flow ToolOutputConfig,瘦身)。"""

    enabled: bool = True
    externalize_min_chars: int = 30_000  # 超过 → 外化到磁盘 + synopsis 摘要
    outputs_dir: str = "data/runtime/tool-outputs"  # 外化落盘根目录
    storage_subdir: str = ".tool-outputs"  # 子目录(防路径穿越校验用)
    preview_head_chars: int = 1_500  # 摘要里附带的 head 原始字符
    preview_tail_chars: int = 1_500  # 摘要里附带的 tail 原始字符
    fallback_max_chars: int = 20_000  # 磁盘失败时的硬截断上限
    fallback_head_chars: int = 10_000
    fallback_tail_chars: int = 10_000
    exempt_tools: set[str] = field(default_factory=set)  # 豁免(不截断)的工具名集合


# ── 文本辅助(照搬 deer-flow tool_output_budget_middleware.py:52-108)──
def _message_text(content: Any) -> str | None:
    """从 ToolMessage.content 抽纯文本;非文本(图片/结构化块)返回 None(跳过预算)。"""
    if isinstance(content, str):
        return content
    if content is None:
        return None
    if isinstance(content, list):  # 多模态 content = list of {"text": ...} / str
        pieces: list[str] = []
        for part in content:
            if isinstance(part, str):
                pieces.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                pieces.append(part["text"])
            else:
                return None
        return "\n".join(pieces) if pieces else None
    return None


def _snap_to_line_boundary(text: str, pos: int) -> int:
    """end 偏移:往回挪到最近的换行(让截断落在完整行,不切半行)。"""
    if pos <= 0 or pos >= len(text):
        return pos
    nl = text.rfind("\n", pos // 2, pos)
    return nl + 1 if nl >= 0 else pos


def _snap_start_to_line_boundary(text: str, pos: int) -> int:
    """start 偏移:往后挪到最近的换行(start 往回会变长,故往前挪)。"""
    if pos <= 0 or pos >= len(text):
        return pos
    half = pos + (len(text) - pos) // 2
    nl = text.find("\n", pos, half)
    return nl + 1 if nl >= 0 else pos


# ── 磁盘外化(简化版:直接写本地 outputs_dir,无 sandbox 映射)——
def _sanitize_tool_name(name: str) -> str:
    base = os.path.basename(name)
    safe = base.replace("..", "").replace("/", "_").replace("\\", "_")
    return safe or "unknown"


def _externalize(
    content: str,
    *,
    tool_name: str,
    outputs_dir: str,
    storage_subdir: str,
) -> str | None:
    """把 content 写到磁盘,返回可被 read_file 读回的路径;失败返回 None。"""
    if os.path.isabs(storage_subdir) or ".." in storage_subdir:  # 防路径穿越
        return None
    storage_dir = os.path.join(outputs_dir, storage_subdir)
    try:
        os.makedirs(storage_dir, exist_ok=True)
    except OSError:
        return None
    ext = "log" if tool_name in {"bash", "bash_tool", "grep"} else "txt"
    filename = f"{_sanitize_tool_name(tool_name)}-{uuid.uuid4().hex[:12]}.{ext}"
    filepath = os.path.join(storage_dir, filename)
    # 二次防穿越:确保最终路径仍在 storage_dir 内
    if not os.path.abspath(filepath).startswith(os.path.abspath(storage_dir) + os.sep):
        return None
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError:
        return None
    return filepath


def _resolve_outputs_dir(request: ToolCallRequest, config: ToolOutputBudgetConfig) -> str:
    """决定外化落盘目录:沙箱在场 → 沙箱可读的 outputs 子目录;否则 config 默认本地路径。

    为什么:外化的文件得让 agent 的 read_file 能读回(否则摘要里给的「文件路径」是死路)。
    R3.2 沙箱化 lead agent 后,SandboxMiddleware 会把 workspace 写进 runtime.state["sandbox"]，
    这里自动取 <workspace>/outputs(R3.2 接）；现在（R3.0）无沙箱 → 走 config.outputs_dir 本地默认。
    对标 deer-flow _resolve_outputs_path（thread_state.py 的 outputs_path），但用 getattr 容错。
    """
    runtime = getattr(request, "runtime", None)
    state = getattr(runtime, "state", None)
    if isinstance(state, dict):
        sandbox_state = state.get("sandbox")
        if isinstance(sandbox_state, dict):
            workspace = sandbox_state.get("workspace")
            if isinstance(workspace, str):
                return os.path.join(workspace, "outputs")
    return config.outputs_dir


def _build_preview(content: str, *, tool_name: str, virtual_path: str, head_chars: int, tail_chars: int) -> str:
    """用 synopsis 渲染「结构化摘要 + 文件路径 + head/tail 原始片段」的预览。"""
    return render_tool_output_preview(
        content,
        tool_name=tool_name,
        virtual_path=virtual_path,
        head_chars=head_chars,
        tail_chars=tail_chars,
    )


def _build_fallback(content: str, *, tool_name: str, max_chars: int, head_chars: int, tail_chars: int) -> str:
    """磁盘不可用时的降级:head + … + tail 截断(行边界对齐)。"""
    total = len(content)
    head_end = _snap_to_line_boundary(content, min(head_chars, max_chars))
    tail_start = _snap_start_to_line_boundary(content, max(0, total - tail_chars))
    if tail_start <= head_end:  # head/tail 重叠 → 只给 head(不超 max_chars)
        snippet = content[:max_chars]
        return snippet + (f"\n... [truncated, full {total} chars]" if total > max_chars else "")
    head = content[:head_end]
    tail = content[tail_start:]
    omitted = total - head_end - (total - tail_start)
    return f"{head}\n... [truncated {omitted} chars; disk externalization unavailable; full {total} chars]\n{tail}"


def _budget_content(
    content: str,
    *,
    tool_name: str,
    tool_call_id: str,
    config: ToolOutputBudgetConfig,
    externalize: bool = True,
) -> str | None:
    """对 content 施加预算。返回替换文本,或 None(无需改)。

    externalize=True(新工具结果主路径):超 externalize_min_chars → 外化磁盘 + synopsis。
    externalize=False(历史漏网兜底,wrap_model_call 调):跳过外化,只做 fallback head+tail 截断。
      为什么历史路径不外化:历史里已有的 ToolMessage 要么是已外化过的 synopsis(~3K,远低于
      fallback_max_chars,该保留不动),要么是漏网的大消息(断点续跑/改过阈值)——后者只需截断,
      重新外化会再写一份磁盘文件 + 生成第二份 synopsis,冗余且 synopsis 累积靠摘要中间件治理(建议 B)。
    """
    # 没超任何阈值 → 不动
    if len(content) <= config.externalize_min_chars and len(content) <= config.fallback_max_chars:
        return None
    # 1) 优先:外化到磁盘 + synopsis 摘要(历史路径 externalize=False 跳过)
    if externalize and len(content) > config.externalize_min_chars:
        vpath = _externalize(
            content,
            tool_name=tool_name,
            outputs_dir=config.outputs_dir,
            storage_subdir=config.storage_subdir,
        )
        if vpath is not None:
            logger.info("Externalized %s output (%d chars) → %s", tool_name, len(content), vpath)
            return _build_preview(
                content,
                tool_name=tool_name,
                virtual_path=vpath,
                head_chars=config.preview_head_chars,
                tail_chars=config.preview_tail_chars,
            )
        logger.warning("Externalize failed for %s; falling back to head+tail", tool_name)
    # 2) 降级:head + tail 硬截断(历史路径走这里:漏网大消息兜底,synopsis 不动)
    if len(content) > config.fallback_max_chars:
        logger.warning("Fallback-truncating %s output: %d → %d max", tool_name, len(content), config.fallback_max_chars)
        return _build_fallback(
            content,
            tool_name=tool_name,
            max_chars=config.fallback_max_chars,
            head_chars=config.fallback_head_chars,
            tail_chars=config.fallback_tail_chars,
        )
    return None


def _patch_tool_message(
    msg: ToolMessage,
    config: ToolOutputBudgetConfig,
    *,
    externalize: bool = True,
) -> ToolMessage:
    """对单条 ToolMessage 施加预算;无改动返回原对象(避免多余 copy)。

    externalize 透传给 _budget_content:历史兜底路径(wrap_model_call 调)传 False。
    """
    tool_name = msg.name or "unknown"
    if tool_name in config.exempt_tools:
        return msg
    text = _message_text(msg.content)
    if text is None:  # 非文本(图片等)不动
        return msg
    replacement = _budget_content(
        text, tool_name=tool_name, tool_call_id=msg.tool_call_id or "", config=config, externalize=externalize
    )
    if replacement is None:
        return msg
    update: dict[str, Any] = {"content": replacement}
    if getattr(msg, "response_metadata", None):
        update["response_metadata"] = dict(msg.response_metadata)
    if getattr(msg, "additional_kwargs", None):
        update["additional_kwargs"] = dict(msg.additional_kwargs)
    return msg.model_copy(update=update)


def _is_over_fallback(msg: ToolMessage, config: ToolOutputBudgetConfig) -> bool:
    """预扫描判据:这条 ToolMessage 是否超 fallback_max_chars(需历史兜底截断)。

    历史路径只兜底「漏网的大消息」(>fallback_max_chars);已外化的 synopsis(~3K)不碰。
    非文本/豁免工具 → False(不在历史兜底范围)。
    """
    if not isinstance(msg, ToolMessage):
        return False
    if (msg.name or "unknown") in config.exempt_tools:
        return False
    text = _message_text(msg.content)
    if text is None:
        return False
    return len(text) > config.fallback_max_chars


def _patch_model_messages(messages: list[Any], config: ToolOutputBudgetConfig) -> list[Any] | None:
    """模型调用前扫历史 ToolMessage:有超 fallback_max_chars 的才兜底截断(不外化)。

    抄 deer-flow _patch_model_messages 的预扫描模式(无超阈值 → 直接返 None 不重建 list):
    synopsis(~3K)< fallback_max_chars(20K)→ 不动(累积靠摘要中间件治,见建议 B;不二次压);
    只有漏网的大消息(>20K)才 fallback 截断。
    返 None = 无需改(不重建 list);有改才返新 list。
    """
    if not any(isinstance(m, ToolMessage) and _is_over_fallback(m, config) for m in messages):
        return None
    updated: list[Any] = []
    changed = False
    for m in messages:
        if isinstance(m, ToolMessage):
            patched = _patch_tool_message(m, config, externalize=False)
            if patched is not m:
                changed = True
            updated.append(patched)
        else:
            updated.append(m)
    return updated if changed else None


def _patch_result(result: ToolMessage | Command, config: ToolOutputBudgetConfig) -> ToolMessage | Command:
    """处理 handler 返回:ToolMessage 直接 patch;Command best-effort patch 其 update.messages。"""
    if isinstance(result, ToolMessage):
        return _patch_tool_message(result, config)
    if isinstance(result, Command):
        # Command.update 可能是 dict(含 "messages")或 list;best-effort patch 内含的 ToolMessage。
        upd = getattr(result, "update", None)
        if isinstance(upd, dict):
            msgs = upd.get("messages")
            if isinstance(msgs, list) and any(isinstance(m, ToolMessage) for m in msgs):
                new_msgs = [_patch_tool_message(m, config) if isinstance(m, ToolMessage) else m for m in msgs]
                # Command 是 dataclass(graph/update/resume/goto);用 dataclasses.replace 换 update 字段
                return replace(result, update={**upd, "messages": new_msgs})
        return result
    return result


# ── 中间件本体 ────────────────────────────────────────────────────
class ToolOutputBudgetMiddleware(AgentMiddleware):
    """工具输出预算中间件:超长工具结果外化磁盘 + synopsis 替换;磁盘失败降级 head+tail。

    用法(由 factory.py 装进中间件链,无需手挂):
        ToolOutputBudgetMiddleware(ToolOutputBudgetConfig(...))
    """

    def __init__(self, config: ToolOutputBudgetConfig | None = None) -> None:
        super().__init__()
        self._config = config if config is not None else ToolOutputBudgetConfig()

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        result = handler(request)
        if not self._config.enabled:
            return result
        outputs_dir = _resolve_outputs_dir(request, self._config)
        cfg = replace(self._config, outputs_dir=outputs_dir) if outputs_dir != self._config.outputs_dir else self._config
        return _patch_result(result, cfg)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        result = await handler(request)
        if not self._config.enabled:
            return result
        outputs_dir = _resolve_outputs_dir(request, self._config)
        cfg = replace(self._config, outputs_dir=outputs_dir) if outputs_dir != self._config.outputs_dir else self._config
        return await asyncio.to_thread(_patch_result, result, cfg)

    # ── 历史兜底(建议 C):每轮扫历史 ToolMessage,兜底漏网的大消息 ──

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        """模型调用前:扫历史 ToolMessage,超 fallback_max_chars 的兜底截断(不外化)。

        价值(对齐 deer-flow):断点续跑 / 改过阈值 / 从旧 checkpoint 恢复时,历史里可能混进未经
        wrap_tool_call 处理的大消息,这里每轮扫一遍兜底。synopsis(~3K)不碰(靠摘要治累积)。
        无超阈值 → 不重建 messages(预扫描返 None)。
        """
        if self._config.enabled:
            messages = getattr(request, "messages", None)
            if isinstance(messages, list):
                patched = _patch_model_messages(messages, self._config)
                if patched is not None:
                    request = request.override(messages=patched)
        return handler(request)

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        if self._config.enabled:
            messages = getattr(request, "messages", None)
            if isinstance(messages, list):
                patched = _patch_model_messages(messages, self._config)
                if patched is not None:
                    request = request.override(messages=patched)
        return await handler(request)
