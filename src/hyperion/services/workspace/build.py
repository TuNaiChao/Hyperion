"""build_check —— P-A 的「试编译门」(Tier 0.5,1a)。

面向小白:验货时把补丁打到一台"样机"(代码仓的临时克隆车位)上,点火看能不能编译过。
  - 装得上(validate_patch 的活,Tier 0)+ 能发动(本模块,Tier 0.5)=「看着靠谱」,但不保证开着不抛锚。
  - 为了不弄脏你正在开的车(主工作树),用一个临时"克隆车位"(git worktree)装,验完拆掉。
  - 封顶在 build:**不跑测试、不复现**(系统软件测试环境太重,用户定)。

设计要点(对标 SWE-bench 的 apply→build 子集,我们停一档不跑 test)
  - worktree 隔离:`git worktree add --detach <tmp> HEAD`,不动主工作树;开不了 → unchecked。
  - 严格 apply:`git apply`(真写,非 --check);不复用 validate 的 3way 降级 —— build 需干净 apply。
  - 构建命令优先级:build_cmd 参 > config.patch.build.commands[<repo>] > 自动探测(Makefile/meson/cmake/configure)。
  - 跑构建:timeout + process-group kill(防 make/gcc 子进程孤儿,借 deer-flow sandbox bounded-bash)。
  - 失败归因:报错引用的源文件若不在补丁改动里 → 标"可能环境/前置问题,非补丁引入"。
  - best-effort:worktree 不支持 / 没构建系统 / apply 失败 → 返 unchecked 或 no + hint,绝不崩。

★v1 决策:单次 patched build(快)+ 失败归因;baseline-compare(无补丁再 build 一次对比)留 backlog
  —— 系统软件 build 慢,翻倍不值;归因提示已能帮人判 builds=no 到底是补丁还是环境。
"""
from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

from hyperion.platform.config import AppConfig, get_app_config

_HONESTY = "build 过 ≠ 包对(只验能编译,没跑测试/没复现);最终正确性靠人/真机。"


def build_check(
    patch: str,
    repo_path: str | Path,
    *,
    build_cmd: str | None = None,
    cfg: AppConfig | None = None,
) -> dict:
    """Tier 0.5 构建门:把 patch 打到 repo 的隔离 worktree → 跑构建 → 返回结果 dict。

    返回 ``{builds, command, errors, attribution, hint}``:
      - builds: "yes"(编译过)| "no"(失败)| "unchecked"(无法判定:worktree / 构建系统 / apply 问题)。
      - command: 实际跑的构建命令(unchecked 时可能为 None)。
      - errors: 失败时的 stderr/stdout 尾(≤2000 字符)。
      - attribution: 失败归因(报错文件在不在补丁改动里)。
      - hint: 给人的提示(含"build 过 ≠ 包对")。
    """
    cfg = cfg or get_app_config()
    timeout = cfg.patch.build.timeout
    repo = Path(repo_path)
    if not repo.is_dir():
        return _unchecked(hint=f"repo_path 不是目录: {repo_path}")
    if not patch or not patch.strip():
        return _unchecked(hint="patch 为空,没法 build 验证")

    # 容错:agent 传补丁常 rstrip 末尾换行 / 带 CRLF → git apply 报"补丁损坏"。归一化(LF + 补末尾换行)。
    patch = patch.replace("\r\n", "\n").replace("\r", "\n")
    if not patch.endswith("\n"):
        patch += "\n"

    # worktree 父目录用 mkdtemp;worktree 路径放其下(git 要求 worktree 路径本身不存在)。
    parent = Path(tempfile.mkdtemp(prefix="hyperion_build_"))
    wt = parent / "wt"
    worktrees: list[Path] = []
    try:
        # 1. 开临时 worktree(不动主工作树)。
        add = subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "--detach", str(wt), "HEAD"],
            capture_output=True, text=True, timeout=60,
        )
        if add.returncode != 0:
            return _unchecked(
                hint=f"开不了 git worktree(老 git / 权限 / 不是 git 仓?):{(add.stderr or '').strip()[-300:]}")
        worktrees.append(wt)

        # 2. 严格 apply(真写)。打不上 → builds=no,提示先 validate_patch。
        ap = subprocess.run(
            ["git", "-C", str(wt), "apply", "--recount"],
            input=patch, capture_output=True, text=True, timeout=60,
        )
        if ap.returncode != 0:
            return {
                "builds": "no", "command": None,
                "errors": (ap.stderr or "").strip()[-600:],
                "attribution": "补丁打不上(路径 / 格式 / context 不匹配)。",
                "hint": "补丁没能干净 apply,先调 validate_patch 排查;build_check 需补丁先能干净打上。",
            }

        # 3. 选构建命令:参 > config.patch.build.commands[<repo名>] > 自动探测。
        cmd = build_cmd or cfg.patch.build.commands.get(repo.name) or _detect_build_cmd(wt)
        if not cmd:
            return _unchecked(
                hint=(f"认不出构建系统(没 Makefile / meson.build / CMakeLists.txt / configure),也没给 build_cmd。"
                      f"在 config.patch.build.commands['{repo.name}'] 里指定,或调工具时传 build_cmd。"))

        # 4. 跑构建(timeout + 杀整组进程,防 make 子进程孤儿)。
        rc, out_tail, err_tail, timed_out = _run_bounded(cmd, cwd=wt, timeout=timeout)

        if timed_out:
            return {"builds": "unchecked", "command": cmd,
                    "errors": f"构建超时({timeout}s 已杀整组进程)。", "attribution": "",
                    "hint": "超时多半是环境慢 / 依赖重,非补丁问题;调大 config.patch.build.timeout。"}
        if rc == 0:
            return {"builds": "yes", "command": cmd, "errors": "",
                    "attribution": "编译过(apply + build 都过)。", "hint": _HONESTY}

        # rc != 0:失败,做归因(报错文件在不在补丁改动里)。
        changed = _changed_files(patch)
        return {"builds": "no", "command": cmd,
                "errors": (err_tail or out_tail)[-2000:].strip(),
                "attribution": _attribute(err_tail + "\n" + out_tail, changed),
                "hint": _HONESTY}
    finally:
        # 5. 清理(无论成败):先 git 解注册 worktree,再删物理目录。
        for w in worktrees:
            subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(w)],
                           capture_output=True, text=True, timeout=30)
        shutil.rmtree(parent, ignore_errors=True)


