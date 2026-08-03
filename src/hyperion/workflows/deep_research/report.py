"""渲染代码仓深度调研报告(§5 骨架,带溯源 Markdown)。

系统架构章节 = CRG architecture_overview 图驱动(社区清单 + 跨社区耦合告警),
**非 LLM 瞎编**。模块深挖 = 各子 agent 的 cited findings(每结论锚 file:line)。
"""

from __future__ import annotations

from datetime import datetime

from hyperion.workflows.deep_research.state import DeepResearchState


def render_report(state: DeepResearchState) -> str:
    """从 state 渲染 §5 Markdown:元数据 / TL;DR / 系统架构 / 关键模块深挖 / 结构风险 / 来源。"""
    stats = state.get("codegraph_stats", {})
    overview = state.get("architecture_overview", {})
    findings = state.get("findings", [])
    warnings = overview.get("warnings", [])
    communities = overview.get("communities", [])
    codebase = state["codebase"]

    out: list[str] = []
    # 1. 元数据 + 溯源
    out.append(f"# {codebase} 代码仓深度调研报告\n")
    out.append("- 溯源:")
    out.append(f"  - 仓库:`{state['repo_root']}`")
    out.append(f"  - 生成日期:{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    out.append(
        f"  - 图统计:{stats.get('total_nodes', '?')} 节点 / "
        f"{stats.get('total_edges', '?')} 边 / {len(communities)} 社区 / "
        f"{stats.get('files_count', '?')} 文件"
    )
    langs = stats.get("languages") or []
    if langs:
        out.append(f"  - 语言构成:{', '.join(langs)}")
    out.append("")

    # 2. TL;DR(从各模块 summary 汇总;Verifier 前可能含未核验断言)
    out.append("## TL;DR\n")
    if findings:
        for f in findings:
            summ = (f.get("summary") or "").strip().splitlines()[0][:200] if f.get("summary") else "(无)"
            out.append(f"- **{f.get('module', '?')}**:{summ}")
    else:
        out.append("_(research 节点未产出 findings)_")
    out.append("")

    # 3. 系统架构(CRG 图驱动:社区 + 耦合告警)
    out.append("## 系统架构\n")
    out.append("_(由 code-review-graph 社区检测自动产出,非 LLM 编造。社区 ≈ 模块边界。)_\n")
    top_comms = sorted(communities, key=lambda c: len(c.get("members", [])), reverse=True)[:20]
    for c in top_comms:
        name = c.get("name", f"community-{c.get('id')}")
        size = len(c.get("members", []))
        lang = c.get("dominant_language") or "?"
        out.append(f"- **{name}** — {size} 节点,主语言 {lang}")
    if warnings:
        out.append("\n### 耦合告警(跨社区边 > 10)\n")
        for w in warnings:
            out.append(f"- ⚠️ {w}")
    out.append("")

    # 4. 关键模块深挖(各 finding)
    out.append("## 关键模块深挖\n")
    for f in findings:
        out.append(f"### {f.get('module', '?')}\n")
        out.append(f.get("summary") or "_(无摘要)_")
        out.append("")
        cites = f.get("citations") or []
        if cites:
            out.append("**关键证据:**\n")
            for c in cites[:15]:
                out.append(f"- `{c.get('file','?')}:{c.get('line','?')}` {c.get('symbol','')} — {c.get('claim','')}")
            out.append("")

    # 5. 结构风险(hub/bridge —— 从 plan 的 key_symbols 占位;完整 hub/bridge 章节可扩)
    out.append("## 结构风险\n")
    plan = state.get("plan", [])
    hub_syms = {s for p in plan for s in (p.get("key_symbols") or [])}
    if hub_syms:
        out.append("hub 节点(高连接,被大量依赖的枢纽;改动影响面大):\n")
        for s in list(hub_syms)[:20]:
            out.append(f"- `{s}`")
    else:
        out.append("_(无 hub 数据)_")
    out.append("")

    # 6. 来源汇总(file:line 清单,锚 commit SHA 见元数据)
    out.append("## 来源\n")
    seen: set[str] = set()
    for f in findings:
        for c in f.get("citations") or []:
            key = f"{c.get('file','?')}:{c.get('line','?')}"
            if key not in seen:
                seen.add(key)
                out.append(f"- `{key}` — {c.get('symbol','')}")
    out.append("")
    return "\n".join(out)
