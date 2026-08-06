"""harness 转向 D0:2 新 MCP 工具 blast_radius + validate_patch 单测。

不真起 transport —— 用 FastMCP.call_tool(name, dict) 直接调工具闭包(call_tool 返回
([TextContent,...], structured),取第一个 TextContent.text 拿工具返回的 str)。验包装逻辑
+ 优雅降级(图未建/后端未装不抛,validate 路径不存在/garbage 补丁不抛)。
"""
from __future__ import annotations

import asyncio
import subprocess

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
