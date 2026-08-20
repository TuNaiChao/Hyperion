"""P1 · 定时分析深化端到端:sync --analyze-agent(假 opencode 桩)/ --ingest-report(拦截包装)。

场景:F4 同款三仓(upstream / baseline up-v20 / fork),零网络 file:// remote。
  ① PATH 注入假 opencode(打印确定文本)→ `sync --analyze --analyze-agent`:报告被追加
     「Agent 复核」段,CLI 打「已追加 agent 复核」;
  ② PATH 指到没有 opencode 的目录 → 诚实 skip(报告保持纯三态,sync 本身 rc=0);
  ③ `--ingest-report`:monkeypatch mirror 的 ingest 包装(真 ingest 走 LLM,e2e 不烧钱)
     → 收到的 codebase 是**项目名**(up-v20 → up,-v 版本段剥掉);
  ④ `--analyze-agent` 不带 `--analyze` → rc=2 友好报错(不瞎跑)。
"""

from __future__ import annotations

import contextlib
import io
import os
import subprocess

import pytest

import rootrecall.services.repos.mirror as mirror_mod
import rootrecall.services.repos.registry as reg_mod
from rootrecall.cli import main as cli_main


def _git(path, *argv, check=True) -> str:
    r = subprocess.run(["git", "-C", str(path), *argv], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise AssertionError(f"git {argv} 失败: {r.stderr}")
    return r.stdout


def _commit(path, msg, files: dict) -> str:
    for name, text in files.items():
        (path / name).write_text(text, encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", msg)
    return _git(path, "rev-parse", "HEAD").strip()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """三仓 + 隔离(基线名带 -v20,好断言 ingest 的项目名剥离)。"""
    monkeypatch.setenv("ROOTRECALL_REPOS_FILE", str(tmp_path / "repos.yaml"))
    monkeypatch.setattr(reg_mod, "_install_root", lambda: tmp_path)

    up = tmp_path / "up"
    up.mkdir()
    _git(up, "init", "-q", "-b", "main")
    _git(up, "config", "user.email", "t@t.test")
    _git(up, "config", "user.name", "tester")
    _commit(up, "A init", {"core.c": "int base(void){return 0;}\n"})

    base = tmp_path / "baseline"
    subprocess.run(["git", "clone", "-q", str(up), str(base)], check=True)
    fork = tmp_path / "fork"
    subprocess.run(["git", "clone", "-q", str(up), str(fork)], check=True)
    for p in (base, fork):
        _git(p, "config", "user.email", "t@t.test")
        _git(p, "config", "user.name", "tester")

    assert cli_main(["repo", "register", "up-v20", "--path", str(base),
                     "--url", str(up), "--role", "baseline", "--branch", "main"]) == 0
    assert cli_main(["repo", "register", "e2e-fork", "--path", str(fork)]) == 0

    # 上游推进两条(一干净一冲突),给三态报告内容
    _commit(up, "U1 add util", {"util.c": "int util(void){return 1;}\n"})
    _commit(up, "U2 touch core", {"core.c": "int base(void){return 42;}\n"})

    # 假 opencode 桩:PATH 注入,打印确定文本
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "opencode").write_text(
        "#!/bin/sh\n echo 'AGENT-REVIEW-OK: U1 该合(fork 缺 util 上下文成立)'\n", encoding="utf-8")
    (bin_dir / "opencode").chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    return {"tmp": tmp_path, "bin": bin_dir}


def _run_cli(argv: list[str]) -> str:
    with contextlib.redirect_stdout(io.StringIO()) as bo:
        assert cli_main(argv) == 0, bo.getvalue()
    return bo.getvalue()


def test_analyze_agent_appends_review_to_report(env):
    out = _run_cli(["repo", "sync", "up-v20", "--no-index", "--analyze", "e2e-fork",
                    "--analyze-agent"])
    assert "已追加 agent 复核" in out and "AGENT-REVIEW-OK" not in out  # CLI 摘要,不含正文
    reports = sorted((env["tmp"] / "data" / "upstream_reports" / "up-v20").glob("*.md"))
    assert reports, "三态报告应已落盘"
    body = reports[-1].read_text(encoding="utf-8")
    assert "## Agent 复核(--analyze-agent" in body and "AGENT-REVIEW-OK" in body


def test_analyze_agent_honest_skip_without_opencode(env, monkeypatch):
    # PATH 收窄到「不可执行 opencode 的目录 + 系统目录」:夹具早前注入的桩(在 env["bin"])和
    # 真机 nvm 里的 opencode 都不可见,git/ls 等照常 —— 验证 which 找不到时的诚实降级。
    nobin = env["tmp"] / "nobin"
    nobin.mkdir()
    (nobin / "opencode").write_text("#!/bin/sh\n", encoding="utf-8")  # 无 x 位,which 跳过
    monkeypatch.setenv("PATH", f"{nobin}:/usr/bin:/bin")
    out = _run_cli(["repo", "sync", "up-v20", "--no-index", "--analyze", "e2e-fork",
                    "--analyze-agent"])
    assert "opencode 不在 PATH" in out
    reports = sorted((env["tmp"] / "data" / "upstream_reports" / "up-v20").glob("*.md"))
    assert "Agent 复核" not in reports[-1].read_text(encoding="utf-8")  # 报告保持纯三态


def test_ingest_report_uses_project_name(env, monkeypatch):
    calls: list[tuple] = []

    def _fake_ingest(report_path, *, codebase):
        calls.append((str(report_path), codebase))
        return "已入记忆(report 路,3 条,scope=test)"

    monkeypatch.setattr(mirror_mod, "_ingest_report_to_memory", _fake_ingest)
    out = _run_cli(["repo", "sync", "up-v20", "--no-index", "--analyze", "e2e-fork",
                    "--ingest-report"])
    assert "报告入记忆:已入记忆" in out
    assert calls and calls[0][1] == "up", "codebase 应剥掉 -v 版本段用项目名(up-v20 → up)"
    assert calls[0][0].endswith(".md")


def test_analyze_agent_requires_analyze(env):
    with contextlib.redirect_stderr(io.StringIO()):
        assert cli_main(["repo", "sync", "up-v20", "--no-index", "--analyze-agent"]) == 2
        assert cli_main(["repo", "sync", "up-v20", "--no-index", "--ingest-report"]) == 2
