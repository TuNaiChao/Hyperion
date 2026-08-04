# src/hyperion/workflows/deep_research/_plan.py
"""deep_research · plan 核心(R3.3.1,窗口展示 · 用户手敲)。被 node_plan 调用。

干什么(面向小白)
-----------------
R3.2 的 node_plan 给每个模块挂的是**同一句写死焦点**(「梳理该模块的职责、公开接口…」),所有模块一视同仁。
本文件把它换成「**一次 LLM batch 调用** → 每个模块拿到**人话名字 + 按模块定制的调研焦点**」。
这样后续 research 子 agent 一上来就知道该模块要重点回答哪些问题,而不是对着通用模板泛泛地读。

核心思路(综合 STORM + DocAgent + deer-flow SKILL.md)
- **STORM 多视角发问**:focus 不再是通用模板,而是 2-4 个针对性子问题(核心数据结构?对外接口?
  主调用链?)+ 1-2 个视角(安全/性能/维护者,按模块特征选)。
- **p0 基础事实视角 always-on**(借 STORM 的 p0 persona):无论附加什么视角,「职责/公开面/入口」
  永远在 → 保报告 §5 骨架(用途/公开面/关键内部类型)不被饿死(STORM ablation:p0 保基线覆盖)。
- **一次 batch**:N 个模块一次 LLM 调用(喂全部候选的 member_files + key_symbols),返 JSON 数组,
  省 N 次冷启动。失败/坏 JSON → 降级通用 focus + 原 CRG 社区名(不阻断,等同 R3.2 行为)。

为什么自建(不复用 extract_items):extract_items 是「文本 → KnowledgeItem」的记忆抽取;这里是
「结构图候选 → 调研计划」的规划,产物形状不同(ModulePlan.focus),且要喂 STORM 视角指令。
DeepSeek-safe:不走 tool_choice/response_format(踩坑:思考模式不支持),改「喂 JSON 形状 + 直出 + regex 解析」。
"""

from __future__ import annotations

import json
import logging
import re

from langchain_core.messages import HumanMessage

from hyperion.workflows.deep_research.state import ModulePlan

logger = logging.getLogger(__name__)

# 附加视角种子(借 deer-flow github-deep-research SKILL.md 的维度矩阵 + STORM 多 persona)。
# LLM 按模块特征从中挑 1-2 个,不是全堆上去(security/robustness 易重叠 → 提示去重)。
_PERSPECTIVE_SEEDS = [
    "安全敏感操作(输入校验/权限/内存安全 —— C 仓尤其注意)",
    "性能与并发(热路径/锁/资源生命周期)",
    "可维护性与耦合(对外暴露面/与其它模块的边界)",
    "状态机与控制流(关键状态转换/异常路径)",
    "与外部交互(内核/硬件/协议/用户态边界)",
]

# R3.2 的通用 focus,降级时复用(等同旧行为,不阻断)。
_GENERIC_FOCUS = (
    "梳理该模块的职责、公开接口(导出函数/类型)、关键数据结构与调用关系;"
    "指出值得注意的设计决策。"
)

_PLAN_PROMPT = """\
你在为代码仓库 **{codebase}** 规划「深度调研」。下面是从结构图(CRG 社区检测)选出的 {n} 个候选模块,
每个给了成员文件样本 + 已知 hub 符号(高连接枢纽)。请为每个模块产出 **人话模块名 + STORM 式调研焦点**。

# 候选模块
{candidates}

# 要求(每个模块产一条)
1. **name**:一句话人话模块名(例:「P2P 设备发现与协商」「驱动适配层(nl80211)」)。
   - 只能基于给的成员文件/符号推断,**别编**不存在的子系统;拿不准就用「X 相关模块」。
   - 别用 community-N 这种机械名。
2. **focus**:该模块的调研焦点 = 2-4 个针对性子问题,结构如下:
   - **p0(必有)**:这模块的职责、公开接口(导出函数/类型)、入口点。
   - **附加视角(选 1-2 个,按模块特征挑,别堆砌;相近视角合并)**,可从这些种子选:
     {seeds}
   - 每个子问题尽量指向「该读哪个符号/文件来回答」,给后续调研子 agent 一个明确起手。

# 输出
**只输出一个 JSON 数组**(无其它正文、无 markdown 围栏),形状:
[{{"id": <原候选 id,原样回填>, "name": "...", "focus": "...(p0 + 1-2 视角的子问题,可换行)"}}]
"""


