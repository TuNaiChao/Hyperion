"""bare 镜像 + git worktree 仓库布局(F3)—— 同一条版本线的多次检出共享一份对象库。

为什么(面向小白)
------------------------
没有这层之前,「v20 的 5.50.61 出了个 bug」要整仓 clone/复制一份(几十 MB 起,含 .git);
bug 多了目录就爆炸(data/workspaces 曾攒到 991M)。而同一条发行版线的小版本之间代码
差异极小 —— git 的 worktree 机制让 N 份工作树共享一个 bare 仓的对象库:

  data/mirrors/<名>.git        bare 全量 clone(基线仓;upstream-merge 的 patch-id/merge-tree
                               需要完整历史,所以这里**不浅克隆** —— shallow 只适合 patch-review 样机)
  data/worktrees/<检出名>/     worktree(秒级创建、几乎零额外对象;bug 分析完 gc 级联删)

布局对齐 git worktree 官方推荐用法(共享对象库是它设计内置的,比 clone --shared/alternates
安全 —— 后者源仓删除后会损坏);v20/v25 两条独立 git 线 = 两个独立 mirror,互不共享。
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def mirrors_root() -> Path:
    from rootrecall.services.repos.registry import data_root

    return data_root() / "mirrors"


def worktrees_root() -> Path:
    from rootrecall.services.repos.registry import data_root

    return data_root() / "worktrees"


def _git(args: list[str], *, cwd: Path | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True, timeout=timeout,
                          cwd=str(cwd) if cwd else None)


def ensure_mirror(name: str, url: str, *, root: Path | None = None) -> tuple[Path, bool]:
    """确保 bare 镜像在盘:``<root>/<name>.git``。返回 (镜像路径, 是否本次新 clone)。

    幂等:已存在直接用(要更新走 fetch_mirror / repo sync)。clone 用 --bare + 全量
    (基线要完整历史;失败抛 RuntimeError 带 stderr 尾)。
    """
    base = (root or mirrors_root()) / f"{name}.git"
    if base.is_dir():
        return base, False
    base.parent.mkdir(parents=True, exist_ok=True)
    r = _git(["clone", "--bare", url, str(base)], timeout=1800)
    if r.returncode != 0:
        raise RuntimeError(f"bare clone 失败({url} -> {base}):{(r.stderr or '').strip()[-400:]}")
    return base, True


def fetch_mirror(mirror: Path) -> dict:
    """bare 镜像拉新(fetch --prune origin)。"""
    r = _git(["fetch", "--prune", "origin"], cwd=mirror)
    if r.returncode != 0:
        raise RuntimeError(f"fetch 失败({mirror}):{(r.stderr or '').strip()[-400:]}")
    return {"ok": True}


def mirror_ref(mirror: Path, ref: str) -> str:
    """把用户给的 ref 解析成镜像里存在的完整 ref(fetch 后分支在 refs/remotes/origin/* 下)。

    解析顺序:refs/tags/<ref> → refs/remotes/origin/<ref> → refs/heads/<ref> → 原样返回
    (让 git 自己报错,如 sha)。bare clone 的本地分支只有克隆时 HEAD 指的一条,所以
    必须先探 origin/*。
    """
    for candidate in (f"refs/tags/{ref}", f"refs/remotes/origin/{ref}", f"refs/heads/{ref}"):
        if _git(["show-ref", "--verify", "--quiet", candidate], cwd=mirror).returncode == 0:
            return candidate
    return ref


def add_worktree(mirror: Path, ref: str, dest: Path, *, new_branch: str | None = None) -> tuple[Path, bool]:
    """从镜像开一个 worktree 检出到 dest。返回 (dest, 是否本次新建)。幂等:dest 已是 git 工作树 → 直接用。

    默认 --detach(检出指定 ref 不占分支位 —— 同一 ref 可开多份,bug 检出互不干扰);
    new_branch 给了则 -b(要留分支的场景)。
    """
    if (dest / ".git").exists():
        return dest, False
    dest.parent.mkdir(parents=True, exist_ok=True)
    full_ref = mirror_ref(mirror, ref)
    argv = ["worktree", "add"]
    argv += (["-b", new_branch, str(dest), full_ref] if new_branch
             else ["--detach", str(dest), full_ref])
    r = _git(argv, cwd=mirror)
    if r.returncode != 0:
        raise RuntimeError(f"worktree add 失败({mirror} {ref} -> {dest}):{(r.stderr or '').strip()[-400:]}")
    return dest, True


def remove_worktree(dest: Path, *, mirror: Path | None = None) -> bool:
    """删一个 worktree:优先 `git worktree remove --force`(顺带清镜像侧簿记),失败/没镜像则
    rmtree 兜底;最后 prune 悬空簿记。返回是否删了东西。"""
    import shutil

    if not dest.exists():
        if mirror is not None:
            _git(["worktree", "prune"], cwd=mirror)
        return False
    removed = False
    if mirror is not None:
        r = _git(["worktree", "remove", "--force", str(dest)], cwd=mirror)
        removed = r.returncode == 0
        _git(["worktree", "prune"], cwd=mirror)
    if not removed and dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
        removed = True
        if mirror is not None:
            _git(["worktree", "prune"], cwd=mirror)
    return removed


# ── sync:基线仓定时更新 + 上游三态分析(F4)───────────────────────────────────


def sync_repo(
    name: str,
    *,
    analyze_fork: str | None = None,
    analyze_agent: bool = False,
    ingest_report: bool = False,
    refresh_index: bool = True,
    embedder=None,
    registry=None,
    code_index_dir: Path | None = None,
    reports_dir: Path | None = None,
    max_commits: int = 50,
) -> dict:
    """同步一个 baseline 仓:fetch --prune → 基线检出 ff 跟进 → (可选)增量刷新索引 →
    (可选)对 fork 仓跑上游三态分析出报告。

    干什么(面向小白):你有一批「共享基线仓」(bluez 上游、uos v20 线…)永久保留、定时更新。
    本函数就是定时器里跑的那条幂等命令:
      1. fetch --prune origin(镜像模式 fetch bare;检出模式 fetch 工作树);
      2. 检出模式:HEAD..origin/<branch> 的新 commit 清单;能 ff 则 `merge --ff-only` 跟进
         (不能 ff = 本地有分叉,诚实报告不强推);
      3. refresh_index 且给了 embedder → 增量重建索引(sha256 增量,只重嵌变化文件);
      4. analyze_fork 给了另一个注册仓名 → 在 fork 检出里 fetch 上游,用 merge_eval 的纯 git
         三态判定(已修/建议合/冲突,零 LLM)出报告,落 data/upstream_reports/<名>/<时间戳>.md;
      5. 回写 last_synced_at / synced_sha(下次 --analyze 的范围底)。

    返回汇总 dict;fetch 失败抛 RuntimeError(带 stderr 尾)。
    """
    from rootrecall.services.repos.registry import RepoRegistry

    reg = registry or RepoRegistry()
    rec = reg.get(name)
    if rec is None:
        raise RuntimeError(f"未注册: {name}(先 rootrecall repo register {name} --url ... --role baseline)")
    if rec.role != "baseline":
        return {"name": name, "skipped": f"role={rec.role},sync 只管 baseline"}

    out: dict = {"name": name, "url": rec.url, "branch": rec.branch}
    work = Path(rec.path) if rec.path and (Path(rec.path) / ".git").exists() else None

    if work is not None:
        # 检出模式:确保 origin 指向记录的 url(没配 remote 才补;已配的不动 —— 尊重用户自定义)。
        if rec.url and _git(["remote", "get-url", "origin"], cwd=work).returncode != 0:
            _git(["remote", "add", "origin", rec.url], cwd=work)
        old_head = _git(["rev-parse", "HEAD"], cwd=work).stdout.strip()
        if _git(["fetch", "--prune", "origin"], cwd=work).returncode != 0:
            raise RuntimeError(f"fetch 失败({work})")
        target = f"origin/{rec.branch}" if rec.branch else "origin/HEAD"
        if _git(["rev-parse", "--verify", "--quiet", target], cwd=work).returncode != 0:
            out["note"] = f"远端没有 {target},只 fetch 不跟进"
            target = None
        if target is not None:
            new_head = _git(["rev-parse", target], cwd=work).stdout.strip()
            commits = _git(["log", "--oneline", f"{old_head}..{target}"], cwd=work).stdout.splitlines()
            out.update(old_head=old_head, new_head=new_head,
                       new_commits=[c.strip() for c in commits if c.strip()])
            if new_head != old_head:
                ff = _git(["merge", "--ff-only", target], cwd=work)
                out["fast_forwarded"] = ff.returncode == 0
                if ff.returncode != 0:
                    out["note"] = "不能 fast-forward(本地有分叉/脏树),HEAD 未动 —— 人工处理"
        else:
            out.setdefault("old_head", old_head)
    elif rec.url:
        # 镜像模式:没有常驻检出 → 只把 bare 镜像 fetch 新(refs 报告),不 ff、不建索引。
        mirror, _ = ensure_mirror(name, rec.url)
        fetch_mirror(mirror)
        out["mirror"] = str(mirror)
        out["note"] = "无常驻检出(只 fetch 镜像);要跟进: repo checkout 或 register --path"
    else:
        return {"name": name, "skipped": "无 path 也无 url,没东西可同步"}

    # 增量刷新索引(检索跟代码同步;embedder None = 调用方明确不要 embed,跳过并注明)。
    if refresh_index and work is not None and embedder is not None:
        from rootrecall.services.code_index.index import build_index
        from rootrecall.services.repos.registry import data_root

        stats = build_index(work, rec.index_name, embedder,
                            code_index_dir or data_root() / "code_index")
        out["index"] = {"mode": stats.get("mode"), "indexed": stats.get("indexed")}
    elif refresh_index and work is not None:
        out["index"] = "skipped(未提供 embedder;CLI --no-index 或 key 缺失时如此)"

    # 上游三态分析:在 fork 检出里 fetch 本基线的上游,merge_eval 纯 git 判三态,报告落盘。
    if analyze_fork:
        out["analysis"] = _analyze_against_fork(
            rec, fork_name=analyze_fork, registry=reg,
            base_sha=(rec.synced_sha or out.get("old_head") or out.get("new_head")),
            reports_dir=reports_dir, max_commits=max_commits)
        # --analyze-agent:纯三态之后追加 headless opencode 的「该不该合」相关性复核
        # (patch-id/merge-tree 只答能不能合,fork 真需不需要要读代码 —— agent 的活)。
        if analyze_agent:
            a = out["analysis"]
            fork_rec = reg.get(analyze_fork) if isinstance(a, dict) else None
            if isinstance(a, dict) and a.get("report") and fork_rec and fork_rec.path:
                a["agent_review"] = _agent_review_report(
                    Path(a["report"]), baseline=rec.name,
                    fork=analyze_fork, fork_path=Path(fork_rec.path))
            else:
                out["analysis"]["agent_review"] = "skipped(三态分析未产出报告或 fork 无检出)"
        # --ingest-report:报告(含 agent 复核)摄取进记忆,recall 能带出「上次为什么没合」。
        # codebase 用项目名(记忆约定:不带版本线;bluez-v20 → bluez,名字里没有 -v<版> 就原样)。
        if ingest_report and isinstance(out.get("analysis"), dict) and out["analysis"].get("report"):
            project = rec.name.rsplit("-v", 1)[0] if "-v" in rec.name else rec.name
            out["analysis"]["ingest"] = _ingest_report_to_memory(
                Path(out["analysis"]["report"]), codebase=project)

    import datetime

    reg.register(name, last_synced_at=datetime.date.today().isoformat(),
                 synced_sha=out.get("new_head") or rec.synced_sha)
    return out


def _analyze_against_fork(rec, *, fork_name: str, registry, base_sha, reports_dir, max_commits: int) -> dict:
    """把本基线的新上游 commit 拿到 fork 仓里评三态(merge_eval,零 LLM),报告落盘返回摘要。"""
    from rootrecall.services.code_index.code_graph import merge_eval

    fork_rec = registry.get(fork_name)
    if fork_rec is None or not fork_rec.path or not (Path(fork_rec.path) / ".git").exists():
        return {"error": f"fork 未注册或无检出: {fork_name}"}
    fork = Path(fork_rec.path)
    url = rec.url
    if url is None:
        return {"error": f"基线 {rec.name} 无 url,fork 侧没法 fetch 它"}
    # fork 仓挂 upstream remote(幂等:已配则不动)并 fetch —— upstream-merge skill 的前置手工步,这里代做。
    if _git(["remote", "get-url", "upstream"], cwd=fork).returncode == 0:
        cur = _git(["remote", "get-url", "upstream"], cwd=fork).stdout.strip()
        if cur != url:
            _git(["remote", "set-url", "upstream", url], cwd=fork)
    else:
        _git(["remote", "add", "upstream", url], cwd=fork)
    if _git(["fetch", "--prune", "upstream"], cwd=fork).returncode != 0:
        return {"error": f"fork 侧 fetch upstream 失败({fork})"}

    head_ref = f"upstream/{rec.branch}" if rec.branch else "upstream/HEAD"
    base_ref = base_sha or head_ref  # 无历史锚点 → 空范围(首跑只对账,下次起有 synced_sha)
    try:
        result = merge_eval(base_ref, head_ref, fork_ref="HEAD", repo_path=str(fork),
                            max_commits=max_commits)
    except ValueError as e:
        return {"error": f"merge_eval 没法算({base_ref}..{head_ref}): {e}"}

    report_path = _write_sync_report(rec, fork_rec, result, reports_dir=reports_dir)
    return {"report": str(report_path), "summary": result.get("summary", {}),
            "range": result.get("upstream_range")}


def _write_sync_report(rec, fork_rec, result: dict, *, reports_dir: Path | None) -> Path:
    """三态分析报告落 markdown:data/upstream_reports/<基线名>/<时间戳>.md。"""
    import datetime

    from rootrecall.services.repos.registry import data_root

    root = reports_dir or data_root() / "upstream_reports"
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    d = root / rec.name
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{ts}-sync.md"

    s = result.get("summary", {})
    lines = [
        f"# 上游同步分析:{rec.name} → {fork_rec.name}",
        f"- 生成:{ts}(`rootrecall repo sync {rec.name} --analyze --fork {fork_rec.name}`)",
        f"- 上游范围:`{result.get('upstream_range')}`  对照 fork:`{result.get('fork_ref')}`",
        f"- 三态:total={s.get('total', 0)} | already_fixed={s.get('already_fixed', 0)} "
        f"| recommend_merge={s.get('recommend_merge', 0)} | conflict={s.get('conflict', 0)} "
        f"| uncertain={s.get('uncertain', 0)}",
        "",
        "> 三态是纯 git 确定性事实(patch-id 等价 + merge-tree 零 touch):「该不该合」的相关性",
        "> 判断归人/agent(upstream-merge skill)。already_fixed 无需动作;recommend_merge 是候选,",
        "> 合前请过 upstream-merge 流程;conflict 需人工解冲突。",
        "",
        "| commit | 三态 | 触及文件 |",
        "|---|---|---|",
    ]
    for c in result.get("commits", []):
        sha = (c.get("sha") or c.get("commit") or "")[:10]
        subject = (c.get("subject") or "").replace("|", "\\|")
        state = c.get("state") or c.get("tri_state") or "?"
        files = ", ".join((c.get("touched_files") or [])[:5]) or "-"
        lines.append(f"| `{sha}` {subject} | {state} | {files} |")
    if result.get("note"):
        lines += ["", f"注:{result['note']}"]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _agent_review_report(report_path: Path, *, baseline: str, fork: str,
                         fork_path: Path, timeout: int = 900) -> str:
    """headless opencode 复核 sync 报告(upstream-merge 的「该不该合」语义判断),结论追加进报告。

    纯三态(patch-id / merge-tree)只答「能不能合」,答不了「fork 真需不需要」—— 那要读 fork
    代码,归 agent。这里把报告交给 headless opencode 复核,回复原样追加(标明来源与时间)。
    诚实降级三档:opencode 不在 PATH / 超时 / 非零退出或空输出 → 报告保持纯三态,返回 skip
    说明(sync 不因此失败;env 里的 LLM key 由调用方 CLI 进程带下,子进程继承 os.environ)。
    """
    import datetime
    import shutil as _shutil
    import subprocess as _sp

    from rootrecall.services.repos.registry import _install_root

    exe = _shutil.which("opencode")
    if exe is None:
        return "skipped(opencode 不在 PATH;报告保持纯三态,该不该合由人复核)"
    prompt = (
        f"你是 upstream-merge 复核员,只读不改代码。读报告 {report_path}"
        f"(基线 {baseline} 新进上游 commit 的三态评估;fork={fork},检出在 {fork_path})。\n"
        f"对报告里每个判为 recommend_merge 的 commit:用 bash `git -C {fork_path} show <sha>` 读改动"
        f"与 fork 侧上下文,判断 fork 是否真需要它(fork 有没有该 bug / 功能的代码上下文)。\n"
        f"输出一张 markdown 表:| sha | subject | 结论(该合/不该合/存疑) | 一句话理由 |,"
        f"表后给一段总体建议。控制在 10 次工具调用内;你的回复会被原样追加进报告。"
    )
    try:
        r = _sp.run([exe, "run", prompt], cwd=str(_install_root()),
                    capture_output=True, text=True, timeout=timeout)
    except _sp.TimeoutExpired:
        return f"skipped(opencode 超时 >{timeout}s;报告保持纯三态)"
    out = (r.stdout or "").strip()
    if r.returncode != 0 or not out:
        tail = ((r.stderr or "") + (r.stdout or "")).strip()[-200:]
        return f"skipped(opencode rc={r.returncode}{'; ' + tail if tail else ''};报告保持纯三态)"
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    old = report_path.read_text(encoding="utf-8")
    report_path.write_text(f"{old}\n\n---\n\n## Agent 复核(--analyze-agent,{ts})\n\n{out}\n",
                           encoding="utf-8")
    return f"已追加 agent 复核({len(out)} 字符)→ {report_path}"


def _ingest_report_to_memory(report_path: Path, *, codebase: str) -> str:
    """把 sync 报告摄取进记忆库(codebase=项目名,recall-first 让「上次为什么没合」可召回)。

    report 路每块走 LLM 抽取(memorize_report)—— 调用方在配好 LLM key 的 CLI 进程里;
    失败不挡 sync,返回 skip 说明。
    """
    import asyncio

    from rootrecall.services.memory.ingest import ingest_document
    from rootrecall.services.memory.schema import Scope

    try:
        stats = asyncio.run(ingest_document(
            report_path, scope=Scope(owner="default", codebase=codebase), repo=codebase))
        return (f"已入记忆({stats.get('route')} 路,{stats.get('wrote', 0)} 条,scope={codebase})")
    except Exception as e:  # noqa: BLE001 —— 记忆摄取失败不挡 sync,诚实注明
        return f"skipped(ingest 失败:{e})"
