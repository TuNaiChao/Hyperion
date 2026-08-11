"""patch_report · 聚合 / 渲染 / Verifier 单测(P-A 1b Checkpoint 4)。

不依赖 LLM / 网络:
  - aggregate 的 _synthesize 被 monkeypatch(测确定性分桶统计)。
  - render / verify 用 fake state(纯函数)。
"""

from __future__ import annotations


def test_aggregate_stats(monkeypatch):
    """聚合:确定性分桶统计(theme/tier/high_security/hot_modules)+ 高安全 PR 列表。"""
    from hyperion.workflows.patch_report import _aggregate

    monkeypatch.setattr(_aggregate, "_synthesize", lambda f, s: ("cross summary", [{"file": "a.c", "line": 1}]))
    findings = [
        {"title": "PR1", "theme": "security", "security_tier": "high", "risk_score": 0.7,
         "modules": [1], "summary": "s1", "citations": [], "changed_files": ["a.c"]},
        {"title": "PR2", "theme": "function", "security_tier": "none", "risk_score": 0.2,
         "modules": [1, 2], "summary": "s2", "citations": [], "changed_files": ["b.c"]},
    ]
    agg = _aggregate.aggregate(findings, "cb")
    st = agg["stats"]
    assert st["total_prs"] == 2
    assert st["by_theme"] == {"security": 1, "function": 1}
    assert st["by_tier"] == {"high": 1, "none": 1}
    assert st["high_security_count"] == 1
    assert st["hot_modules"][0] == {"module": 1, "pr_count": 2}  # module 1 在两条 PR 都出现
    assert agg["high_security_prs"] == ["PR1"]
    assert agg["cross_summary"] == "cross summary"


def test_render_patch_report_sections():
    """渲染:跨 PR 综合 + 每 PR deep-dive + sources 段齐全。"""
    from hyperion.workflows.patch_report.report import render_patch_report

    state = {
        "codebase": "cb",
        "findings": [{"title": "PR1", "applies": True, "risk_score": 0.5,
                      "security_tier": "high", "theme": "security",
                      "summary": "fix at a.c:2", "changed_files": ["a.c"],
                      "citations": [{"file": "a.c", "line": 2, "symbol": "f", "claim": "x"}]}],
        "aggregate": {"stats": {"total_prs": 1, "by_theme": {"security": 1}, "by_tier": {"high": 1},
                                "high_security_count": 1, "high_risk_count": 1, "hot_modules": []},
                      "cross_summary": "CS", "citations": [],
                      "high_security_prs": ["PR1"]},
    }
    md = render_patch_report(state)
    assert "跨 PR 综合" in md and "CS" in md
    assert "PR1" in md and "建议人工复核" in md   # 高安全 PR 段
    assert "Sources" in md and "a.c:2" in md


def test_verify_passes_when_file_in_changed_files():
    """citation 的 file 在 PR 的 changed_files 里 → Verifier 通过(✅)。"""
    from hyperion.workflows.patch_report.report import verify_and_append

    state = {"findings": [{"title": "PR1", "changed_files": ["a.c"],
                           "citations": [{"file": "a.c", "line": 2, "symbol": "f", "claim": "x"}]}],
             "aggregate": {"citations": [{"file": "a.c", "line": 3, "symbol": "g", "claim": "y"}]}}
    md = verify_and_append("report\n", state)
    assert "Verifier" in md and "✅" in md
    assert "可疑" not in md


def test_verify_flags_invented_file():
    """citation 引用了 changed_files 里没有的文件 → 标可疑(防 LLM 编造)。"""
    from hyperion.workflows.patch_report.report import verify_and_append

    state = {"findings": [{"title": "PR1", "changed_files": ["a.c"],
                           "citations": [{"file": "INVENTED.c", "line": 1, "symbol": "x", "claim": "y"}]}],
             "aggregate": {"citations": []}}
    md = verify_and_append("report\n", state)
    assert "可疑" in md and "INVENTED.c" in md


# ── 行锚定软查(Tier 2 #4)──────────────────────────────────────────────────────

# 一个已知 hunk 的 diff:a.c 的 +1,3 → 改动行 [1,3]。
_DIFF_A = "diff --git a/a.c b/a.c\n--- a/a.c\n+++ b/a.c\n@@ -1,1 +1,3 @@\n x\n+int new;\n+y;\n"


def _art(diff: str):
    from hyperion.services.patch.fetcher import PatchArtifact

    return PatchArtifact(url="u", source_kind="github", diff=diff, changed_files=["a.c"])


def test_verify_line_anchor_in_hunk():
    """citation.line 落在 diff hunk 改动区间 → 行锚定(✅,无未锚定)。"""
    from hyperion.workflows.patch_report.report import verify_and_append

    state = {
        "artifacts": [_art(_DIFF_A)],
        "findings": [{"title": "PR1", "changed_files": ["a.c"],
                      "citations": [{"file": "a.c", "line": 2, "symbol": "f", "claim": "x"}]}],
        "aggregate": {"citations": []},
    }
    md = verify_and_append("report\n", state)
    assert "行锚定率: 1/1 (100%)" in md
    assert "未锚定(文件对" not in md   # 无未锚定段(注意:软指标说明文字里含"未锚定",故匹配段头)
    assert "可疑" not in md