def _render_candidates(candidates: list[dict]) -> str:
    """把候选模块渲染成 prompt 文本块:每块 = id + 原社区名 + 成员文件样本 + hub 符号。"""
    parts: list[str] = []
    for c in candidates:
        files = c.get("member_files") or []
        syms = c.get("key_symbols") or []
        parts.append(
            f"- id={c.get('id')}  原社区名={c.get('raw_name')}\n"
            f"  成员文件(前 12):{', '.join(files[:12])}\n"
            f"  hub 符号(前 10):{', '.join(syms[:10])}"
        )
    return "\n".join(parts)


def _fallback_one(c: dict) -> ModulePlan:
    """单条降级:原 CRG 社区名 + 通用 focus(等同 R3.2 行为)。"""
    return ModulePlan(
        name=c.get("raw_name") or f"community-{c.get('id')}",
        focus=_GENERIC_FOCUS,
        member_files=c.get("member_files") or [],
        key_symbols=c.get("key_symbols") or [],
    )


def plan_modules(candidates: list[dict], codebase: str, model) -> list[ModulePlan]:
    """一次 LLM batch → 每模块 {人话名, STORM focus}。失败降级(见 _fallback_one)。

    Args:
        candidates: node_plan 备好的候选 [{id, raw_name, member_files, key_symbols}, ...]
                    (已选 top-N、hub 已按 community 分桶)。
        codebase:   仓库名(prompt 里给 LLM 上下文)。
        model:      已建好的 chat model(create_chat_model;DeepSeek-safe,直出 JSON + regex 解析)。

    Returns:
        list[ModulePlan],顺序与 candidates 一致;LLM 没覆盖到的条目走单条降级。
    """
    if not candidates:
        return []

    prompt = _PLAN_PROMPT.format(
        codebase=codebase,
        n=len(candidates),
        candidates=_render_candidates(candidates),
        seeds=" / ".join(_PERSPECTIVE_SEEDS),
    )

    # ① 调 LLM(同步;node_plan 是同步节点)。调用失败 → 整体降级。
    try:
        resp = model.invoke([HumanMessage(content=prompt)])
        text = resp.content if isinstance(resp.content, str) else str(resp.content)
    except Exception:  # noqa: BLE001 - LLM 调用失败不阻断调研
        logger.warning("plan LLM 调用失败,%d 个模块全降级通用 focus", len(candidates), exc_info=True)
        return [_fallback_one(c) for c in candidates]

    # ② 抠 JSON 数组(模型可能前后带闲话;取第一个 [ ... ])。抠不到/坏 JSON → 整体降级。
    m = re.search(r"\[[\s\S]*\]", text)
    if not m:
        logger.warning("plan LLM 没吐 JSON 数组,全降级。响应前 300 字:%s", text[:300])
        return [_fallback_one(c) for c in candidates]
    try:
        arr = json.loads(m.group(0))
    except json.JSONDecodeError:
        logger.warning("plan LLM JSON 解析失败,全降级")
        return [_fallback_one(c) for c in candidates]

    # ③ 按 id 对齐回候选(id 可能是 int/str,统一 str 比对,防 LLM 把 3 写成 "3")。
    #    LLM 可能乱序/漏/多条 → 逐候选查;查不到的走单条降级(其余仍用 LLM 结果,不全盘丢)。
    by_id = {str(item.get("id")): item for item in arr if isinstance(item, dict)}
    plans: list[ModulePlan] = []
    n_named = 0
    for c in candidates:
        hit = by_id.get(str(c.get("id")))
        name = (hit or {}).get("name")
        focus = (hit or {}).get("focus")
        if name and focus and str(name).strip() and str(focus).strip():
            plans.append(
                ModulePlan(
                    name=str(name).strip()[:80],  # 限长,防 LLM 写小作文当模块名
                    focus=str(focus).strip(),
                    member_files=c.get("member_files") or [],
                    key_symbols=c.get("key_symbols") or [],
                )
            )
            n_named += 1
        else:
            plans.append(_fallback_one(c))
    logger.info("plan_modules: %d/%d 模块由 LLM 命名 + STORM focus,%d 条降级通用 focus",
                n_named, len(candidates), len(candidates) - n_named)
    return plans
