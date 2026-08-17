"""deep_research · _verify 逐符号@行核验测试(R3.3.2 / P3.1)。

测什么(面向小白)
  P3.1 把 Verifier 从「只查文件存在」升级到「逐符号@行」:打开文件 → 确认 symbol 存在 →
  确认 line 落在 symbol 行区间 [start,end] 内。核验分三档:
    strict = 全过(真·逐符号@行,计入 Existence@Line Ratio);
    file   = 降级(只确认文件存在 —— 缺 symbol/line、parse 不到符号,不误杀);
    bad    = 疑似编造(文件不存在 / symbol 找不到 / line 出界)。
  打桩 parse_file(不依赖真 tree-sitter / 真仓),验三档 + Ratio 算对 + Verifier 章节渲染。
"""

from __future__ import annotations

from types import SimpleNamespace

from rootrecall.workflows.deep_research._verify import _verify_report_citations


def _sym(qname: str, start: int, end: int, name: str | None = None) -> SimpleNamespace:
    """造一个假 Symbol(parse_file 的返回元素),带 _verify 用到的四个属性。

    真 Symbol 是 dataclass;这里用 SimpleNamespace 够了(只读属性,不调方法)。
    """
    return SimpleNamespace(qualified_name=qname, name=name or qname, start_line=start, end_line=end)


def _state(repo_root, findings, plan=None):
    """造最小 DeepResearchState(只填 _verify 用到的 repo_root/findings/plan)。"""
    return {"repo_root": str(repo_root), "findings": findings, "plan": plan or []}


# _verify 模块顶层 import 了 parse_file,所以打它的桩要 patch _verify 模块里的那个名字。
_PARSE = "rootrecall.workflows.deep_research._verify.parse_file"


# ── 1. strict:symbol 存在 + line 落在区间内 ─────────────────────────────────
def test_strict_pass(tmp_path, monkeypatch):
    f = tmp_path / "p2p.c"
    f.write_text("x")  # 真文件(内容无所谓,parse_file 打桩)
    monkeypatch.setattr(_PARSE, lambda fp: [_sym("p2p_connect", 95, 180)])

    findings = [{"module": "P2P", "citations": [
        {"file": "p2p.c", "line": 120, "symbol": "p2p_connect", "claim": "发起协商"},
    ]}]
    report, stats = _verify_report_citations("report", _state(tmp_path, findings, plan=[{}]))

    assert stats["citations"] == 1
    assert stats["symbol_strict"] == 1
    assert stats["verified"] == 1
    assert stats["unverified"] == 0
    assert stats["existence_at_line"] == 1.0
    assert "✅ 所有引用通过逐符号@行核验" in report


# ── 2. bad:文件不存在 ───────────────────────────────────────────────────────
def test_bad_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(_PARSE, lambda fp: [])
    findings = [{"module": "M", "citations": [
        {"file": "nope.c", "line": 1, "symbol": "foo", "claim": ""},
    ]}]
    _, stats = _verify_report_citations("r", _state(tmp_path, findings, plan=[{}]))

    assert stats["unverified"] == 1
    assert stats["symbol_strict"] == 0
    assert stats["existence_at_line"] == 0.0


# ── 3. bad:文件存在但 symbol 找不到(疑似编造)──────────────────────────────
def test_bad_symbol_not_found(tmp_path, monkeypatch):
    (tmp_path / "a.c").write_text("x")
    monkeypatch.setattr(_PARSE, lambda fp: [_sym("real_func", 10, 20)])
    findings = [{"module": "M", "citations": [
        {"file": "a.c", "line": 15, "symbol": "fake_func", "claim": ""},
    ]}]
    report, stats = _verify_report_citations("r", _state(tmp_path, findings, plan=[{}]))

    assert stats["unverified"] == 1
    assert "找不到符号 `fake_func`" in report


# ── 4. bad:symbol 存在但 line 出界 ──────────────────────────────────────────
def test_bad_line_out_of_range(tmp_path, monkeypatch):
    (tmp_path / "a.c").write_text("x")
    monkeypatch.setattr(_PARSE, lambda fp: [_sym("foo", 10, 20)])
    findings = [{"module": "M", "citations": [
        {"file": "a.c", "line": 999, "symbol": "foo", "claim": ""},
    ]}]
    report, stats = _verify_report_citations("r", _state(tmp_path, findings, plan=[{}]))

    assert stats["unverified"] == 1
    assert "远离体内" in report


# ── 5. file 降级:citation 缺 symbol → 不判假,verified 计入但 strict 不计 ──────
def test_file_degrade_no_symbol(tmp_path, monkeypatch):
    (tmp_path / "a.c").write_text("x")
    monkeypatch.setattr(_PARSE, lambda fp: [])
    findings = [{"module": "M", "citations": [
        {"file": "a.c", "line": 5, "symbol": "", "claim": ""},  # 无 symbol
    ]}]
    _, stats = _verify_report_citations("r", _state(tmp_path, findings, plan=[{}]))

    assert stats["verified"] == 1  # 文件存在 → 过
    assert stats["symbol_strict"] == 0  # 但没做逐符号 → 不计 Ratio
    assert stats["unverified"] == 0
    assert stats["existence_at_line"] == 0.0


