# src/hyperion/workflows/deep_research/_research.py
# note(zh):用户手敲核心;用 Write 落盘的是干净版(prompt 内不含 ``` 围栏,避免 markdown 截断)。
"""deep_research · research 核心(R3.2,窗口展示 · 用户手敲)。被 node_research 调用。

每模块一个 ReAct 子 agent 并行深挖 + cited-reporter(防幻觉)。

核心思路
- **闭包工具**:grep/read/search 三个 @tool 闭包包住 repo_root,让子 agent 只在目标仓里找。
  复用 code_index.retrieve(语义)+ parse_file(grep/read);替代 workspace-bound 的 code_nav
  工具(那些绑 config.sandbox.workspace,不适用 research 的任意 repo_root)。
- **cited-reporter / emit-concept**:子 agent 的产出契约要求它只断言工具实际返回的符号、
  每条结论带 file:line —— 结构上无法"凭空编"一个不存在的符号(得先 grep/read 到才能引用)。
- **fan-out**:asyncio.gather + semaphore(并发帽 3);每模块一个 create_hyperionagent,
  复用默认中间件链(Summarization + LoopDetection + TokenBudget),长 agent 不爆上下文 / 不死循环。
- **单模型复用**:一个 create_chat_model 实例喂所有子 agent(模型无状态,省冷启动)。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.errors import GraphRecursionError

from hyperion.platform.config import get_app_config
from hyperion.platform.models import create_chat_model
from hyperion.platform.runtime.factory import create_hyperion_agent

# 注意:code_index 这些必须在**模块顶层**导入,不能在 @tool 函数里懒导入。langgraph 的 tool_node
# 用线程池(run_in_executor)跑工具,工具第一次执行时在线程里 import 重模块会触发 _DeadlockError
# (包 __init__ 的导入链争用 import lock)。顶层导入 = 主线程一次性载入,线程里只剩 sys.modules 查表。
from hyperion.services.code_index.embed import create_embedder
from hyperion.services.code_index.parser import iter_source_files, parse_file
from hyperion.services.code_index.retrieval import create_reranker, retrieve
from hyperion.services.code_index.store import LanceDBStore
from hyperion.workflows.deep_research.state import DeepResearchState, ModuleFinding, ModulePlan

logger = logging.getLogger(__name__)

_CONCURRENCY = 3   # 同时跑几个模块子 agent
_MAX_TURNS = 20    # 每子 agent 最多几轮 ReAct(model 调一次工具 + 工具回 = 一轮;防跑飞)。
# LangGraph 的 recursion_limit 按 *superstep*(图节点执行次数)计,不是"轮"。标准 ReAct 图每轮
# = 2 superstep(model 节点吐 tool_call + tools 节点执行),所以 ×2 把"轮"换算成 superstep。
# 真正的防跑飞交给 LoopDetection(相同 tool_call ≥5 硬停)+ TokenBudget(总账兜底);
# recursion_limit 只设一个不误杀合法深调研的上限(踩坑:原先直接把 _MAX_TURNS 当 recursion_limit,
# 实际只给 ~12 轮,wpa 8 模块里 7 个撞 GraphRecursionError)。
_RECURSION_LIMIT = _MAX_TURNS * 2

# 子 agent 系统提示。注意:不用 ``` 围栏(会和外层 markdown 冲突),JSON 形状用纯文本描述。
_RESEARCHER_PROMPT = """\
你在深度调研一个代码仓库的某个模块。用提供的工具(grep_symbol / read_function / search_code)读代码,
然后产出该模块的调研结论。

⏱ 节奏控制(很重要 —— 你有有限的工具调用次数,大约 10-12 次,别用尽):
  目标是写一份**有用的概述**,不是穷尽读完所有代码。按这个高效节奏来:
  1. 先 search_code(用「调研焦点」当 query,一次返回多个相关代码块 —— 比逐个 grep 省 turn)。
  2. 再 grep_symbol 定位 prompt 给的「hub 符号」里的 2-4 个核心符号。
  3. 只 read_function 最关键的 2-4 个函数(入口/核心数据结构/主流程),其余靠 search_code 的片段就够断言。
  4. **立刻输出 JSON 总结** —— 拿到足够写概述的证据就停,把最后一次响应留给 JSON。
  不要为了"全面"把工具调用次数耗光:耗光 = 被强制中断 = 你前面的工作全丢(拿不到任何结论)。
  宁可基于已读到的真实符号写一份精简但准确的概述,也不要读到一半被截断。

硬要求(cited-reporter,防幻觉):
1. 每条结论都必须锚一个真实的 file:line —— 只断言工具实际返回的符号/位置,不许凭空编。
2. 最后只输出一个 JSON 对象(不要其它正文,不要 markdown 代码围栏)。形状:
   {"summary": "该模块职责/关键设计/调用关系的 3-6 句概述(每句尽量带 file:line)",
    "citations": [{"file": "相对路径", "line": 起始行号, "symbol": "限定名", "claim": "该引用支撑的断言"}]}
   其中 line 是整数;file/symbol/claim 是字符串。