def test_verify_line_anchor_out_of_hunk_flagged():
    """citation.line 不在 hunk 区间(a.c 改动是 [1,3],引 line 99)→ 标未锚定(软,不删)。"""
    from hyperion.workflows.patch_report.report import verify_and_append

    state = {
        "artifacts": [_art(_DIFF_A)],
        "findings": [{"title": "PR1", "changed_files": ["a.c"],
                      "citations": [{"file": "a.c", "line": 99, "symbol": "f", "claim": "x"}]}],
        "aggregate": {"citations": []},
    }
    md = verify_and_append("report\n", state)
    assert "未锚定" in md and "a.c:99" in md   # 软提示:列出未锚定
    assert "可疑" not in md                    # 文件本身对,不算可疑(硬指标过)


def test_verify_no_artifacts_skips_line_check():
    """无 artifacts(没解到 hunk)→ 不做行锚定(不报行锚定率,回归旧 file-only 行为)。"""
    from hyperion.workflows.patch_report.report import verify_and_append

    state = {"findings": [{"title": "PR1", "changed_files": ["a.c"],
                           "citations": [{"file": "a.c", "line": 2, "symbol": "f", "claim": "x"}]}],
             "aggregate": {"citations": []}}
    md = verify_and_append("report\n", state)
    assert "行锚定率" not in md   # 无 hunk → 不计分母,不报率
    assert "✅" in md and "未锚定" not in md


def test_aggregate_dedup_same_subject(monkeypatch):
    """两个 PR 改动文件完全重叠 + theme 同 → 判同主题:n_unique_subjects=1,1 组。"""
    from hyperion.workflows.patch_report import _aggregate
    monkeypatch.setattr(_aggregate, "_synthesize", lambda f, s: ("cs", []))
    findings = [
        {"title": "PR1", "theme": "security", "changed_files": ["a.c", "b.c"], "modules": [], "summary": "s"},
        {"title": "PR2", "theme": "security", "changed_files": ["a.c", "b.c"], "modules": [], "summary": "s"},
    ]
    st = _aggregate.aggregate(findings, "cb")["stats"]
    assert st["total_prs"] == 2
    assert st["n_unique_subjects"] == 1                       # 2 PR 同主题 → 1 unique
    assert len(st["duplicate_subject_groups"]) == 1
    assert st["duplicate_subject_groups"][0]["pr_count"] == 2


def test_aggregate_dedup_no_overlap(monkeypatch):
    """不同文件 → 不去重:n_unique_subjects=2,无重复组。"""
    from hyperion.workflows.patch_report import _aggregate
    monkeypatch.setattr(_aggregate, "_synthesize", lambda f, s: ("cs", []))
    findings = [
        {"title": "PR1", "theme": "security", "changed_files": ["a.c"], "modules": [], "summary": "s"},
        {"title": "PR2", "theme": "security", "changed_files": ["z.c"], "modules": [], "summary": "s"},
    ]
    st = _aggregate.aggregate(findings, "cb")["stats"]
    assert st["n_unique_subjects"] == 2
    assert st["duplicate_subject_groups"] == []


def test_aggregate_dedup_different_theme_not_merged(monkeypatch):
    """文件重叠但 theme 不同 → 不并(主题不同不算重复)。"""
    from hyperion.workflows.patch_report import _aggregate
    monkeypatch.setattr(_aggregate, "_synthesize", lambda f, s: ("cs", []))
    findings = [
        {"title": "PR1", "theme": "security", "changed_files": ["a.c", "b.c"], "modules": [], "summary": "s"},
        {"title": "PR2", "theme": "refactor", "changed_files": ["a.c", "b.c"], "modules": [], "summary": "s"},
    ]
    st = _aggregate.aggregate(findings, "cb")["stats"]
    assert st["n_unique_subjects"] == 2
    assert st["duplicate_subject_groups"] == []


def test_render_shows_unique_subjects():
    """报告渲染:有重复组时顶部展示 unique subjects 注记(底层 finding 不删)。"""
    from hyperion.workflows.patch_report.report import render_patch_report
    state = {
        "codebase": "cb",
        "findings": [],
        "aggregate": {"stats": {"total_prs": 3, "n_unique_subjects": 2,
                                "duplicate_subject_groups": [{"pr_count": 2, "titles": ["PR1", "PR2"]}]},
                      "cross_summary": "CS", "citations": [], "high_security_prs": []},
    }
    md = render_patch_report(state)
    assert "3 PRs" in md
    assert "2 unique subjects" in md
    assert "1 组同主题" in md
