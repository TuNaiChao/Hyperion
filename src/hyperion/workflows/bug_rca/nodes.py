"""bug-RCA 多阶段委托(R2 收尾:localize + repair 两阶段)。

九步(对标 bug-rca-design.md §7.5):
  ingest → recall → localize → assemble_localize → delegate_localize
    → assemble_repair → delegate_repair → verify → report+memorize

多阶段(解 glm-5.2 单 loop 不收敛):delegate 拆两阶段 ——
① localize_delegate 只定位 root_cause/evidence(禁补丁,glm-5.2 不纠结 diff);
② repair_delegate 根因已锁、只改局部、产 patch。每阶段任务单一,glm-5.2 易收敛。
依据:Agentless 32%/$0.70 vs SWE-agent 18.3%/$2.53(分阶段又便宜又稳);消融 skeleton>整文件。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from hyperion.services.code_index.loc_translate import line_wrap_content, merge_intervals
from hyperion.services.code_index.parser import parse_file
from hyperion.services.memory import get_memory_service
from hyperion.services.memory.schema import Evidence, KnowledgeItem, Scope, SourceTier
from hyperion.tools.delegate import CodingAgentDelegate
from hyperion.workflows.bug_rca.localize import localize
from hyperion.workflows.bug_rca.state import BugRcaState

# 阶段① 定位契约:只 root_cause/evidence,**无 patch**(禁补丁,glm-5.2 不用纠结 diff 格式)
LOCALIZE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "root_cause": {"type": "string"},
        "trigger_chain": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "array", "items": {"type": "object"}},  # [{file, line, snippet, why}]
        "blast_radius_files": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["root_cause"],
}

# 阶段② 修复契约:根因已锁,产 patch(R2 单候选;R3 多候选改 patches[])
REPAIR_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "patch": {"type": "string"},  # unified diff
        "confidence": {"type": "number"},
    },
    "required": ["patch"],
}


async def node_ingest(state: BugRcaState) -> dict:
    """1. ingest:初始化 scope。"""
    codebase = Path(state["repo_root"]).name
    return {"scope": Scope(owner="default", codebase=codebase)}


async def node_recall(state: BugRcaState) -> dict:
    """2. recall:翻记忆。"""
    scope = state.get("scope")
    if scope is None:
        return {"recalled": []}
    svc = get_memory_service()
    hits = await svc.recall(state["trigger"], scope, top_k=5)
    return {"recalled": hits}


def node_localize(state: BugRcaState) -> dict:
    """3. localize:Hyperion 自己的漏斗,产锚点(给阶段①作指引起点)。"""
    anchors = localize(state["repo_root"], state["trigger"])
    return {"anchors": anchors}


def _render_guide(anchors) -> str:
    """渲染嫌疑起点指引(file:line + why,不内联大片代码)—— 避 lost-in-the-middle。"""
    if not anchors:
        return "(漏斗未圈出锚点;请自行 grep/read 探索)"
    lines = [f"- {a.file}:{a.line}  {a.function or '?'}  [{a.why}]" for a in anchors]
    return "\n".join(lines)


def node_assemble_localize(state: BugRcaState) -> dict:
    """4. 阶段① 组装定位 prompt:线索 + 锚点指引 + 历史教训 + 定位 schema(无 patch)。"""
    guide = _render_guide(state.get("anchors", []))
    recalled_lines = []
    for h in state.get("recalled", []):
        recalled_lines.append(h.render() if hasattr(h, "render") else str(h))
    recalled_ctx = "\n".join(recalled_lines) or "(暂无历史教训)"
    prompt = f"""你是 C/系统软件 bug 根因定位专家。**只定位根因,不要写补丁**。

### Bug 线索 ###
{state["trigger"]}
###

### 已圈定的嫌疑起点(从这里读,可自行 grep/read 扩展)###
{guide}
###

### 相关历史教训(来自 Hyperion 记忆)###
{recalled_ctx}
###