"""


# 撞到 recursion_limit 后的强制收尾提示:把子 agent 已收集的证据喂回模型(裸模型,不带工具),
# 逼它只产出 JSON 总结 —— 挽救"模型一直探索不收尾、GraphRecursionError 把工作全丢"的情况。
_FORCED_SUMMARY_PROMPT = """\
下面是你(代码调研子 agent)在撞到调用次数上限前已经收集到的真实代码证据(工具返回的 file:line / 函数体 / 检索片段)。
现在**不再调用任何工具**,直接基于这些已读到的真实信息,输出该模块的调研 JSON 总结。

要求(同调研契约):
- 只断言证据里真实出现的 file:line / 符号,不许凭空编(Verifier 会回查文件是否存在)。
- 只输出一个 JSON 对象(无其它正文、无 markdown 围栏),形状:
  {"summary": "该模块职责/关键设计/调用关系 3-6 句概述(每句尽量带 file:line)",
   "citations": [{"file": "相对路径", "line": 起始行号, "symbol": "限定名", "claim": "该引用支撑的断言"}]}
"""


# ── 闭包工具(包住 repo_root;= cited-reporter 的 emit-concept 基座)──────────


def _build_research_tools(repo_root: str, codebase: str) -> list:
    """造三个闭包工具:grep_symbol / read_function / search_code(都限定在 repo_root 下)。"""

    @tool("grep_symbol", parse_docstring=True)
    def grep_symbol(description: str, name: str, regex: bool = False, max_results: int = 30) -> str:
        """按名字/正则找符号定义(function/class)在哪个 file:line。先靠它定位,再 read_function。

        Args:
            description: 为什么找它(简短)。
            name: 符号名或正则;regex=False 做大小写不敏感子串匹配。
            regex: True 表示 name 是正则。
            max_results: 最多返回多少条。
        """
        files = [p for p, _r, _l in iter_source_files(Path(repo_root))]
        needle = name.lower()
        out: list[str] = []
        for fp in files:
            for sym in parse_file(fp):
                hit = (
                    re.search(name, sym.qualified_name, re.IGNORECASE)
                    if regex
                    else (needle in sym.name.lower() or needle in sym.qualified_name.lower())
                )
                if hit:
                    out.append(f"{sym.file}:{sym.start_line}  {sym.kind}  {sym.qualified_name}")
                    if len(out) >= max_results:
                        break
            if len(out) >= max_results:
                break
        return "\n".join(out) or f"未找到 '{name}'。"

    @tool("read_function", parse_docstring=True)
    def read_function(description: str, symbol: str, file: str) -> str:
        """读一个符号的完整定义体。先 grep_symbol 拿到 file 再来。

        Args:
            description: 为什么读(简短)。
            symbol: qualified_name(如 'wpa_supplicant_init')。
            file: 相对 repo_root 的文件路径(从 grep_symbol 结果取)。
        """
        fp = Path(repo_root) / file
        if not fp.is_file():
            return f"文件不存在: {file}"
        for sym in parse_file(fp):
            if sym.qualified_name == symbol or sym.name == symbol:
                lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
                return "\n".join(lines[sym.start_line - 1 : sym.end_line])
        return f"未在 {file} 找到符号 {symbol}。"

    @tool("search_code", parse_docstring=True)
    def search_code(description: str, query: str, top_k: int = 8) -> str:
        """语义搜索:自然语言 → 混合检索 top-k 代码块(需 node_index 已建索引)。

        Args:
            description: 为什么搜(简短)。
            query: 自然语言(如"P2P scan 的 radio work 生命周期")。
            top_k: 返回几条(默认 8)。
        """
        cfg = get_app_config()
        embedder = create_embedder(cfg.code_index.embedding)
        vs_cfg = getattr(cfg.code_index, "vector_store", None)
        vs_path = getattr(vs_cfg, "path", "data/code_index") if vs_cfg else "data/code_index"
        store = LanceDBStore(vs_path)
        reranker = create_reranker(getattr(cfg.code_index, "reranker", None))
        res = retrieve(query, codebase, embedder, store, reranker, top_k=top_k)
        out: list[str] = []
        for h in res.hits:
            out.append(f"{h.file}:{h.start_line}-{h.end_line}  {h.symbol}\n  {(h.text or '')[:300]}")
        return "\n---\n".join(out) or "无结果(确认已 hyperion index 建索引)。"

    return [grep_symbol, read_function, search_code]


# ── 解析子 agent 答复 → ModuleFinding ─────────────────────────────────────


def _parse_finding(module: str, final_text: str) -> ModuleFinding:
    """从子 agent 答复抠 JSON 对象 → ModuleFinding。抠不到/坏 JSON → 退化为整段 summary(无 citations)。"""
    m = re.search(r"\{[\s\S]*\}", final_text)
    if not m:
        return ModuleFinding(module=module, summary=final_text[:500], citations=[])
    try:
        obj = json.loads(m.group(0))
        return ModuleFinding(module=module, summary=obj.get("summary", ""), citations=obj.get("citations", []))
    except json.JSONDecodeError:
        return ModuleFinding(module=module, summary=final_text[:500], citations=[])


# ── 单模块子 agent + fan-out ─────────────────────────────────────────────


def _compact_evidence(messages: list, *, max_chars: int = 24000) -> str:
    """把子 agent 跑出来的消息压成一段证据文本(保留 file:line),喂回强制收尾用。

    只留工具结果(grep/read/search 的真返回)+ 模型的文字陈述;丢掉纯 tool_call 骨架与初始 prompt。
    超长截断(按时间序保留前部,溯源清晰)。
    """
    parts: list[str] = []
    for m in messages:
        role = getattr(m, "type", None)
        content = getattr(m, "content", "")
        if not content:
            continue
        if role == "tool":
            parts.append(f"[工具结果]\n{content}")
        elif role == "ai" and isinstance(content, str) and content.strip():
            parts.append(f"[模型陈述]\n{content}")
    evidence = "\n\n".join(parts)
    return evidence[:max_chars]


async def _research_one_module(plan_item: ModulePlan, repo_root: str, codebase: str, model) -> ModuleFinding:
    """跑一个模块的 ReAct 子 agent → ModuleFinding(cited)。

    优雅降级(关键):用 astream 流式拿 state。若子 agent 一路探索到 recursion_limit 还没收尾
    (reasoning 模型常见 —— 工具越好越想多看几眼,实测 8/8 撞墙),**不**让 GraphRecursionError
    把已做的工作全丢,而是把已收集的证据喂回裸模型(不带工具)逼它产出 JSON 总结。
    这样每个模块至少拿到一份"基于真实 file:line"的 finding,而不是空白的"(调研失败)"。
    """
    name = plan_item.get("name", "mod")
    tools = _build_research_tools(repo_root, codebase)
    agent = create_hyperion_agent(
        model,
        tools,
        system_prompt=_RESEARCHER_PROMPT,
        name=f"research-{name}",
    )
    prompt = (
        f"模块:{plan_item.get('name')}\n"
        f"调研焦点:{plan_item.get('focus')}\n"
        f"成员文件(参考,不必全读):{', '.join((plan_item.get('member_files') or [])[:15])}\n"
        f"已知 hub 符号(优先看这些):{', '.join((plan_item.get('key_symbols') or [])[:10])}\n"
    )
    cfg: RunnableConfig = {"configurable": {"thread_id": f"research-{name}"}, "recursion_limit": _RECURSION_LIMIT}

    final = ""
    messages: list = []
    try:
        # stream_mode="values":每个 superstep 后吐一次完整 state;只留最后一份(消息最全)。
        async for chunk in agent.astream(
            {"messages": [HumanMessage(content=prompt)]}, config=cfg, stream_mode="values"
        ):
            messages = chunk.get("messages", [])
            for msg in reversed(messages):
                if getattr(msg, "type", None) == "ai" and getattr(msg, "content", ""):
                    final = msg.content if isinstance(msg.content, str) else str(msg.content)
                    break
    except GraphRecursionError:
        # 模型一路探索没收尾 —— 挽救:把已收集证据喂回裸模型强制产出 JSON(不再带工具)。
        logger.warning("research 模块 %s 撞 recursion_limit(%d),降级强制收尾", name, _RECURSION_LIMIT)
        evidence = _compact_evidence(messages)
        if evidence:
            try:
                resp = await model.ainvoke(
                    [HumanMessage(content=f"{_FORCED_SUMMARY_PROMPT}\n\n模块:{name}\n\n# 已收集的证据\n\n{evidence}")]
                )
                forced = resp.content if isinstance(resp.content, str) else str(resp.content)
                if forced and forced.strip():
                    final = forced
            except Exception:  # noqa: BLE001 - 强制收尾也失败就保留已有 final(可能为空 → _parse_finding 降级)
                logger.warning("research 模块 %s 强制收尾失败,用已收集片段", name, exc_info=True)
    return _parse_finding(name, final)


async def _research_modules(state: DeepResearchState) -> list[ModuleFinding]:
    """fan-out:每模块一个子 agent,asyncio.gather + 并发帽 3。单模块失败不连坐。"""
    plan = state.get("plan") or []
    if not plan:
        logger.warning("research: plan 为空,无模块可调研")
        return []
    cfg = get_app_config()
    name = cfg.model_roles.get("researcher") or cfg.model_roles.get("planner") or cfg.models[0].name
    model = create_chat_model(name)

    sem = asyncio.Semaphore(_CONCURRENCY)

    async def guarded(item: ModulePlan) -> ModuleFinding:
        async with sem:
            try:
                return await _research_one_module(item, state["repo_root"], state["codebase"], model)
            except Exception:  # noqa: BLE001 - 单模块失败不连坐
                logger.warning("research 模块 %s 失败", item.get("name"), exc_info=True)
                return ModuleFinding(module=item.get("name", "?"), summary="(调研失败)", citations=[])

    return await asyncio.gather(*(guarded(it) for it in plan))
