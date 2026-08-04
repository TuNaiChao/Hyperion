"""bug-RCA 多阶段委托 + 迭代 verify-refine(R3.1 #54-rework,B)+ 工具驱动(踩坑 #2)。

五步(踩坑 #2,2026-07-31:砍 Hyperion 侧定位漏斗 —— recall/localize/assemble_localize 三节点
与 opencode 重复定位 double localization,改 opencode 自主定位 + MCP 工具):
  ingest → delegate_localize_loop  (阶段① 定位:opencode 自定位 + 调 hyperion_* 工具,verify-refine 循环,max K1 轮,同会话)
        → assemble_repair
        → delegate_repair_loop    (阶段② 修复:verify-refine 循环,max K2 轮,同会话 + git diff 观察 + validate_patch 门控)
        → report_memorize

R3.1 #54-rework(B):弃「多候选采样 + majority voting」改「迭代 verify-refine」——
  - 同一个 opencode session 贯穿两阶段(--continue 链;per-bug workspace 唯一 cwd → session 隔离);
  - verdict 由 opencode 证伪式自审产出(confirmed/needs_revisit、verified/needs_fix);
  - 执行硬门控(Hyperion 侧,非 LLM):validate_patch Tier0 apply/revert(#50 repro 落地后加强);
  - 收敛:每 delegate call 仍 steps + 单 schema(不重蹈 glm-5.2 单 loop 97K 不收敛);只在
    verdict=needs_revisit 时重试,infra 错误(timeout/error)直接跳出(不 --continue 破损 session)。
2026-07-31:rerank/majority_voting 整体移除(无 oracle 平凡白烧);Hyperion 侧定位漏斗砍掉改工具驱动
(opencode 经 MCP 调 search_codebase/recall/filter_logs,见 bug-rca-design.md §6)。
依据:Self-Refine/Reflexion/SWE-Search/Aider/OpenHands;Agentless 投票仅在有 oracle 时有效。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from hyperion.services.code_index.loc_translate import line_wrap_content, merge_intervals
from hyperion.services.code_index.parser import parse_file
from hyperion.services.memory import get_memory_service
from hyperion.services.memory.schema import Evidence, KnowledgeItem, Scope, SourceTier
from hyperion.tools.delegate import CodingAgentDelegate
from hyperion.workflows.bug_rca.state import BugRcaState

# 阶段① 定位契约:root_cause/evidence + verdict/falsification(自审),**无 patch**。
# R3.5 #46 加 4 个 optional 字段(problem_summary/impact/scope_notes/log_evidence)喂报告的
# 「问题描述/定位定界/关键证据」段。required 不变(只 root_cause/verdict)→ 缺字段报告优雅降级。
# 关键:prompt 嵌 str(schema)(见 _build_localize_prompt),加字段自动告知 delegate,不用改 prompt 结构。
# description 写死长度上限 + 客观陈述 —— 否则字段越多 delegate 输出越长,撞 token 上限、报告冗长。
LOCALIZE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "root_cause": {"type": "string"},  # 为什么出 bug(为什么句)
        "problem_summary": {"type": "string", "description": "现象一句话,≤80字(出什么错;区别于 root_cause 讲为什么;客观,禁数字/禁覆盖率)"},
        "impact": {"type": "string", "description": "影响面/严重度,≤120字(客观,禁数字/禁覆盖率)"},
        "trigger_chain": {"type": "array", "items": {"type": "string"}},  # 触发链:因果步骤,每步可内嵌 file:line
        "evidence": {"type": "array", "items": {"type": "object", "properties": {
            "file": {"type": "string"}, "line": {"type": "integer"},
            "snippet": {"type": "string"}, "why": {"type": "string"}},
            "description": "代码证据:每条锚到 file:line + 原文片段 snippet + 为何相关 why(别只给文件名)"}},  # [{file, line, snippet, why}]
        "blast_radius_files": {"type": "array", "items": {"type": "string"}},
        "scope_notes": {"type": "string", "description": "定位定界:为何这些文件/符号是根因落点+范围边界,≤200字(客观)"},
        "log_evidence": {"type": "array", "items": {"type": "object", "properties": {
            "line": {"type": "integer"}, "event": {"type": "string"}, "note": {"type": "string"}},
            "description": "关键日志行,≤8条,仅日志驱动时填:[{line,event,note}]"}},
        "verdict": {"type": "string", "enum": ["confirmed", "needs_revisit"]},  # B:自审判定
        "falsification": {"type": "string"},  # B:证伪式自审找的反例(或"找了 X 处无反例")
    },
    "required": ["root_cause", "verdict"],
}

# 阶段② 修复契约:delegate 直接 edit code/(补丁由 git diff 观察);自审 verdict/falsification。
# R3.5 #46 加 patch_rationale/next_steps(optional)喂报告的「补丁说明/下一步建议」段。
REPAIR_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "confidence": {"type": "number"},  # 0-1,delegate 对这次修复的自评
        "verdict": {"type": "string", "enum": ["verified", "needs_fix"]},  # B:自审判定
        "falsification": {"type": "string"},  # B:证伪式自审(改完仍可能出问题的地方)
        "patch_rationale": {"type": "string", "description": "补丁说明:改了啥+为啥这么改+正确性论证(如NULL守卫/无double-free/正常路径不变),≤300字(客观)"},
        "next_steps": {"type": "array", "items": {"type": "string"}, "description": "下一步建议,≤5条(人工review重点/回归用例/邻近风险)"},
    },
    "required": ["verdict"],
}


async def node_ingest(state: BugRcaState) -> dict:
    """1. ingest:初始化 scope + 建 workspace(delegate 在 workspace/code 改码,原仓不动)。"""
    from hyperion.services.workspace.manager import create_workspace

    codebase = Path(state["repo_root"]).name
    ws = create_workspace(state["repo_root"], state["trigger"])
    return {"scope": Scope(owner="default", codebase=codebase), "workspace": str(ws)}


def _code_dir(state: BugRcaState) -> str:
    """delegate 的工作目录:有 workspace → workspace/code(opencode 在此 read+edit);
    无 workspace(兼容/降级)→ repo_root。

    为什么用 workspace/code:opencode 的 edit 改的是这份拷贝,原仓不动;补丁由 git diff
    观察这份拷贝的改动生成(行号/格式天然对,根治 LLM 吐 diff off-by-one)。
    """
    ws = state.get("workspace")
    if ws:
        return str(Path(ws) / "code")
    return state["repo_root"]


def _build_localize_prompt(trigger: str, schema: dict, log_path: str | None = None) -> str:
    """阶段① 定位 prompt:线索 + 自主定位 + 证伪自审 + 契约。

    砍漏斗后(踩坑 #2,2026-07-31):不再预喂「锚点/历史教训」—— opencode 自主定位,**优先调
    Hyperion 的 MCP 工具**(search_codebase 语义找码 / recall 翻教训 / filter_logs 筛大日志)缩范围,
    比 grep 整库又准又省;再 read/grep 精读、追调用链。**工具名 + 优先级写在 opencode agent 持久
    prompt 里**(config/opencode_hyperion.json),这里只给任务 + 契约,不硬编码工具名(免得前缀对不上)。
    """
    log_hint = ""
    if log_path:
        log_hint = (
            f"\n### 日志(大文件,禁直接 read)###\n原始日志 `{log_path}` 可能很大(兆级)。"
            f"**禁止直接 read 它(会撑爆上下文)**;只调 "
            f"`hyperion_filter_logs(log_path=\"{log_path}\", since=\"<故障窗起 HH:MM:SS>\", "
            f"until=\"<故障窗止 HH:MM:SS>\")` 取时间窗内的精华行(默认封顶 400 行)。"
            f"故障窗起止从日志/线索里读出;需要更细再缩窗调一次。\n###\n"
        )
    return f"""你是 C/系统软件 bug 根因定位专家。**只定位根因,不要写补丁**。

### Bug 线索 ###
{trigger or "(见日志,自行提取故障现象)"}
##
{log_hint}
### 你的任务 ###
1. 自主定位根因(为什么出 bug):优先用 Hyperion 提供的差异化工具(语义检索代码 / 翻历史同类 bug
   教训 / 筛大日志)缩范围——比从头 grep 整库更准更省;再用 read/grep 精读嫌疑代码、追调用链。
2. **输出前自审(证伪式,对抗「自己骗自己」)**:主动找一条可能推翻结论的证据(日志/堆栈/调用链里
   仍矛盾处)填 falsification;无反例 → verdict="confirmed",有矛盾 → verdict="needs_revisit"。
3. 严格按下面 JSON schema 返回(**不要 patch 字段** —— 本阶段只定位):
{schema}
"""


async def node_delegate_localize_loop(state: BugRcaState) -> dict:
    """2. 阶段① 定位 verify-refine 循环(B,#54-rework)。【loop 核心】"""
    from hyperion.platform.config import get_app_config

    # K1:最多跑几轮(iter0 + 最多 K1-1 次重定位)。默认 2 = 初次定位 + 1 次重定位机会。
    k1 = getattr(get_app_config().delegate, "max_localize_loops", 2) or 2
    delegate = CodingAgentDelegate.from_config()
    code_dir = _code_dir(state)
    schema = LOCALIZE_SCHEMA  # 模块常量(砍 assemble_localize 节点后不再走 state)

    localization = None            # 最后一次拿到的 localization_json(喂阶段②)
    last_result = None             # 最后一次 delegate 回执(report 用)
    verdict_chain: list[str] = []  # 每轮 verdict(report 显示 verify-refine 过程)
    prompt = _build_localize_prompt(state["trigger"], schema, state.get("log_path"))

    for i in range(k1):
        # iter0 新 session(continue_session=False);其后 --continue 续同一个 session —— 复用 opencode
        # 已读的代码上下文(免冷启动重读 → 省 token/时间);per-bug workspace 唯一 cwd → session 天然隔离。
        last_result = await delegate.run(
            prompt, cwd=code_dir, output_schema=schema, timeout=None,
            agent="hyperion-localize", continue_session=i > 0,
        )
        # infra 错误(timeout / error / schema 抽不出 JSON):读不到 verdict,不 --continue 续破损 session,直接跳出。
        if not last_result.ok or not isinstance(last_result.data, dict):
            verdict_chain.append(f"iter{i}:infra-{last_result.status}")
            break
        localization = last_result.data
        verdict = localization.get("verdict", "confirmed")  # schema 万一缺 verdict,默认 confirmed(防空转死循环)
        verdict_chain.append(f"iter{i}:{verdict}")
        if verdict == "confirmed":
            break  # 证伪式自审通过 → 进阶段②
        # verdict == needs_revisit:组 revisit prompt(带上轮它自己找的 falsification 反馈)进下一轮重定位
        prompt = (
            "你上一轮把根因标为 needs_revisit。请基于你找的反例重新审视,给出 confirmed 的定位。\n\n"
            f"上一轮根因:{localization.get('root_cause', '(无)')}\n"
            f"你找的反例/矛盾:{localization.get('falsification', '(无)')}\n\n"
            "重新读相关代码/日志:要么修正根因(给新的 confirmed 定位),要么确认原根因仍成立"
            "(把反例解释清楚 → verdict=confirmed)。按 schema 返回:\n" + str(schema)
        )
    # localization 可能 None(infra 失败)→ 阶段② assemble_repair 会降级让 delegate 自读(已处理)
    return {
        "localization_json": localization,
        "delegate_localize_result": last_result,
        "localize_loops": len(verdict_chain),
        "verdict_chain": verdict_chain,
        "localize_revisit_prompt": prompt if len(verdict_chain) > 1 else "",
    }


def _render_evidence_snippets(localization_json, repo_root: str) -> str:
    """阶段② 给 evidence 指向的精确代码片段(小窗口 ±10,基于阶段①锁的 evidence)。"""
    if not localization_json:
        return "(阶段①未定位,请自行读码定位 + 修)"
    evidence = localization_json.get("evidence", [])
    if not evidence:
        return "(阶段①无 evidence,请基于根因自行定位代码)"
    by_file: dict[str, list[int]] = {}
    for e in evidence:
        if not isinstance(e, dict):
            continue
        f = e.get("file")
        ln = e.get("line")
        if f and isinstance(ln, int):
            by_file.setdefault(f, []).append(ln)
    if not by_file:
        return "(阶段① evidence 无 file:line,请基于根因自行定位)"
    parts = []
    for fn, lines in by_file.items():
        intervals = merge_intervals([(max(1, ln - 10), ln + 10) for ln in lines])
        fpath = Path(repo_root) / fn
        try:
            source = fpath.read_text(encoding="utf-8", errors="replace")
            syms = parse_file(fpath)
            parts.append(f"### {fn}\n" + line_wrap_content(source, intervals, sticky_scroll=True, symbols=syms))
        except OSError:
            parts.append(f"### {fn}(读失败)")
    return "\n".join(parts)


def node_assemble_repair(state: BugRcaState) -> dict:
    """3. 阶段② 组装修复 prompt:阶段①根因(锁死)+ evidence 代码片段 + 修复 schema(含自审 verdict)。"""
    loc = state.get("localization_json") or {}
    root_cause = loc.get("root_cause", "(阶段①未给出)")
    trigger_chain = loc.get("trigger_chain", [])
    snippets = _render_evidence_snippets(loc, state["repo_root"])
    tc_ctx = "\n".join(f"- {t}" for t in trigger_chain) if trigger_chain else "(无)"
    prompt = f"""你是 C/系统软件 bug 修复专家。**根因已定位,直接改文件**(不要重新定位、不要贴 diff 文本)。

### Bug 线索 ###
{state["trigger"]}
###

### 已定位的根因(阶段① 锁定,直接信)###
根因:{root_cause}

触发链:
{tc_ctx}
###

### 根因涉及的代码(带行号,sticky;如需更多上下文自行 read/grep)###
{snippets}
###

### 你的任务 ###
1. 基于上面的根因,**用 edit 工具直接修改 `./code/` 里根因涉及的文件**来修这个 bug。
   **不要把 unified diff 贴在回复里** —— Hyperion 会用 `git diff` 观察你对 code/ 的实际改动生成补丁(行号/格式天然正确)。
2. 只改根因相关文件,禁止顺手重构。
3. **改完自审(证伪式)**:re-read 改动,主动找「改完仍可能出问题」处(根因没真消除?引入新问题?改错函数?)
   填 falsification;确认消除根因且无新问题 → verdict="verified",否则 verdict="needs_fix"。
4. 严格按下面 JSON schema 返回(**不要 patch 字段**):
{REPAIR_SCHEMA}
"""
    return {"prompt": prompt, "output_schema": REPAIR_SCHEMA}


def _observe_patch(code_dir: str) -> str:
    """git diff 观察 workspace/code 的改动 → unified diff 补丁(git add -A 后 diff --cached)。

    面向小白:opencode 改完 code/ 里的文件后,我们用 git 把这些改动"拍个照"生成标准补丁。
    为什么用 git diff 而非信 delegate 吐的 diff 文本:git 直接看文件实际前后差异,行号/格式天然
    对(根治 R2 的 LLM 吐 diff off-by-one 问题)。git add -A 先暂存所有改动,diff --cached 看暂存区。
    """
    try:
        subprocess.run(["git", "add", "-A"], cwd=code_dir, capture_output=True, text=True, timeout=30)
        proc = subprocess.run(
            ["git", "diff", "--cached"], cwd=code_dir, capture_output=True, text=True, timeout=30,
        )
        return proc.stdout if proc.returncode == 0 else ""
    except Exception:  # noqa: BLE001 —— git 操作可能各种失败,失败返空(上层当"未过"处理)
        return ""


async def node_delegate_repair_loop(state: BugRcaState) -> dict:
    """4. 阶段② 修复 verify-refine 循环(B,#54-rework)。【loop 核心】"""
    from hyperion.platform.config import get_app_config
    from hyperion.services.workspace.validate import validate_patch

    cfg = get_app_config().delegate
    k2 = getattr(cfg, "max_repair_loops", 2) or 2  # 默认 2 = 初次修 + 1 次重修机会
    delegate = CodingAgentDelegate.from_config()
    code_dir = _code_dir(state)
    repo_root = state["repo_root"]
    schema = state.get("output_schema") or REPAIR_SCHEMA

    patch = ""
    verified = False
    validate_log = ""
    last_result = None
    verdict_chain: list[str] = []
    loop_iters = 0  # 实际 loop 轮数(供 report)
    prompt = state.get("prompt", "")

    for i in range(k2):
        loop_iters += 1
        # 续 localize 那个 session(continue_session=True);**不 git reset** —— B 是迭代精炼,
        # opencode 在"已读上下文 + 已改代码"上继续修(改动累积),reset 是独立多采样的逻辑(已弃)。
        last_result = await delegate.run(
            prompt, cwd=code_dir, output_schema=schema, timeout=None,
            agent="hyperion-repair", continue_session=True,
        )
        # 观察补丁(git diff,不信任 delegate 吐的 diff)+ 执行硬门控(validate_patch Tier0,非 LLM)
        patch = _observe_patch(code_dir)
        v = (
            validate_patch(patch, forward_dir=repo_root, reverse_dir=code_dir)
            if patch else {"verified": False, "forward_method": "empty", "log": "patch 为空"}
        )
        validate_log = v.get("log", "")
        # infra 错误:读不到 verdict,跳出(不续破损 session)
        if not last_result.ok or not isinstance(last_result.data, dict):
            verdict_chain.append(f"iter{i}:infra-{last_result.status}")
            break
        verdict = last_result.data.get("verdict", "verified")  # schema 万一缺 verdict,默认 verified(防空转)
        verdict_chain.append(f"iter{i}:{verdict}")
        # 通过 = 执行硬门控 AND 自审 verified(信号分层:执行硬 / 自评弱,两者皆要才放行)
        verified = bool(v.get("verified")) and verdict == "verified"
        if verified:
            break
        # 没过:组 repair revisit prompt(带 gate 结果 + falsification 反馈)进下一轮 --continue 再修
        prompt = (
            "这次修复没过验证。请继续修(在同一批文件上改,不要重头来)。\n\n"
            f"- 你的自审:{verdict}(反例:{last_result.data.get('falsification', '(无)')})\n"
            f"- 执行门控 git apply --check:{'通过' if v.get('verified') else '失败'}"
            f" [{v.get('forward_method', '?')}]\n"
            f"- 诊断:{validate_log[-800:]}\n\n"
            "基于上面的反馈继续修这个 bug(用 edit 改 code/),改完按 schema 返回(含 verdict/falsification):\n"
            + str(schema)
        )

    return {
        "patch": patch,
        "verified": verified,
        "delegate_result": last_result,
        "repair_loops": loop_iters,
        "verdict_chain": verdict_chain,
        "validate_log": validate_log,
    }


def _coerce_evidence_line(v) -> int | None:
    """LLM 给的 evidence.line 防御解析 → 首个 int(或 None)。

    模型偶尔不守 schema:int / "3067" / "3067,4105,5980"(多嫌疑行逗号串)/ "3067-3070"(区间)/ float。
    取首个 int 喂 Evidence(要 int | None);解析不出 → None(丢行号,留 file)。
    """
    if v is None or isinstance(v, bool):  # bool 是 int 子类,先排除
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    if isinstance(v, str):
        first = v.split(",")[0].split("-")[0].strip()  # "3067,4105" / "3067-3070" → "3067"
        try:
            return int(first)
        except ValueError:
            return None
    return None


async def node_report_memorize(state: BugRcaState) -> dict:
    """5. report + memorize:patch 从 repair loop 观察;root_cause/evidence 从阶段① localization_json。"""
    repair_result = state.get("delegate_result")
    loc = state.get("localization_json") or {}
    scope = state.get("scope")
    if repair_result is None or scope is None:
        return {}
    patch = state.get("patch", "")  # git diff 观察出的补丁(repair loop 写)
    root_cause = loc.get("root_cause", "")
    evidence_raw = loc.get("evidence", [])

    try:
        from hyperion.workflows.bug_rca.report import render_report

        # R3.5 #46:render_report 单一数据源 state(直读 localization_json/delegate_result.data);
        # 不再把 root_cause/evidence mutate 进 repair_result.data(旧 hack 已删);verify-refine/METR 已在报告 §六。
        report_md = render_report(state)
    except ImportError:
        report_md = (
            f"# bug-RCA 报告(降级)\n\n## 根因\n{root_cause or '(无)'}\n\n"
            f"## 补丁\n```diff\n{patch}\n```\n"
        )

    out_dir = Path("data/bug_rca")
    out_dir.mkdir(parents=True, exist_ok=True)
    repo_name = Path(state["repo_root"]).name
    report_path = out_dir / f"{repo_name}-rca.md"
    report_path.write_text(report_md, encoding="utf-8")
    patch_path = out_dir / f"{repo_name}.patch"
    if patch:
        patch_path.write_text(patch, encoding="utf-8")

    svc = get_memory_service()
    evidence = []
    for e in evidence_raw:
        if not isinstance(e, dict):
            continue
        efile = e.get("file")
        if not efile:
            continue
        # 防御 LLM 输出方差:evidence.line 可能是 int / "3067" / "3067,4105,5980"(逗号多行)/ float。
        # 单条 evidence 坏不连坐全盘崩 —— try/except 跳过这条,继续。
        try:
            evidence.append(Evidence(file=efile, line=_coerce_evidence_line(e.get("line"))))
        except Exception:  # noqa: BLE001 —— 坏 evidence 跳过,不崩整个 workflow
            continue
    lesson = KnowledgeItem(
        kind="bug_lesson", repo=repo_name, scope=scope,
        summary=root_cause[:200], root_cause=root_cause, detail="", evidence=evidence,
        source="bug_rca", source_tier=SourceTier.inferred,
    )
    await svc.memorize([lesson], scope)
    return {"report_path": str(report_path), "patch_path": str(patch_path),
            "verified": state.get("verified", False), "lesson": lesson}