def _unchecked(*, hint: str) -> dict:
    return {"builds": "unchecked", "command": None, "errors": "", "attribution": "", "hint": hint}


def _detect_build_cmd(workdir: Path) -> str | None:
    """自动认构建系统(常见系统软件四件:Makefile / meson / cmake / configure)。认不出 → None。"""
    if (workdir / "Makefile").exists():
        return "make"
    if (workdir / "meson.build").exists():
        return "meson setup builddir && meson compile -C builddir"
    if (workdir / "CMakeLists.txt").exists():
        return "cmake -B builddir && cmake --build builddir"
    if (workdir / "configure").exists():
        return "./configure && make"
    return None


def _run_bounded(cmd: str, *, cwd: Path, timeout: float) -> tuple[int, str, str, bool]:
    """跑一条 shell 构建命令,带 timeout + process-group kill。

    用 Popen + start_new_session=True(子进程自成新会话 / 进程组),超时 os.killpg 杀整组
    —— make / gcc 常派生子进程,只杀父进程会留孤儿继续吃 CPU(借 deer-flow sandbox bounded-bash)。
    返回 (rc, stdout 尾, stderr 尾, 是否超时)。
    """
    proc = subprocess.Popen(
        ["sh", "-c", cmd], cwd=str(cwd),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, _tail(out), _tail(err), False
    except subprocess.TimeoutExpired:
        # 杀整组(start_new_session 让 proc.pid == pgid),收尸,返回超时标记。
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            out, err = proc.communicate(timeout=10)
        except Exception:  # noqa: BLE001 - 清理阶段,收不回输出也不影响判定
            out, err = "", ""
        return 1, _tail(out or ""), _tail(err or ""), True


def _tail(s: str, n: int = 2000) -> str:
    return (s or "")[-n:]


def _changed_files(patch: str) -> set[str]:
    """从 unified diff 抽改动的文件集合(失败归因用,取文件名)。取每 hunk 的 ``+++ b/<path>``。"""
    files: set[str] = set()
    for line in (patch or "").splitlines():
        if line.startswith("+++ "):
            rest = line[4:].strip().split("\t")[0]
            if rest.startswith("b/"):
                rest = rest[2:]
            if rest and rest != "/dev/null":
                files.add(Path(rest).name)
    return files


def _attribute(log: str, changed: set[str]) -> str:
    """失败归因(best-effort 启发式):报错引用的源文件若不在补丁改动集合 → 提示可能是环境/前置问题。

    抓报错里 ``.c/.h/.cpp/...`` 这类源文件名 token,跟 changed 比文件名。不精确,只给人一个线索。
    """
    if not changed:
        return "补丁改动文件未解析出,无法归因。"
    refs = set(re.findall(r"\b([A-Za-z0-9_./-]+\.(?:c|h|cpp|hpp|cc|hh|cxx|hxx|S))\b", log))
    ref_files = {Path(r).name for r in refs}
    outside = sorted(ref_files - changed)
    inside = sorted(ref_files & changed)
    parts = [f"补丁改动文件:{sorted(changed)[:10]}"]
    if outside:
        parts.append(f"⚠️ 报错引用了未改动的文件({', '.join(outside[:6])})—— 可能是本机环境/前置依赖问题,非补丁引入")
    if inside:
        parts.append(f"报错落在补丁改动文件内({', '.join(inside[:6])})")
    return "。".join(parts) + "。"
