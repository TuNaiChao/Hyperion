"""workspace 管理(R2 末最简):每个 bug 一个专用工作目录。

这一层干什么(面向小白)
------------------------
delegate(opencode)干活前,给它搭一个"工位" —— 一个独立目录,里面放全量代码(code/)、
问题描述(triggers/issue.md)、契约(AGENTS.md)。opencode 在这个目录里读码、改码,
不污染原仓库。这就是 workspace(方式 B 的载体)。

为什么不能直接在原仓库跑:① opencode 会 edit 改文件,直接在原仓跑就改坏了原仓;
② 每个 bug 该有自己的"案卷"目录,归拢补丁/报告/artifacts;③ 隔离后能 git diff 观察
opencode 的改动生成正确补丁(根治 delegate 吐 diff 的 off-by-one)。

R2 末最简形态:只建 code/ + triggers/issue.md + delegate/ + patch/ + report/ + AGENTS.md
(完整七段 triggers/logs/poc、artifacts/、docs/ 等 R3,见 workspace-design.md §2/§8)。

对标 deer-flow per-thread per-user sandbox(每 thread 一个目录);隔离用本地目录
(Docker 留 R5,见 workspace-design.md §3)。
"""
from __future__ import annotations

import shutil
from pathlib import Path

# workspace 根(R2 暂写死 data/workspaces/;R3 进 config.yaml 可配)
WORKSPACE_ROOT = Path("data/workspaces")

# AGENTS.md 模板:opencode 自动发现并注入 system prompt,强制契约。
# 关键:让 opencode「自读 code/、用 edit 改文件、返回 JSON 不要 patch」——
# patch 由 Hyperion 用 git diff 观察 code/ 改动生成(行号/格式天然对)。
_AGENTS_MD = """\
# bug-RCA 工作目录契约(opencode 强制遵守)

你是 C/系统软件 bug 根因定位专家。**当前目录就是你的工作区**:

- **代码在 `./code/`**(全量源码)—— 用 read/grep 工具**自读**(嫌疑起点见任务,但可自行扩展探索)。
- **直接用 edit 工具改 `./code/` 里的文件来修 bug**。**不要把 unified diff 贴在回复里** ——
  Hyperion 会用 `git diff` 观察你对 code/ 的实际改动来生成补丁,这样行号/格式天然正确。
- 定位后**只返回 JSON**:`{root_cause, evidence[{file,line,note}], trigger_chain[], confidence, blast_radius_files[]}`。
  **不要 patch 字段**(补丁从你的 edit 改动取)。
- 证据必须锚 `code/` 内相对路径的 `file:line`。
- 只改根因相关文件,禁止顺手重构。
"""


def create_workspace(
    repo_root: Path | str, trigger: str, *, bug_id: str | None = None
) -> Path:
    """建一个 bug 专用 workspace,返回它的路径。

    repo_root:原代码仓(整体复制进 code/,opencode 在 code/ 改,原仓不动)。
    trigger:  bug 线索(写进 triggers/issue.md)。
    bug_id:   bug 标识;不给则用时间戳。

    产出目录结构(R2 末最简):
      <repo>__<bug_id>/
      ├── code/          # 全量代码 cp(含 .git,供 git diff;大仓 R3 改 git worktree 省空间)
      ├── triggers/issue.md
      ├── delegate/      # node_assemble 写 prompt.md(方式 B 指引)
      ├── patch/         # node_verify 写 final.diff(git diff code/ 的产物)
      ├── report/        # node_report_memorize 写报告
      └── AGENTS.md      # opencode 自动读的强制契约
    """
    import time  # 延迟到调用时,避免模块级 import 影响冷启动

    repo_root = Path(repo_root)
    name = bug_id or time.strftime("%Y%m%d-%H%M%S")
    ws = WORKSPACE_ROOT / f"{repo_root.name}__{name}"
    # 同 bug 重跑:已存在先清(保持干净;R3 改成 archive 而非删)
    if ws.exists():
        shutil.rmtree(ws)
    ws.mkdir(parents=True)

    # code/:复制全量代码(含 .git → git diff 能跑)。opencode 在此读+改,原仓不动。
    shutil.copytree(repo_root, ws / "code")

    # triggers/:问题描述(R2 最简只 issue.md;logs/poc R3 随 log_preprocess)
    (ws / "triggers").mkdir()
    (ws / "triggers" / "issue.md").write_text(trigger, encoding="utf-8")

    # delegate/ + patch/ + report/:产出目录(node_assemble/verify/report 节点往里写)
    (ws / "delegate").mkdir()
    (ws / "patch").mkdir()
    (ws / "report").mkdir()

    # AGENTS.md:opencode 从 cwd 向上遍历自动发现,注入 system prompt(强制契约)
    (ws / "AGENTS.md").write_text(_AGENTS_MD, encoding="utf-8")

    return ws
