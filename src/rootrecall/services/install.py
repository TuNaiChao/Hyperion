"""opencode 全局注册 + bug 目录现场标记(F2)—— 把「每个目录跑一次接线脚本」简化成「全机一次」。

为什么(面向小白)
------------------------
wire_opencode.sh 给每个 bug 目录接三根线(skills 软链 / AGENTS.md 软链 / opencode.json),
目录一多就烦。opencode 官方支持**用户级全局发现**且与项目配置合并(docs/config、docs/skills、
docs/rules,2026-08-19 查证),三根线都有全局等价物:

| 每目录接的线                | 全局等价(装一次,全机生效)                       |
|-----------------------------|---------------------------------------------------|
| <bug目录>/.claude/skills 链 | ~/.config/opencode/skills/<名>(逐 skill 软链)    |
| <bug目录>/opencode.json     | ~/.config/opencode/opencode.json 的 mcp.rootrecall|
| <bug目录>/AGENTS.md 路由表  | ~/.config/opencode/AGENTS.md(标记段落,可整段换) |

第四件套(2026-08-20):模板 `config/opencode_rootrecall.json` 的 `agent` 块(10 个
`rootrecall-*` subagent —— AGENTS.md「逃生舱:委派 subagent」的实体定义)同样合进全局
opencode.json;不合并的话,姿势①(任意目录提问)下用户按路由表 `@` 点名 subagent 解析不到,
只有从本仓根启动(姿势②)才存在。

之后 `mkdir 任意bug目录 && cd && opencode` 零接线直接问。`rootrecall here` 再补最后一块:
在 bug 目录写 `.rootrecall.yaml`(默认检索库标记,agent/人都读得懂)+ 薄项目 opencode.json
(ROOTRECALL_CODEBASE 按项目覆盖默认索引 —— opencode 配置按项目合并,这里整块写死覆盖最稳)。

诚实边界:全局 AGENTS.md 会注入本机**所有** opencode 会话(不只在 bug 目录)。路由表本身
带条件判据(「对代码库/bug/补丁/记忆的调研分析类需求」才路由),对无关项目只是多占一点
system prompt;介意的话别装全局,退回 wire_opencode.sh 的项目级接线。
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

_MARKER_START = "<!-- rootrecall:routing:start -->"
_MARKER_END = "<!-- rootrecall:routing:end -->"


def install_root() -> Path:
    """RootRecall 安装根(pyproject.toml 所在层;全局注册把绝对路径焊进配置)。"""
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


def opencode_config_home() -> Path:
    """opencode 用户级配置目录:XDG_CONFIG_HOME 优先,回落 ~/.config(opencode 官方约定)。"""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return Path(xdg).expanduser() / "opencode" if xdg else Path.home() / ".config" / "opencode"


def mcp_server_block(root: Path | None = None) -> dict:
    """全局注册写的 mcp.rootrecall 块 —— 与 config/opencode_rootrecall.json 同形,cwd 焊绝对路径
    (MCP 进程锚回安装根:uv 找得到 .venv、data/ 不漂;见 wire_opencode.sh 门3 同理)。
    装机时 shell 里设了 ROOTRECALL_HOME → 透传给 MCP 子进程(opencode 拉起时是干净 env,
    不透传则 server 看不到迁家配置)。"""
    import os

    env = {"PYTHONUNBUFFERED": "1"}
    if home := (os.environ.get("ROOTRECALL_HOME") or "").strip():
        env["ROOTRECALL_HOME"] = home
    return {
        "type": "local",
        "command": ["uv", "run", "--no-sync", "rootrecall", "mcp", "serve"],
        "cwd": str(root or install_root()),
        "environment": env,
        "enabled": True,
        "timeout": 120000,
    }


# ── ① skills:逐个软链进全局发现目录 ─────────────────────────────────────────


def _link_skills(cfg_home: Path, root: Path) -> list[str]:
    skills_src = root / ".claude" / "skills"
    done: list[str] = []
    if not skills_src.is_dir():
        return done
    dst_dir = cfg_home / "skills"
    dst_dir.mkdir(parents=True, exist_ok=True)
    for src in sorted(skills_src.iterdir()):
        if not src.is_dir():
            continue
        dst = dst_dir / src.name
        if dst.is_symlink() or dst.exists():
            if dst.is_symlink():
                tgt = dst.resolve()
                if str(tgt) == str(src.resolve()):
                    done.append(src.name)  # 已链对,幂等跳过
                    continue
                # 指向 */.claude/skills/<同名> 的链 = 旧安装根留下的自家电线(含旧根删除后的悬空链)
                # → 换链到本安装根;换目录重装不再半换链(MCP 跑新仓、skill 吃旧仓,旧根一删全断)。
                parts = tgt.parts
                if tgt.name == src.name and len(parts) >= 3 and parts[-2] == "skills" and parts[-3] == ".claude":
                    dst.unlink()
                    os.symlink(src, dst)
                    done.append(src.name)
                    continue
            # 名字被别的 skill 占了(用户自己的真目录/异构链)→ 不覆盖,跳过
            continue
        os.symlink(src, dst)
        done.append(src.name)
    return done


def _unlink_skills(cfg_home: Path, root: Path) -> list[str]:
    """只摘指向本安装根的软链(别的 skill 绝不碰)。"""
    removed: list[str] = []
    dst_dir = cfg_home / "skills"
    if not dst_dir.is_dir():
        return removed
    for dst in sorted(dst_dir.iterdir()):
        if dst.is_symlink():
            try:
                if str(dst.resolve()).startswith(str(root)):
                    dst.unlink()
                    removed.append(dst.name)
            except OSError:
                pass
    return removed


# ── ② MCP:合并进全局 opencode.json(不动别人的 server)──────────────────────


def _merge_mcp(cfg_home: Path, root: Path) -> str:
    """把 mcp.rootrecall 写进 ~/.config/opencode/opencode.json,返回动作描述。

    安全:文件已有且不含 rootrecall → 备份成 .bak-rootrecall 再改(原文先留底);
    解析失败(JSONC/手改坏)→ 不动原文件,报错让用户手工合并(诚实失败,不猜)。
    """
    cfg_file = cfg_home / "opencode.json"
    if cfg_file.exists():
        try:
            cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            return f"⚠️ 全局 opencode.json 解析失败({e})—— 未改;手工把 mcp.rootrecall 块并进去"
        if "rootrecall" not in (cfg.get("mcp") or {}):
            shutil.copy2(cfg_file, cfg_file.with_name("opencode.json.bak-rootrecall"))
    else:
        cfg = {}
    cfg.setdefault("mcp", {})["rootrecall"] = mcp_server_block(root)
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    cfg_file.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return f"mcp.rootrecall 已注册(cwd={root})"


def _unmerge_mcp(cfg_home: Path) -> str:
    cfg_file = cfg_home / "opencode.json"
    if not cfg_file.exists():
        return "全局 opencode.json 不存在(没装过)"
    try:
        cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return f"⚠️ 解析失败({e})未动"
    if "rootrecall" not in (cfg.get("mcp") or {}):
        return "mcp 里没有 rootrecall(没装过)"
    cfg["mcp"].pop("rootrecall")
    if not cfg["mcp"]:
        cfg.pop("mcp")
    cfg_file.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return "mcp.rootrecall 已移除"


# ── ③ AGENTS.md 路由表:标记段落整块维护 ─────────────────────────────────────


def _routing_block(root: Path) -> str:
    """路由表 = 仓根 AGENTS.md 原文(单一真相,升级后重跑 install 即同步)包标记。"""
    body = (root / "AGENTS.md").read_text(encoding="utf-8")
    return f"{_MARKER_START}\n{body.strip()}\n{_MARKER_END}\n"


def _write_routing(cfg_home: Path, root: Path) -> str:
    ag_file = cfg_home / "AGENTS.md"
    block = _routing_block(root)
    if ag_file.exists():
        text = ag_file.read_text(encoding="utf-8")
        if _MARKER_START in text and _MARKER_END in text:
            pre, _, rest = text.partition(_MARKER_START)
            _, _, post = rest.partition(_MARKER_END)
            new = pre + block + post.lstrip("\n")
            if new != text:
                ag_file.write_text(new, encoding="utf-8")
            return "路由表标记段落已更新"
        ag_file.write_text(text.rstrip("\n") + "\n\n" + block, encoding="utf-8")
        return "路由表已追加(标记段落,重装/卸载整块维护)"
    ag_file.parent.mkdir(parents=True, exist_ok=True)
    ag_file.write_text(block, encoding="utf-8")
    return "AGENTS.md 已创建(仅含 rootrecall 路由标记段落)"


def _remove_routing(cfg_home: Path) -> str:
    ag_file = cfg_home / "AGENTS.md"
    if not ag_file.exists():
        return "AGENTS.md 不存在(没装过)"
    text = ag_file.read_text(encoding="utf-8")
    if _MARKER_START not in text:
        return "AGENTS.md 无 rootrecall 标记(不是我们写的,不动)"
    pre, _, rest = text.partition(_MARKER_START)
    _, _, post = rest.partition(_MARKER_END)
    new = (pre + post).strip()
    if not new:
        ag_file.unlink()  # 全文就我们这一块 → 整个删掉
        return "AGENTS.md 只含路由表,已删除"
    ag_file.write_text(new + "\n", encoding="utf-8")
    return "路由表标记段落已移除(其余内容保留)"


# ── ④ agent 块:rootrecall-* subagent 定义合进全局(姿势① @ 点名的实体)────────


def _merge_agents(cfg_home: Path, root: Path) -> str:
    """把模板 config/opencode_rootrecall.json 的 agent 块(10 个 rootrecall-* subagent)
    合并进全局 opencode.json,返回动作描述。

    纪律与 _merge_mcp 同款:只写自家命名空间(模板列出的 rootrecall-* 键;重跑 = 幂等升级,
    与 mcp.rootrecall 的覆盖语义一致),用户自己的 agent 键绝不碰;解析失败诚实报错不动文件
    (同一安装流程里 .bak-rootrecall 已由 mcp 步留底)。
    """
    tpl_file = root / "config" / "opencode_rootrecall.json"
    try:
        tpl = json.loads(tpl_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return f"⚠️ 模板 {tpl_file} 解析失败({e})—— agent 块未合并"
    agents = tpl.get("agent") or {}
    if not agents:
        return "模板无 agent 块(跳过)"

    cfg_file = cfg_home / "opencode.json"
    if cfg_file.exists():
        try:
            cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            return f"⚠️ 全局 opencode.json 解析失败({e})—— agent 块未合并"
        if cfg.get("agent") is not None and not isinstance(cfg.get("agent"), dict):
            return "⚠️ 全局 opencode.json 的 agent 键不是对象 —— 未动,手工合并"
    else:
        cfg = {}
    merged = cfg.setdefault("agent", {})
    for name, block in agents.items():
        merged[name] = block
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    cfg_file.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return f"agent 块已合并({len(agents)} 个 rootrecall-* subagent)"


def _unmerge_agents(cfg_home: Path) -> str:
    """只摘 rootrecall-* 前缀的 agent 键(用户自己的 agent 绝不碰);摘空则连 agent 键一起删。"""
    cfg_file = cfg_home / "opencode.json"
    if not cfg_file.exists():
        return "全局 opencode.json 不存在(没装过)"
    try:
        cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return f"⚠️ 解析失败({e})未动"
    agents = cfg.get("agent")
    if not isinstance(agents, dict) or not any(k.startswith("rootrecall-") for k in agents):
        return "agent 里没有 rootrecall-*(没装过)"
    for k in [k for k in agents if k.startswith("rootrecall-")]:
        agents.pop(k)
    if not agents:
        cfg.pop("agent")
    cfg_file.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return "rootrecall-* agent 块已移除"


# ── 对外:install / uninstall / here ─────────────────────────────────────────


def install_global(config_home: Path | None = None, root: Path | None = None) -> dict:
    """全局注册四件套(skills 软链 / mcp 合并 / agent 块 / AGENTS.md 路由段),幂等可重跑。"""
    root = root or install_root()
    cfg_home = config_home or opencode_config_home()
    return {
        "config_home": str(cfg_home),
        "skills": _link_skills(cfg_home, root),
        "mcp": _merge_mcp(cfg_home, root),
        "agents": _merge_agents(cfg_home, root),
        "agents_md": _write_routing(cfg_home, root),
    }


def uninstall_global(config_home: Path | None = None, root: Path | None = None) -> dict:
    """摘除全局注册(只动自己写的东西:指向本安装根的软链 / mcp.rootrecall /
    rootrecall-* agent 块 / 标记段落)。"""
    root = root or install_root()
    cfg_home = config_home or opencode_config_home()
    return {
        "config_home": str(cfg_home),
        "skills_removed": _unlink_skills(cfg_home, root),
        "mcp": _unmerge_mcp(cfg_home),
        "agents": _unmerge_agents(cfg_home),
        "agents_md": _remove_routing(cfg_home),
    }


def here(
    project_dir: Path | str | None = None, *, codebase: str | None = None,
    config_home: Path | None = None, root: Path | None = None,
) -> dict:
    """在 bug/工作目录现场做轻量标记(配合全局注册后,这是每个目录唯一要跑的命令)。

    ① `.rootrecall.yaml`:默认检索库标记(agent/人可读;`repo ls` 语义的项目级伴生物);
    ② 项目 `opencode.json`:整块写 mcp.rootrecall(含 ROOTRECALL_CODEBASE env,按项目覆盖
       默认索引)—— opencode 配置按项目合并,整块写覆盖最稳,不依赖嵌套合并语义;
       已有不含 rootrecall 的项目配置 → 备份 .bak 后跳过(与 wire_opencode.sh 同款安全门);
    ③ 目录不是 git 仓 → git init(opencode 项目发现沿 git 根,与 wire 脚本同理)。
    全局注册没做过也照样写(项目级配置自带 mcp 块,单目录自洽可用)。
    """
    import subprocess

    root = root or install_root()
    proj = Path(project_dir) if project_dir else Path.cwd()
    proj.mkdir(parents=True, exist_ok=True)

    actions: dict = {"project": str(proj)}

    if not (proj / ".git").exists():
        subprocess.run(["git", "init", "-q", str(proj)], check=True, capture_output=True)
        actions["git_init"] = True

    marker = proj / ".rootrecall.yaml"
    lines = ["# rootrecall 项目标记(install --global 后由 `rootrecall here` 维护)"]
    if codebase:
        lines.append(f"default_codebase: {codebase}")
    marker.write_text("\n".join(lines) + "\n", encoding="utf-8")
    actions["marker"] = str(marker)

    cfg_file = proj / "opencode.json"
    if cfg_file.is_symlink():
        actions["opencode_json"] = "已是软链,跳过(不穿透软链写文件)"
    elif cfg_file.exists():
        try:
            has_rr = "rootrecall" in json.loads(cfg_file.read_text(encoding="utf-8")).get("mcp", {})
        except (json.JSONDecodeError, OSError):
            has_rr = False
        if not has_rr:
            shutil.copy2(cfg_file, cfg_file.with_name("opencode.json.bak"))
            actions["opencode_json"] = "已有配置不含 rootrecall —— 备份 .bak 后跳过(确认要覆盖:删掉 opencode.json 重跑)"
        else:
            actions["opencode_json"] = "已含 rootrecall,跳过"
    else:
        srv = mcp_server_block(root)
        if codebase:
            srv["environment"]["ROOTRECALL_CODEBASE"] = codebase
        cfg_file.write_text(json.dumps({"mcp": {"rootrecall": srv}}, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        actions["opencode_json"] = f"已生成(mcp.rootrecall.cwd={root}" + (f", 默认检索库={codebase})" if codebase else ")")
    return actions
