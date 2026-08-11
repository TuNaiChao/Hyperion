"""harness 转向 D0/D1:MCP 工具 blast_radius + validate_patch + export_patch + export_report 单测。

不真起 transport —— 用 FastMCP.call_tool(name, dict) 直接调工具闭包(call_tool 返回
([TextContent,...], structured),取第一个 TextContent.text 拿工具返回的 str)。验包装逻辑
+ 优雅降级(图未建/后端未装不抛,validate 路径不存在/garbage 补丁不抛)。
"""
from __future__ import annotations

import asyncio
import subprocess

import pytest

from hyperion.tools.mcp_memory import build_server


def _call(mcp, name: str, args: dict) -> str:
    """调一个工具,取回它的 str 结果。"""
    blocks, _structured = asyncio.run(mcp.call_tool(name, args))
    return blocks[0].text


def _git_repo(path) -> None:
    """建个真 git 仓 + 一个 commit(validate_patch 的 git apply --check 要在 git 仓里跑)。"""
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True)
    (path / "f.c").write_text("int main(void){return 0;}\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "init"], check=True)


# ════════════════════════ validate_patch 工具 ════════════════════════

def test_validate_patch_not_a_dir():
    """repo_path 不存在 → 友好提示,不抛。"""
    mcp = build_server()
    out = _call(mcp, "validate_patch", {"patch": "x", "repo_path": "/no/such/dir/xyz_abc"})
    assert "不是目录" in out


def test_validate_patch_applies_clean(tmp_path):
    """真 git 仓 + 合法 forward 补丁 → applies=True。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_repo(repo)
    # 改一行 → git diff 出 forward 补丁 → checkout 还原:补丁对该仓(HEAD 态)应干净 apply
    (repo / "f.c").write_text("int main(void){return 1;}\n", encoding="utf-8")
    diff = subprocess.run(["git", "-C", str(repo), "diff"], capture_output=True, text=True, check=True).stdout
    subprocess.run(["git", "-C", str(repo), "checkout", "--", "f.c"], check=True)
    assert diff.strip(), "测试夹具:没生成 diff"

    mcp = build_server()
    out = _call(mcp, "validate_patch", {"patch": diff, "repo_path": str(repo)})
    assert "applies=True" in out, out
    assert "✅" in out


def test_validate_patch_garbage(tmp_path):
    """garbage 补丁 → 三条降级路径全挂 → applies=False。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_repo(repo)

    mcp = build_server()
    out = _call(mcp, "validate_patch", {"patch": "this is not a diff at all", "repo_path": str(repo)})
    assert "applies=False" in out, out


# ════════════════════════ blast_radius 工具 ═════════════════════════

def test_blast_radius_empty_input():
    """没传 changed_files → 提示,不查图。"""
    mcp = build_server()
    out = _call(mcp, "blast_radius", {"changed_files": []})
    assert "未传 changed_files" in out


def test_blast_radius_not_built():
    """图未建(或 code-review-graph 后端未装)→ 优雅返回提示串,绝不漏 traceback。"""
    mcp = build_server()
    out = _call(mcp, "blast_radius",
                {"changed_files": ["src/x.c"], "codebase": "nonexistent_xyz_repo_42"})
    assert "Traceback" not in out, out
    # 三种友好提示之一:图未建 / 后端未装 / 失败
    assert any(k in out for k in ("未建", "不可用", "失败")), out


# ════════════════════════ call_chain 工具 ══════════════════════════

def test_call_chain_not_built():
    """图未建(或 CRG 后端未装)→ 优雅返回提示串,绝不漏 traceback(策略同 blast_radius)。"""
    mcp = build_server()
    out = _call(mcp, "call_chain",
                {"symbol": "some_function", "codebase": "nonexistent_xyz_repo_42"})
    assert "Traceback" not in out, out
    # 三种友好提示之一:图未建 / 后端未装 / 失败
    assert any(k in out for k in ("未建", "不可用", "失败")), out


def test_call_chain_bad_direction(monkeypatch):
    """非法 direction → CodeGraph.call_chain 抛 ValueError → 工具转友好串,不抛 traceback。

    monkeypatch CodeGraph.open 返一个 call_chain 必抛 ValueError 的假图,直测工具的 ValueError 兜底
    (不靠真图,hermetic;真图缺失时 direction 校验根本到不了,故必须注入)。
    """
    import hyperion.services.code_index.code_graph as cg_mod

    class _FakeGraph:
        def call_chain(self, *a, **kw):  # noqa: ANN002,ANN003 —— 假对象,签名宽松
            raise ValueError("direction 需为 callers / callees / both,收到 'sideways'")

    # 替掉 classmethod open:经类访问的普通函数不绑 cls,CodeGraph.open(target) → 假图。
    monkeypatch.setattr(cg_mod.CodeGraph, "open", lambda target: _FakeGraph())
    mcp = build_server()
    out = _call(mcp, "call_chain", {"symbol": "foo", "direction": "sideways"})
    assert "Traceback" not in out, out
    assert "没法算" in out, out  # ValueError 被工具兜底成友好串


# ════════════════════════ repo_map 工具(#38)════════════════════════

def test_repo_map_not_built():
    """图未建(或 CRG 后端未装)→ 优雅返回提示串,绝不漏 traceback(策略同 call_chain)。"""
    mcp = build_server()
    out = _call(mcp, "repo_map", {"codebase": "nonexistent_xyz_repo_42"})
    assert "Traceback" not in out, out
    assert any(k in out for k in ("未建", "不可用", "失败")), out


def test_repo_map_success_via_fake_graph(monkeypatch):
    """happy path:假图 repo_map 返固定 dict → 工具格式化「N symbols / M files」+ body;map_tokens 透传。

    monkeypatch CodeGraph.open 返假图(不靠真图,hermetic):直测工具壳的格式化 + map_tokens/per-call codebase 透传。
    """
    import hyperion.services.code_index.code_graph as cg_mod

    seen: dict = {}

    class _FakeGraph:
        def repo_map(self, *, map_tokens: int = 2048):  # noqa: ANN002
            seen["map_tokens"] = map_tokens
            return {"repo": "fake", "map_text": "f.c\n└── main (function) L1 pr=0.500",
                    "n_symbols": 1, "n_files": 1, "map_tokens_budget": map_tokens,
                    "map_tokens_used": 8, "truncated": False,
                    "top_symbols": [{"qualified_name": "f.c::main", "file": "f.c", "pagerank": 0.5}],
                    "note": ""}

    monkeypatch.setattr(cg_mod.CodeGraph, "open", lambda target: _FakeGraph())
    mcp = build_server()
    out = _call(mcp, "repo_map", {"map_tokens": 512, "codebase": "fake_cb"})
    assert "Traceback" not in out, out
    assert "1 symbols / 1 files" in out, out
    assert "f.c::main" in out  # body(json)含 top_symbols
    assert seen["map_tokens"] == 512  # map_tokens 透传到 repo_map


# ════════════════════════ export_patch 工具 ════════════════════════

def test_export_patch_not_a_dir():
    """repo_path 不存在 → 友好提示,不抛。"""
    mcp = build_server()
    out = _call(mcp, "export_patch", {"repo_path": "/no/such/dir/xyz_abc"})
    assert "不是目录" in out


def test_export_patch_empty_diff(tmp_path):
    """git 仓但工作树无改动 → 空 diff → 拒绝写(治改错树 / 没保存)。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_repo(repo)  # 干净 commit,工作树无改动
    mcp = build_server()
    out = _call(mcp, "export_patch",
                {"repo_path": str(repo), "out_dir": str(tmp_path / "out")})
    assert "空 diff" in out, out
    assert "已落盘" not in out  # 拒绝写空补丁


def test_export_patch_writes_file(tmp_path):
    """git 仓 + 有未提交改动 → 写 <out_dir>/<repo-name>.patch(unified diff,非空)。"""
    repo = tmp_path / "myrepo"
    repo.mkdir()
    _git_repo(repo)
    (repo / "f.c").write_text("int main(void){return 1;}\n", encoding="utf-8")  # 未提交改动
    out_dir = tmp_path / "out"
    mcp = build_server()
    out = _call(mcp, "export_patch",
                {"repo_path": str(repo), "out_dir": str(out_dir)})
    assert "已落盘" in out, out
    patch_file = out_dir / "myrepo.patch"  # 命名 = repo 目录名
    assert patch_file.is_file(), f"没写到 {patch_file}"
    content = patch_file.read_text(encoding="utf-8")
    assert "diff --git" in content, "落盘的不是 unified diff"
    assert "return 1" in content, "diff 没含改动"


# ════════════════════════ export_report 工具 ════════════════════════

def test_export_report_empty(tmp_path):
    """空内容(或纯空白)→ 拒绝写(治 agent 假装写报告 / 传空串糊弄)。"""
    mcp = build_server()
    out = _call(mcp, "export_report",
                {"content": "   \n  ", "repo_path": str(tmp_path / "repo"),
                 "out_dir": str(tmp_path / "out")})
    assert "空报告" in out, out
    assert "已落盘" not in out  # 拒绝写空报告


def test_export_report_writes_file(tmp_path):
    """有内容 → 写 <out_dir>/<repo-name>-rca.md(内容逐字一致;repo 目录不存在也能取名)。"""
    repo = tmp_path / "myrepo"  # 故意不 mkdir:export_report 不依赖 repo 目录存在,只取目录名
    out_dir = tmp_path / "out"
    report_md = ("# 根因\n\nradio work 泄漏:abort 失败分支不释放 p2p_scan_work。\n\n"
                 "patch: data/bug_rca/myrepo.patch\nmemorize id=abc")
    mcp = build_server()
    out = _call(mcp, "export_report",
                {"content": report_md, "repo_path": str(repo),
                 "out_dir": str(out_dir)})
    assert "已落盘" in out, out
    report_file = out_dir / "myrepo-rca.md"  # 命名 = <repo 目录名>-rca.md
    assert report_file.is_file(), f"没写到 {report_file}"
    assert report_file.read_text(encoding="utf-8") == report_md


# ════════════════════════ memory_recall kind 过滤(patch_search 已并入 recall)════════════════════════

def test_memory_recall_kind_filter():
    """memory_recall 加 kind 参数(原 patch_search 并入):不崩 + kind 标签生效。

    kind 过滤逻辑(recall 多取再按 kind 过滤)在此验证不崩;命中路径由 recall 自身测试覆盖。
    """
    mcp = build_server()
    out_all = _call(mcp, "memory_recall", {"query": "bluetooth connection", "top_k": 3})
    assert "codebase=" in out_all  # 命中列表或空提示都带 codebase
    out_lesson = _call(mcp, "memory_recall", {"query": "bluetooth connection", "top_k": 3, "kind": "bug_lesson"})
    assert "kind=bug_lesson" in out_lesson  # kind 过滤生效(命中列表与空提示都带 kind 标签)


# ════════════════════════ per-call codebase(多库:同 server 进程切仓)═══════════════════════


class _FakeMemSvc:
    """记录 scope 的假 MemoryService —— 让 recall/memorize 的 per-call codebase 测试不碰真 db / 网络。

    build_server() 内 `from hyperion.services.memory import get_memory_service` 在调用时读模块属性,
    monkeypatch 替掉它即可注入本假对象(绕开真单例)。
    """

    def __init__(self):
        self.recall_scopes: list = []
        self.memorize_scopes: list = []

    async def recall(self, query, scope, *, top_k=None):  # noqa: ANN001 —— 假对象,签名宽松
        self.recall_scopes.append(scope)
        return []  # 无命中 → memory_recall 走空提示分支(仍回显 codebase)

    async def memorize(self, items, scope):  # noqa: ANN001
        self.memorize_scopes.append(scope)
        return len(items)


def test_search_codebase_per_call_codebase():
    """search_codebase 传 codebase → 真去查那个仓:提示里回显 per-call 名(非闭包默认)。"""
    mcp = build_server()
    out = _call(mcp, "search_codebase",
                {"query": "p2p scan routing", "codebase": "nonexistent_xyz_cb_42"})
    assert "Traceback" not in out, out
    # per-call 生效:返回串是传入的 codebase 名,不是闭包默认的 repo(cwd/config 名)
    assert "nonexistent_xyz_cb_42" in out, out
    assert any(k in out for k in ("还没建索引", "未找到")), out


def test_memory_recall_per_call_codebase(monkeypatch):
    """memory_recall 传 codebase → recall 用对应 scope(空结果回显 per-call 名 + scope 记录双证)。"""
    fake = _FakeMemSvc()
    monkeypatch.setattr("hyperion.services.memory.get_memory_service", lambda: fake)
    mcp = build_server()
    out = _call(mcp, "memory_recall",
                {"query": "bluetooth disconnect", "codebase": "nonexistent_xyz_cb_42"})
    assert "codebase=nonexistent_xyz_cb_42" in out, out
    assert fake.recall_scopes, "recall 没被调"
    assert fake.recall_scopes[-1].codebase == "nonexistent_xyz_cb_42"


def test_memory_memorize_per_call_codebase(monkeypatch):
    """memory_memorize 传 codebase → 写入用对应 scope(返回串回显 + scope 记录双证,不碰真 db)。"""
    fake = _FakeMemSvc()
    monkeypatch.setattr("hyperion.services.memory.get_memory_service", lambda: fake)
    mcp = build_server()
    out = _call(mcp, "memory_memorize", {
        "kind": "bug_lesson", "summary": "per-call codebase probe",
        "codebase": "nonexistent_xyz_cb_42",
    })
    assert "codebase=nonexistent_xyz_cb_42" in out, out
    assert fake.memorize_scopes, "memorize 没被调"
    assert fake.memorize_scopes[-1].codebase == "nonexistent_xyz_cb_42"


# ════════════════════════ cross_version_diff 工具 ═════════════════════════

def test_cross_version_diff_bad_ref(tmp_path):
    """非法 ref(含 ';' → 过不了 _SAFE_GIT_REF)→ ValueError → 工具转友好串,不抛 traceback。

    不需 git:regex 校验在 rev-parse 之前;tmp_path 是合法目录即可(repo_path is_dir 检查过)。
    """
    mcp = build_server()
    out = _call(mcp, "cross_version_diff",
                {"base_ref": "a;b", "head_ref": "HEAD", "repo_path": str(tmp_path)})
    assert "Traceback" not in out, out
    assert "没法算" in out, out  # ValueError 被工具兜底成友好串


def test_cross_version_diff_not_a_repo(tmp_path):
    """repo_path 是合法目录但非 git 仓 → git rev-parse 失败 → ValueError → 友好串,不抛。

    需 git(无 git skip):没装 git 时是另一条路径(OSError),语义不同,跳过保持断言精度。
    """
    import shutil
    if not shutil.which("git"):
        pytest.skip("git 不在 PATH")
    mcp = build_server()
    out = _call(mcp, "cross_version_diff",
                {"base_ref": "HEAD~1", "head_ref": "HEAD", "repo_path": str(tmp_path)})
    assert "Traceback" not in out, out
    assert "没法算" in out, out  # 非 git 仓 → rev-parse 失败 → ValueError → "没法算"
