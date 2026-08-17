"""R3.4 文档摄取(ingest)· 离线逻辑测试。

不依赖外部 API。覆盖:
  - LongDocChunker:header 切节 / 段落再切 / 短文 1 块 / 空 → []。
  - ingest_document 分发器:report 路(.md → 复用 parse_issue → 分块 → svc.memorize_report)、
    patch 路(monkeypatch PatchIngestPipeline.run 测接线,不依赖真核心)、kind 强制覆盖、空文档 warn。

(patch 路径的真核心算法 retrieve-then-summarize 由 PatchIngestPipeline.run 实现后单测覆盖;
 同根因去重合并走真 native 后端的集成测试见 e2e。)
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from rootrecall.services.memory.ingest import LongDocChunker, PatchIngestPipeline, _parse_diff_hunks, ingest_document
from rootrecall.services.memory.schema import KnowledgeItem, Scope, SourceTier

# ── LongDocChunker ────────────────────────────────────────────────────────────


def test_chunker_short_returns_one_chunk():
    out = LongDocChunker().split("# 标题\n短内容一段就够。")
    assert len(out) == 1
    assert "短内容" in out[0]


def test_chunker_splits_on_headers():
    text = "# 根因\n模块 A 的 bug 是空指针解引用。\n\n# 修复\n加 NULL 检查。"
    out = LongDocChunker(max_chars=1000).split(text)
    assert len(out) == 2
    assert "# 根因" in out[0] and "空指针" in out[0]
    assert "# 修复" in out[1] and "NULL 检查" in out[1]


def test_chunker_single_oversized_paragraph_stays_whole():
    # 单节只有一段、无空行、远超 max → 整段成一块(不劈中段,保语义完整;docstring 已标注)。
    big = "# 大节\n" + ("同一件事。" * 60)
    out = LongDocChunker(max_chars=120).split(big)
    assert len(out) == 1
    assert "# 大节" in out[0]


def test_chunker_paragraph_subsplit_with_blank_lines():
    # 有空行 → 按段攒 ≤ max_chars。
    paras = "\n\n".join(f"第 {i} 段内容。" for i in range(20))
    big = "# 大节\n" + paras
    out = LongDocChunker(max_chars=80).split(big)
    assert len(out) > 1
    assert all(len(c) <= 160 for c in out)  # 每块大致在上限内(允许单段超)


def test_chunker_empty_returns_empty():
    assert LongDocChunker().split("") == []
    assert LongDocChunker().split("   \n\n  ") == []


# ── 假 MemoryService(只实现 ingest 用到的两个方法)────────────────────────────


class _FakeSvc:
    """最小桩:记录 memorize_report / memorize 的调用。"""

    def __init__(self):
        self.report_calls: list[int] = []  # 每块的字符数
        self.memorize_calls: list[int] = []  # 每次 memorize 的条数

    async def memorize_report(self, text, scope, **kw):
        self.report_calls.append(len(text))
        return 1  # 假装每块抽 1 条

    async def memorize(self, items, scope):
        self.memorize_calls.append(len(items))
        return len(items)


def _scope():
    return Scope(codebase="wpa")


# ── ingest_document · report 路 ───────────────────────────────────────────────


def test_ingest_report_md_routes_and_chunks(tmp_path):
    f = tmp_path / "rca.md"
    f.write_text("# 根因\n这个 bug 是空指针解引用。\n\n# 修复\n加了 NULL 检查。")
    svc = _FakeSvc()

    stats = asyncio.run(ingest_document(f, scope=_scope(), repo="wpa", svc=svc))

    assert stats["route"] == "report"
    assert stats["chunks"] >= 2  # 两个 header → ≥2 块
    assert stats["wrote"] == stats["chunks"]  # 每块 1 条
    assert len(svc.report_calls) == stats["chunks"]
    assert svc.memorize_calls == []  # report 路走 memorize_report,不走 memorize


def test_ingest_report_empty_md_returns_zero_with_warn(tmp_path):
    f = tmp_path / "empty.md"
    f.write_text("   \n\n  ")  # 空白
    svc = _FakeSvc()

    stats = asyncio.run(ingest_document(f, scope=_scope(), repo="wpa", svc=svc))

    assert stats["route"] == "report"
    assert stats["wrote"] == 0
    assert stats["chunks"] == 0
    assert stats.get("warn")  # 提示抽不出文本


def test_ingest_report_default_svc_resolution(tmp_path, monkeypatch):
    # 不传 svc → ingest_document 内部 get_memory_service()。桩住工厂,确认默认解析路径通。
    f = tmp_path / "x.md"
    f.write_text("# A\n内容足够长到能抽出东西的一段话。")
    fake = _FakeSvc()
    monkeypatch.setattr("rootrecall.services.memory.ingest.get_memory_service", lambda: fake)

    stats = asyncio.run(ingest_document(f, scope=_scope(), repo="wpa"))

    assert stats["route"] == "report"
    assert stats["wrote"] == 1  # 1 块 → 假装 1 条


# ── ingest_document · patch 路(monkeypatch run() 测接线)────────────────────


def test_ingest_patch_routes_to_pipeline(tmp_path, monkeypatch):
    f = tmp_path / "fix.patch"
    f.write_text("diff --git a/foo b/foo\n@@ -1 +1 @@\n-old\n+new\n")
    # 桩 run():假装核心产出 1 条 KI(只验 dispatcher 把 patch 交给 pipeline 再交给 svc.memorize)。
    fake_ki = [KnowledgeItem(
        kind="bug_lesson", repo="wpa", scope=_scope(), summary="NULL 解引用根因",
        root_cause="foo() 未判 NULL", fix_patch="diff", tags=["patch_insight"],
        source_tier=SourceTier.imported,
    )]
    monkeypatch.setattr(PatchIngestPipeline, "run", lambda self: fake_ki)
    svc = _FakeSvc()

    stats = asyncio.run(ingest_document(f, scope=_scope(), repo="wpa", svc=svc))

    assert stats["route"] == "patch"
    assert stats["items_produced"] == 1
    assert stats["wrote"] == 1
    assert svc.memorize_calls == [1]
    assert svc.report_calls == []  # patch 路不走 memorize_report


def test_ingest_patch_empty_run_returns_zero(tmp_path, monkeypatch):
    # run() 产 0 条(解析不出 hunk / 降级)→ wrote=0,不抛。
    f = tmp_path / "empty.patch"
    f.write_text("not a real diff")
    monkeypatch.setattr(PatchIngestPipeline, "run", lambda self: [])
    svc = _FakeSvc()

    stats = asyncio.run(ingest_document(f, scope=_scope(), repo="wpa", svc=svc))

    assert stats["route"] == "patch"
    assert stats["wrote"] == 0
    assert stats["items_produced"] == 0


def test_ingest_kind_override_patch_on_non_diff_returns_zero(tmp_path):
    # .md 强制 kind=patch,内容无真 hunk 头 → run() 解析为空 → wrote=0(降级,不抛)。
    f = tmp_path / "looks_md.md"
    f.write_text("diff --git")  # 没有 @@ hunk 头
    stats = asyncio.run(ingest_document(f, scope=_scope(), repo="wpa", svc=_FakeSvc(), kind="patch"))
    assert stats["route"] == "patch"
    assert stats["wrote"] == 0
    assert stats["items_produced"] == 0


# ── _parse_diff_hunks:unified-diff 解析(纯函数,离线)──────────────────────────


def test_parse_diff_hunks_multi_hunk_single_file():
    diff = (
        "diff --git a/foo.c b/foo.c\n+++ b/foo.c\n"
        "@@ -10,3 +10,4 @@ ctx\n old\n+new\n"
        "@@ -50,2 +51,3 @@ ctx2\n+more\n"
    )
    hs = _parse_diff_hunks(diff)
    assert len(hs) == 2
    assert hs[0]["file"] == "foo.c" and hs[0]["new_start"] == 10
    assert hs[1]["file"] == "foo.c" and hs[1]["new_start"] == 51  # +51,3 → 51


def test_parse_diff_hunks_empty_and_non_diff():
    assert _parse_diff_hunks("") == []
    assert _parse_diff_hunks("完全不是 diff 的一段散文") == []


def test_parse_diff_hunks_new_file_dev_null():
    # 新文件场景:旧是 /dev/null,新是 b/new.c(无 diff --git 行也能从 +++ 取 file)。
    diff = "--- /dev/null\n+++ b/new.c\n@@ -0,0 +1,2 @@\n+a\n+b\n"
    hs = _parse_diff_hunks(diff)
    assert len(hs) == 1
    assert hs[0]["file"] == "new.c"
    assert hs[0]["new_start"] == 1


# ── PatchIngestPipeline.run(真核心:桩 model + 桩 retrieve)────────────────────


def _stub_model(json_reply: str) -> SimpleNamespace:
    """桩模型:invoke 返 .content = json_reply(DeepSeek-safe 抽取会抠出 JSON)。"""
    return SimpleNamespace(invoke=lambda msgs: SimpleNamespace(content=json_reply))


def test_pipeline_run_assembles_bug_lesson(tmp_path, monkeypatch):
    # 真 run()(不 monkeypatch run):桩 _retrieval_bundle=None(跳过 retrieve)+ 桩 model 吐 JSON。
    monkeypatch.setattr("rootrecall.services.memory.ingest._retrieval_bundle", lambda: None)
    reply = ('{"summary": "NULL 解引用修复", "root_cause": "foo() 返回前未判 NULL", '
             '"blast_radius_files": ["src/foo.c"], "evidence": [{"file": "src/foo.c", "line": 12}]}')
    diff = "diff --git a/src/foo.c b/src/foo.c\n+++ b/src/foo.c\n@@ -10,3 +10,4 @@\n+    if (!p) return -1;\n"

    kis = PatchIngestPipeline(diff, repo="wpa", scope=_scope(), model=_stub_model(reply)).run()

    assert len(kis) == 1
    ki = kis[0]
    assert ki.kind == "bug_lesson"
    assert ki.root_cause == "foo() 返回前未判 NULL"
    assert ki.summary == "NULL 解引用修复"
    assert ki.tags == ["patch_insight"]
    assert ki.source_tier == SourceTier.imported
    assert ki.fix_patch == diff
    assert ki.blast_radius_files == ["src/foo.c"]
    assert ki.evidence[0].file == "src/foo.c" and ki.evidence[0].line == 12


def test_pipeline_run_degrades_when_llm_garbage(monkeypatch):
    # LLM 吐非 JSON → _summarize 返 None → 降级:summary 用文件名凑、evidence 用 hunk 兜底。
    monkeypatch.setattr("rootrecall.services.memory.ingest._retrieval_bundle", lambda: None)
    diff = "diff --git a/src/bar.c b/src/bar.c\n+++ b/src/bar.c\n@@ -5,2 +5,3 @@\n+    guard();\n"

    kis = PatchIngestPipeline(diff, repo="wpa", scope=_scope(), model=_stub_model("完全不是 JSON")).run()

    assert len(kis) == 1
    ki = kis[0]
    assert ki.kind == "bug_lesson"
    assert ki.root_cause == ""  # LLM 没出根因 → 空
    assert "src/bar.c" in ki.summary and "降级" in ki.summary  # 降级摘要带文件名
    assert ki.evidence[0].file == "src/bar.c"  # hunk 兜底 evidence
    assert ki.evidence[0].line == 5  # hunk new_start


def test_pipeline_run_no_model_uses_auto_summary(monkeypatch):
    # 无模型(_build_model 也返 None)→ 不调 LLM,降级摘要 + hunk 兜底 evidence,仍写一条。
    monkeypatch.setattr("rootrecall.services.memory.ingest._retrieval_bundle", lambda: None)
    monkeypatch.setattr(PatchIngestPipeline, "_build_model", lambda self: None)
    diff = "+++ b/x.c\n@@ -1,1 +1,2 @@\n+a\n"

    kis = PatchIngestPipeline(diff, repo="wpa", scope=_scope(), model=None).run()

    assert len(kis) == 1
    assert kis[0].root_cause == ""
    assert "x.c" in kis[0].summary


def test_pipeline_run_stable_id_across_llm_variance(monkeypatch):
    # 同一个 .patch 摄取两次:LLM 措辞不同(summary 文本变)→ 但 id 必须相同(按 diff 算),
    # 这样 memorize 时走 bayesian 合并,而非 LLM 措辞不同 → 重复入库。
    monkeypatch.setattr("rootrecall.services.memory.ingest._retrieval_bundle", lambda: None)
    diff = "+++ b/x.c\n@@ -1,1 +1,2 @@\n+a\n"
    m1 = _stub_model('{"summary":"修法A的描述","root_cause":"rc1"}')
    m2 = _stub_model('{"summary":"完全不同的措辞B","root_cause":"rc2"}')

    ki1 = PatchIngestPipeline(diff, repo="wpa", scope=_scope(), model=m1).run()[0]
    ki2 = PatchIngestPipeline(diff, repo="wpa", scope=_scope(), model=m2).run()[0]

    assert ki1.id == ki2.id  # 同 diff → 同 id(与 LLM 措辞无关)
    assert ki1.id and ki2.id
    # 不同 diff → 不同 id(不误并):
    diff2 = "+++ b/y.c\n@@ -1,1 +1,2 @@\n+b\n"
    ki3 = PatchIngestPipeline(diff2, repo="wpa", scope=_scope(), model=m1).run()[0]
    assert ki3.id != ki1.id
