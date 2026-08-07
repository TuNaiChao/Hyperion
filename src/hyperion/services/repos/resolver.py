"""ensure_repo —— P-A 的「auto-clone 代码仓」(1a)。

干什么(面向小白)
  build_check / patch-review 要一台"样机"(代码仓)来验货。本地没有时,这里按 config.patch.git 配的
  地址自动 `git clone` 一台到 clone_dir。本地已经有了(显式给的路径、或 clone_dir 里之前 clone 过)
  就直接用,不重 clone(幂等)。

为什么这步归 Hyperion(踩坑 #2 辩护)
  opencode 自己会 `git clone`,但只会去公网拉;用户的"自定义 git 连接"(config.patch.git.remotes
  里配的内网镜像 / SSH url)它不知道。这正是用户需求里"自动 clone(用户自定义 git 连接)"那一项 ——
  所以由 Hyperion 按 config 解析 remote 来 clone,而不让 agent 盲拉公网。

幂等
  同一个名字调两次:第二次命中 clone_dir 里已存在的副本 → 直接返回,不再 clone。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from hyperion.platform.config import AppConfig, get_app_config


def ensure_repo(name_or_path: str, *, cfg: AppConfig | None = None) -> tuple[Path, bool]:
    """解析/取代码仓到本地,返回 ``(本地绝对路径, 是否本次新 clone)``。

    解析顺序:
      1. ``name_or_path`` 是**路径样**(绝对路径或含 ``/``)且存在的本地目录 → 直接用(不 clone)。
         光秃秃的名字不当本地路径(避免跟 cwd 下同名目录撞车),走下面 remotes/clone_dir。
      2. ``clone_dir/<name>`` 已存在 → 直接用(幂等,不重 clone)。``name`` 取 URL/路径末段去 ``.git``。
      3. 否则:remote = ``config.patch.git.remotes[name]``(或按原样拿 ``name_or_path`` 当 git URL);
         ``git clone [--depth 1] <remote> <clone_dir/<name>>``;返回新路径。

    浅克隆由 ``config.patch.git.shallow`` 控制(默认开,省时省空间)。
    clone 失败(网络错 / rc≠0)抛 ``RuntimeError``(带 stderr 尾,调用方友好降级)。
    """
    cfg = cfg or get_app_config()
    git_cfg = cfg.patch.git
    clone_dir = Path(git_cfg.clone_dir)

    # 1. 显式本地路径直接命中 —— 但只认「路径样」输入(绝对路径或含分隔符);
    #    光秃秃的名字(如 "src"/"wpa")不当本地路径,否则碰巧跟 cwd 下同名目录撞车
    #    (例:项目根有 src/,传 "src" 会被误判成命中而不 clone)。名字应走 remotes/clone_dir。
    p = Path(name_or_path)
    looks_like_path = p.is_absolute() or ("/" in name_or_path) or ("\\" in name_or_path)
    if looks_like_path and p.is_dir():
        return p.resolve(), False

    # 2. clone_dir 里按短名命中(幂等:之前 clone 过就别再 clone)。
    name = repo_name(name_or_path)
    dest = clone_dir / name
    if dest.is_dir():
        return dest.resolve(), False

    # 3. 缺则 clone:remote 优先按短名查 config.remotes;查不到就把 name_or_path 当 git URL。
    remote = git_cfg.remotes.get(name) or git_cfg.remotes.get(name_or_path) or name_or_path
    clone_dir.mkdir(parents=True, exist_ok=True)
    cmd: list[str] = ["git", "clone"]
    if git_cfg.shallow:
        cmd += ["--depth", "1"]
    cmd += [remote, str(dest)]

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as e:  # git 不可用 / 超时
        raise RuntimeError(f"git clone 失败({remote}): {e}") from e
    if r.returncode != 0:
        # 不静默:带 stderr 尾让调用方知道为啥挂(认证失败 / 仓不存在 / 网络 …)。
        tail = (r.stderr or "").strip()[-400:]
        raise RuntimeError(f"git clone 失败({remote}):rc={r.returncode} {tail}")
    return dest.resolve(), True


def repo_name(name_or_url: str) -> str:
    """从仓库名或 URL 取短名(末段去 ``.git``、去尾斜杠)。

    例:``wpa_supplicant`` → ``wpa_supplicant``;``https://.../wpa_supplicant.git`` → ``wpa_supplicant``。
    取不到(空)就原样返回,绝不返回空串(空串会落 ``clone_dir`` 本身)。
    """
    s = (name_or_url or "").rstrip("/").split("/")[-1]
    if s.endswith(".git"):
        s = s[:-4]
    return s or (name_or_url or "")
