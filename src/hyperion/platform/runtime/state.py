# src/hyperion/platform/runtime/state.py
"""Hyperion agent 运行时状态 schema(对标 deer-flow ThreadState,瘦身起步)。

这是什么(面向小白):
  LangGraph 的 agent 跑起来时,要在各步骤间传一份"状态"(state)。这份 state 就是
  Hyperion 自己的 lead agent(深度调研那种长 agent)的工作台 —— 对话历史 + 摘要 + 委托账本。
  对标 deer-flow 的 ThreadState,但 **R3 先只落三样**(messages / summary_text / delegations);
  sandbox / title / image / artifact / skill_context / goal 等**暂不引入,后续按需扩展**(见末尾)。
  设计见 docs/设计/runtime-harness-design.md。
"""

from __future__ import annotations

from typing import Annotated, NotRequired, TypedDict, cast

# AgentState 是 langchain 1.x agents 模块内置的状态基类:
# 自带 messages 字段 + add_messages 合并 reducer(追加/同 id 替换/RemoveMessage 删除)。
from langchain.agents import AgentState

# —— 委托账本(记录每次把活派给 opencode/omp 的结果)——————————————
# 对标 deer-flow thread_state.py:146/149;上限防账本无限增长。
_DELEGATION_LEDGER_MAX_ENTRIES = 50

# 终态集合:status 取自 tools/delegate.py 的 DelegateStatus(小写)。
# DelegateStatus.OK/TIMEOUT/ERROR/SCHEMA 都是"本次委托已结束",故都算终态。
TERMINAL_STATUSES: frozenset[str] = frozenset({"ok", "timeout", "error", "schema"})


class DelegationEntry(TypedDict):
    """一次 CodingAgentDelegate 委托的账本条目(对标 deer-flow DelegationEntry)。

    字段贴合 Hyperion 的 DelegateResult(tools/delegate.py):
      - id          本次委托唯一 id(去重/同 id 更新用)
      - delegate    委托后端:"opencode" / "omp" / "claude"(对标 deer-flow 的 subagent_type)
      - description 干了啥(一句话)
      - status      ok / timeout / error / schema(见 DelegateStatus)
      - result_brief 结果摘要(成功时根因/补丁概要)
      - stop_reason 守卫终止原因:token_capped / turn_capped / loop_capped(可加性,不破坏 status)
      - created_at  ISO 时间戳
    """

    id: str
    delegate: str
    description: str
    status: str
    result_brief: NotRequired[str]
    stop_reason: NotRequired[str]
    created_at: str


def merge_delegations(
    existing: list[DelegationEntry] | None,
    new: list[DelegationEntry] | None,
) -> list[DelegationEntry]:
    """delegations 通道的 reducer(照搬 deer-flow thread_state.py:165 的契约)。

    规则(面向小白)—— 委托账本像一本"外勤派遣记录":
      - 新条目为空 → 保留旧账本。
      - 追加新条目;同 id 的用最新版替换,但保留首次出现顺序。
      - 终态(已 ok/timeout/error/schema)永不被非终态覆盖(外勤已交差,不许退回"进行中")。
      - 最多留最近 50 条(防账本无限增长)。
    """
    if not new:
        return existing or []

    by_id: dict[str, DelegationEntry] = {}
    order: list[str] = []
    for entry in [*(existing or []), *new]:
        entry_id = entry["id"]
        previous = by_id.get(entry_id)
        # 终态保护:旧的已终态、新的非终态 → 跳过(不让"已完成"退回"进行中")
        if previous is not None and previous["status"] in TERMINAL_STATUSES and entry["status"] not in TERMINAL_STATUSES:
            continue
        if entry_id not in by_id:
            order.append(entry_id)
        # ← 加 "previous is not None" 让 pyright 把 previous 收窄成 DelegationEntry,
        #   否则 previous 仍是「DelegationEntry | None」,下面 .get/[] 都会报错。
        elif previous is not None and previous.get("created_at"):
            # 保留首次创建时间(更新内容不刷新 created_at)。
            # 注意:{**entry, ...} 把 TypedDict 展开成普通 dict,pyright 认不出它还是
            #       DelegationEntry,所以 cast 一下保持类型(纯类型提示,运行时零开销)。
            entry = cast(DelegationEntry, {**entry, "created_at": previous["created_at"]})
        by_id[entry_id] = entry

    merged = [by_id[entry_id] for entry_id in order]
    if len(merged) > _DELEGATION_LEDGER_MAX_ENTRIES:
        merged = merged[-_DELEGATION_LEDGER_MAX_ENTRIES:]
    return merged


class HyperionState(AgentState):
    """Hyperion lead agent 的状态(R3 最小集,可扩展)。

    字段(面向小白):
      - messages     对话历史(AgentState 自带,带 add_messages 合并 reducer)。
      - summary_text 历史压缩后的摘要(独立通道;summarization 中间件写入,R3 落地,先占位)。
      - delegations  委托账本(每次派 opencode/omp 干活的记录,merge_delegations 合并)。

    暂不引入(R3 范围,后续按需加,见模块末尾「扩展指南」):
      sandbox / thread_data / title / artifacts / todos /
      uploaded_files / viewed_images / promoted / skill_context / goal ——
      这些是 deer-flow 的字段,Hyperion 现阶段用不到,但 schema 任何时候都能加回来。
    """

    # LastValue 通道(默认覆盖语义);summarization 中间件写入,R3 落地
    summary_text: NotRequired[str | None]
    # 自定义 reducer:同 id 最新赢 + 终态保护 + 首序保留 + 上限 50
    delegations: Annotated[list[DelegationEntry], merge_delegations]


# ──────────────────────────────────────────────────────────────────
# 扩展指南(面向后续阶段:怎么给 HyperionState 加字段)
# ──────────────────────────────────────────────────────────────────
# 加字段有两扇门,按"谁拥有这字段"选,都不用改核心逻辑:
#
# ① 简单情况(某字段就 HyperionState 自己用)→ 直接在本 class 加:
#       new_field: NotRequired[str | None]
#       或带 reducer:foo: Annotated[list[X], merge_foo]
#   TypedDict 天然可扩展,加了就能用。
#
# ② 生产级做法(某字段归某个中间件管,如未来的 SandboxMiddleware 管 sandbox 字段)
#   → 不动 HyperionState,在**中间件类**上声明 state_schema:
#       class SandboxMiddleware(AgentMiddleware):
#           state_schema = SandboxState   # ← 中间件自带 state 定义
#   然后 factory.py 调 langgraph 的 normalize_middleware_state_schema() 把各中间件
#   的 state_schema 合并进图的总 schema(对标 deer-flow factory.py:157)。
#   好处:中间件自包含、即插即用;核心 state 保持精简。← 这条是 deer-flow 30+ 中间件
#   能堆叠却互不干扰的关键,Hyperion factory(R3.0 后续展示)会照搬这个合并机制。
#
# 预留映射(到阶段):
#   sandbox        ← R5 Docker 隔离(SandboxMiddleware)
#   skill_context  ← 若引入 skill 机制(SkillActivationMiddleware)
#   artifacts      ← 若 lead agent 要渲染产物卡(目前不需要)
#   goal           ← 若要做 goal-continuation(R5 生产化再议)