### 你的任务 ###
1. 用 read/grep 工具读嫌疑代码(起点已给,可追调用链、不限于这些)。
2. 定位根因(为什么出 bug)。
3. 严格按下面 JSON schema 返回(**不要 patch 字段** —— 本阶段只定位):
{LOCALIZE_SCHEMA}
"""
    return {"localize_prompt": prompt, "localize_schema": LOCALIZE_SCHEMA}


async def node_delegate_localize(state: BugRcaState) -> dict:
    """5. 阶段① delegate:opencode 定位,产 root_cause/evidence JSON。"""
    from hyperion.tools.delegate import DelegateResult, DelegateStatus

    prompt = state.get("localize_prompt")
    schema = state.get("localize_schema")
    if not prompt or not schema:
        return {
            "delegate_localize_result": DelegateResult(
                final_text="", status=DelegateStatus.ERROR, error="assemble_localize 未产 prompt/schema"
            ),
            "localization_json": None,
        }
    delegate = CodingAgentDelegate.from_config()
    result = await delegate.run(prompt, cwd=state["repo_root"], output_schema=schema, timeout=None,
                                agent="hyperion-localize")  # stage-1 C: locator agent (steps=12 force converge, deny edit)
    # localization_json 喂阶段②(失败则 None,repair 降级自读)
    return {"delegate_localize_result": result, "localization_json": result.data if result.ok else None}


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
    """6. 阶段② 组装修复 prompt:阶段①根因(锁死)+ evidence 代码片段 + 修复 schema。"""
    loc = state.get("localization_json") or {}
    root_cause = loc.get("root_cause", "(阶段①未给出)")
    trigger_chain = loc.get("trigger_chain", [])
    snippets = _render_evidence_snippets(loc, state["repo_root"])
    tc_ctx = "\n".join(f"- {t}" for t in trigger_chain) if trigger_chain else "(无)"
    prompt = f"""你是 C/系统软件 bug 修复专家。**根因已定位,只写补丁**(不要重新定位)。

### Bug 线索 ###
{state["trigger"]}
###

### 已定位的根因(阶段① 锁定,直接信)###
根因:{root_cause}

触发链:
{tc_ctx}
###

### 根因涉及的代码(带行号,sticky)###
{snippets}
###

### 你的任务 ###
1. 基于上面的根因 + 代码,**给出 unified diff 补丁**修这个 bug(patch 字段)。
2. 只改根因相关文件,禁止顺手重构。
3. 严格按下面 JSON schema 返回:
{REPAIR_SCHEMA}
"""
    return {"prompt": prompt, "output_schema": REPAIR_SCHEMA}


async def node_delegate_repair(state: BugRcaState) -> dict:
    """7. 阶段② delegate:opencode 产 patch。"""
    from hyperion.tools.delegate import DelegateResult, DelegateStatus

    prompt = state.get("prompt")
    schema = state.get("output_schema")
    if not prompt or not schema:
        return {
            "delegate_result": DelegateResult(
                final_text="", status=DelegateStatus.ERROR, error="assemble_repair 未产 prompt/schema"
            )
        }
    delegate = CodingAgentDelegate.from_config()
    result = await delegate.run(prompt, cwd=state["repo_root"], output_schema=schema, timeout=None,
                                agent="hyperion-repair", continue_session=True)  # 阶段② C:repair agent(steps=8) + A 续同会话(记得阶段①)
    return {"delegate_result": result}


def node_verify(state: BugRcaState) -> dict:
    """8. verify:tolerant apply(git apply --recount --check,验补丁能打)。"""
    result = state.get("delegate_result")
    if not result or not result.ok or not result.data:
        return {"verified": False}
    patch = result.data.get("patch", "")
    if not patch:
        return {"verified": False}
    try:
        proc = subprocess.run(
            ["git", "apply", "--recount", "--check"],
            input=patch, cwd=state["repo_root"],
            capture_output=True, text=True, timeout=30,
        )
        return {"verified": proc.returncode == 0, "patch": patch}
    except Exception:  # noqa: BLE001
        return {"verified": False, "patch": patch}


async def node_report_memorize(state: BugRcaState) -> dict:
    """9. report + memorize:patch 从 repair delegate;root_cause/evidence 从阶段① localization_json。"""
    repair_result = state.get("delegate_result")
    loc = state.get("localization_json") or {}
    scope = state.get("scope")
    if repair_result is None or scope is None:
        return {}
    data = repair_result.data or {}
    patch = data.get("patch", "")
    root_cause = loc.get("root_cause", "")
    evidence_raw = loc.get("evidence", [])

    # 把 root_cause/evidence 塞进 repair_result.data,供 render_report 读(不改 report.py)
    if isinstance(repair_result.data, dict):
        repair_result.data.setdefault("root_cause", root_cause)
        repair_result.data.setdefault("evidence", evidence_raw)

    try:
        from hyperion.workflows.bug_rca.report import render_report

        report_md = render_report(state, repair_result)
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
        if efile is None:
            continue
        evidence.append(Evidence(file=efile, line=e.get("line")))
    lesson = KnowledgeItem(
        kind="bug_lesson", repo=repo_name, scope=scope,
        summary=root_cause[:200], root_cause=root_cause, detail="", evidence=evidence,
        source="bug_rca", source_tier=SourceTier.inferred,
    )
    await svc.memorize([lesson], scope)
    return {"report_path": str(report_path), "patch_path": str(patch_path),
            "verified": state.get("verified", False), "lesson": lesson}
