"""R3.5 #46 bug-RCA 报告渲染 · 8 段去重结构测试。

不真跑 opencode/git:直接构造 state dict + DelegateResult fixture,调 render_report(state)
验渲染逻辑 —— 8 段都在、证据锚 file:line、降级不崩、METR 前置(TL;DR + §六)、patch 截断、
log_evidence 行号 coercion、problem_summary 与 root_cause 不互相吞。

fixture 思路:report 测试只调 render_report(纯函数),不调 delegate.run → 不需要 _ScriptedDelegate,
直接拼 state dict 即可(对齐 test_verify_refine_loop.py 里 _state 的最小拼装风格)。
"""
from __future__ import annotations

from rootrecall.tools.delegate import DelegateResult, DelegateStatus
from rootrecall.workflows.bug_rca.report import render_report


def _dr(data=None, status=DelegateStatus.OK, tokens=None):
    """DelegateResult fixture(data 是 repair 侧结构化产出)。"""
    return DelegateResult(final_text="{}", status=status, data=data, tokens=tokens or {})


def _full_state():
    """全字段 state:8 段都有料(模仿 demo2 P2P 扫描孤儿 bug)。"""
    return {
        "repo_root": "/tmp/wpa_supplicant",
        "trigger": "WiFi 扫描列表为空;iw 能扫到。journalctl 见日志。",
        "log_path": "/tmp/journalctl_b.txt",
        "patch": "--- a/wpa_supplicant/scan.c\n+++ b/wpa_supplicant/scan.c\n"
                  "@@ -2446,3 +2446,5 @@\n"
                  " static void scan_only_handler(...) {\n"
                  "+    wpas_p2p_scan_work_done(wpa_s);\n",
        "verified": True,
        "validate_log": "forward=strict ok; reverse ok",
        "localize_loops": 2,
        "repair_loops": 1,
        "verdict_chain": ["iter0:needs_revisit", "iter1:confirmed", "iter0:verified"],
        "localization_json": {
            "root_cause": "scan_only_handler 不释放 p2p_scan_work → 孤儿 radio work 阻塞队列",
            "problem_summary": "P2P 扫描进行中,普通 WiFi 扫描列表突然为空",
            "impact": "所有站点扫描被阻塞,用户看不到热点",
            "trigger_chain": [
                "① p2p-scan 启动",
                "② 并发 Interface.Scan 覆写 handler",
                "③ 结果误路由到 scan_only_handler(scan.c:2446)",
            ],
            "evidence": [
                {"file": "wpa_supplicant/scan.c", "line": 2446,
                 "snippet": "static void scan_only_handler(...)", "why": "误路由落点,不释放 p2p_scan_work"},
            ],
            "blast_radius_files": ["wpa_supplicant/scan.c", "wpa_supplicant/p2p_supplicant.c"],
            "scope_notes": "根因落点在 scan_only_handler 收尾;补丁只动 scan.c/p2p_supplicant.c,不碰 events.c 分发逻辑",
            "log_evidence": [
                {"line": 2452, "event": "Scan-only results received", "note": "结果误路由"},
                {"line": "3865", "event": "p2p_scan timeout (running=1)", "note": "孤儿超时"},
            ],
            "verdict": "confirmed",
            "falsification": "查了 radio_work_free 调用链,无反例",
        },
        "delegate_result": _dr(data={
            "confidence": 0.9, "verdict": "verified", "falsification": "re-read 改动无新问题",
            "patch_rationale": "在 scan_only_handler 末尾加 wpas_p2p_scan_work_done(NULL 守卫,无 double-free,正常路径不变)",
            "next_steps": ["补 P2P 并发扫描回归用例", "复核 events.c 分发是否还有邻近失效模式"],
        }, tokens={"total": 12345}),
    }


# ════════════════════════════════════════ 8 段 + 关键内容 ════════════════════════════════════════