# ── 6. file 降级:parse_file 返空(未知后缀)→ 不误杀 ─────────────────────────
def test_file_degrade_parse_empty(tmp_path, monkeypatch):
    (tmp_path / "a.unknown").write_text("x")
    monkeypatch.setattr(_PARSE, lambda fp: [])  # 文件在但 parse 不到符号
    findings = [{"module": "M", "citations": [
        {"file": "a.unknown", "line": 5, "symbol": "foo", "claim": ""},
    ]}]
    _, stats = _verify_report_citations("r", _state(tmp_path, findings, plan=[{}]))

    assert stats["verified"] == 1
    assert stats["symbol_strict"] == 0
    assert stats["unverified"] == 0


# ── 7. name 兜底匹配(qualified_name 是全限定名时,name 也能对上)──────────────
def test_symbol_match_by_name(tmp_path, monkeypatch):
    (tmp_path / "a.c").write_text("x")
    monkeypatch.setattr(_PARSE, lambda fp: [_sym("module::long_func", 10, 20, name="long_func")])
    findings = [{"module": "M", "citations": [
        {"file": "a.c", "line": 15, "symbol": "long_func", "claim": ""},  # 匹配 name,非 qualified_name
    ]}]
    _, stats = _verify_report_citations("r", _state(tmp_path, findings, plan=[{}]))

    assert stats["symbol_strict"] == 1  # name 兜底匹配成功


# ── 8. Ratio 混合算对(2 strict + 1 bad + 1 degrade → 0.5)+ module_coverage ──
def test_ratio_mixed(tmp_path, monkeypatch):
    (tmp_path / "a.c").write_text("x")
    (tmp_path / "b.c").write_text("x")
    (tmp_path / "c.c").write_text("x")
    monkeypatch.setattr(_PARSE, lambda fp: [_sym("good", 1, 100)])  # 三个文件都返这个符号
    findings = [{"module": "M", "citations": [
        {"file": "a.c", "line": 50, "symbol": "good", "claim": ""},   # strict
        {"file": "b.c", "line": 10, "symbol": "good", "claim": ""},   # strict
        {"file": "c.c", "line": 999, "symbol": "good", "claim": ""},  # bad(出界)
        {"file": "a.c", "line": 5, "symbol": "", "claim": ""},        # file 降级
    ]}]
    _, stats = _verify_report_citations("r", _state(tmp_path, findings, plan=[{}, {}]))

    assert stats["citations"] == 4
    assert stats["symbol_strict"] == 2
    assert stats["verified"] == 3  # 2 strict + 1 degrade
    assert stats["unverified"] == 1
    assert stats["existence_at_line"] == 0.5  # 2/4
    assert stats["module_coverage"] == 0.5  # 1 个模块有 citations / 2 个 plan


# ── 9. Verifier 章节追加到报告末尾 + 警示语 ──────────────────────────────────
def test_verifier_section_appended(tmp_path, monkeypatch):
    (tmp_path / "a.c").write_text("x")
    monkeypatch.setattr(_PARSE, lambda fp: [_sym("foo", 1, 10)])
    findings = [{"module": "M", "citations": [{"file": "a.c", "line": 5, "symbol": "foo", "claim": ""}]}]
    report, _ = _verify_report_citations("原始报告内容", _state(tmp_path, findings, plan=[{}]))

    assert "原始报告内容" in report  # 原文保留
    assert "## Verifier(逐符号@行回查)" in report
    assert "Cited but Not Verified" in report  # 警示语


# ── 10. near:line 在体外但 ±_LINE_TOLERANCE 内 → 近似(算通过,不判幻觉)─────
def test_near_tolerance(tmp_path, monkeypatch):
    """line 在 symbol 体外但在 ±_LINE_TOLERANCE 内 → near(算通过,不计严格 Ratio,不判幻觉)。"""
    (tmp_path / "a.c").write_text("x")
    monkeypatch.setattr(_PARSE, lambda fp: [_sym("foo", 100, 120)])  # 函数占 100-120 行
    findings = [{"module": "M", "citations": [
        {"file": "a.c", "line": 124, "symbol": "foo", "claim": ""},  # 124 在 [100-5,120+5]=[95,125] → near
    ]}]
    _, stats = _verify_report_citations("r", _state(tmp_path, findings, plan=[{}]))

    assert stats["near"] == 1
    assert stats["symbol_strict"] == 0
    assert stats["verified"] == 1  # near 算通过
    assert stats["unverified"] == 0  # 不判幻觉
    assert stats["existence_at_line"] == 0.0  # 严格 Ratio 不含 near
    assert stats["existence_at_line_lenient"] == 1.0  # 含容差 = 1.0