def test_render_full_sections():
    """全字段 → 8 个一级标题都在 + 关键内容命中。"""
    md = render_report(_full_state())
    for h in [
        "## 执行摘要(TL;DR)",
        "## 一、问题描述",
        "## 二、根因分析",
        "## 三、定位定界",
        "## 四、关键证据",
        "## 五、补丁说明",
        "## 六、验证与过程",
        "## 七、下一步建议",
        "## 附录",
    ]:
        assert h in md, f"缺段落:{h}"
    # 各段的关键料都在
    assert "P2P 扫描进行中" in md          # problem_summary(§一)
    assert "scan_only_handler 不释放" in md  # root_cause(§二)
    assert "wpas_p2p_scan_work_done" in md   # patch_rationale(§五)
    assert "补 P2P 并发扫描回归用例" in md    # next_steps(§七)


def test_render_degrades_gracefully():
    """最小 state(只 root_cause/verdict)→ 不崩,核心段(根因/补丁)仍在。"""
    state = {
        "repo_root": "/tmp/repo",
        "trigger": "某 bug",
        "localization_json": {"root_cause": "根因X", "verdict": "confirmed"},
        "delegate_result": _dr(data={"verdict": "verified"}),
    }
    md = render_report(state)
    assert "# bug 根因分析报告" in md
    assert "## 二、根因分析" in md
    assert "根因X" in md
    assert "## 五、补丁说明" in md
    assert "(未生成)" in md  # patch 空 → 占位


def test_evidence_anchored_file_line():
    """代码证据表带 file:line 锚定(证据纪律)。"""
    md = render_report(_full_state())
    assert "wpa_supplicant/scan.c" in md
    assert "2446" in md  # evidence line 锚定


def test_metr_warning_in_tldr_and_section():
    """METR 警示在 TL;DR 和 §六都在(诚实准确率前置)。"""
    md = render_report(_full_state())
    assert md.count("METR") >= 2  # TL;DR 一次 + §六 一次


def test_next_steps_fallback():
    """无 next_steps + verified=False → 兜底模板含'人工复核'。"""
    state = _full_state()
    state["verified"] = False
    state["delegate_result"] = _dr(data={"verdict": "needs_fix"})  # 无 next_steps
    md = render_report(state)
    assert "人工复核" in md


def test_patch_size_truncation():
    """patch >200 行 → 截断为前 50 行 + 引 .patch(防报告爆炸)。"""
    state = _full_state()
    big_body = "\n".join(f"+line {i}" for i in range(250))
    state["patch"] = "--- a/x\n+++ b/x\n" + big_body  # 2 + 250 = 252 行
    md = render_report(state)
    assert "此处展示前 50 行" in md
    assert ".patch" in md
    assert "line 249" not in md  # 第 249 行被截掉


def test_log_evidence_line_coercion():
    """log_evidence.line = 区间串/float/缺 → 经 _coerce_evidence_line 不崩、取首。"""
    state = _full_state()
    state["localization_json"] = dict(state["localization_json"])
    state["localization_json"]["log_evidence"] = [
        {"line": "2452-2453", "event": "ev1", "note": "n1"},
        {"line": 3865.0, "event": "ev2", "note": "n2"},
        {"line": None, "event": "ev3", "note": "n3"},
    ]
    md = render_report(state)  # 不崩即过
    assert "ev1" in md and "ev2" in md and "ev3" in md
    assert "2452" in md  # 区间串 "2452-2453" 取首


def test_problem_vs_root_cause_no_dup():
    """problem_summary 与 root_cause 都给且不同 → 两段分别出现,不互相吞(防同义重复)。"""
    md = render_report(_full_state())
    # §一 里的现象句
    assert "P2P 扫描进行中,普通 WiFi 扫描列表突然为空" in md
    # §二/TL;DR 里的为什么句
    assert "scan_only_handler 不释放 p2p_scan_work" in md
