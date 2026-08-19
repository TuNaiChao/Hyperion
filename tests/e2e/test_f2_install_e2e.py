"""F2 · opencode 全局注册 + `here` 端到端(假 config home,不碰真 ~/.config)。

验证用户视角的三件事:
  ① 装一次:skills 软链 + mcp.rootrecall 合并 + AGENTS.md 路由段落全部就位;
     **已有别人配置时不破坏**(mcp 里别的 server 保留、AGENTS.md 别的内容保留);
  ② 幂等:装两次结果一致;升级(路由表变化)后重装能整段刷新;
  ③ 卸载:只摘自己写的,别人的 server/skill/AGENTS.md 内容原样保留;
  ④ `here`:bug 目录里写标记 + 项目 opencode.json(含 ROOTRECALL_CODEBASE),已有非 rootrecall
     配置时备份不覆盖。
"""

from __future__ import annotations

import json

from rootrecall.services.install import (
    _MARKER_END,
    _MARKER_START,
    here,
    install_global,
    install_root,
    uninstall_global,
)


def test_f2_global_install_uninstall_e2e(tmp_path):
    cfg = tmp_path / "opencode-config"
    root = install_root()

    # ── ① 首装:已有「别人的」全局配置 → 合并不破坏 ──────────────────────────
    (cfg / "skills").mkdir(parents=True)
    (cfg / "skills" / "my-own-skill").mkdir()          # 用户自己的 skill(非软链)
    (cfg / "opencode.json").write_text(json.dumps({
        "mcp": {"other-server": {"type": "remote", "url": "https://x/mcp"}},
        "theme": "dark",
    }), encoding="utf-8")
    (cfg / "AGENTS.md").write_text("# 我的全局偏好\n用中文回答。\n", encoding="utf-8")

    r1 = install_global(config_home=cfg, root=root)
    assert len(r1["skills"]) >= 8, f"8 个 skill 应逐一软链: {r1['skills']}"
    for name in r1["skills"]:
        dst = cfg / "skills" / name
        assert dst.is_symlink() and str(dst.resolve()).startswith(str(root))
    assert (cfg / "skills" / "my-own-skill").is_dir() and not (cfg / "skills" / "my-own-skill").is_symlink()

    g = json.loads((cfg / "opencode.json").read_text(encoding="utf-8"))
    assert g["mcp"]["other-server"]["url"] == "https://x/mcp"   # 别人的 server 保留
    assert g["theme"] == "dark"
    rr = g["mcp"]["rootrecall"]
    assert rr["type"] == "local" and rr["cwd"] == str(root)
    assert rr["command"] == ["uv", "run", "--no-sync", "rootrecall", "mcp", "serve"]
    assert (cfg / "opencode.json.bak-rootrecall").exists()      # 改前留底

    ag = (cfg / "AGENTS.md").read_text(encoding="utf-8")
    assert "我的全局偏好" in ag and _MARKER_START in ag and _MARKER_END in ag
    assert "rootrecall`" in ag or "RootRecall" in ag            # 路由表内容进来了

    # ── ② 幂等:装两次,配置不重复、不漂 ──────────────────────────────────────
    before = (cfg / "opencode.json").read_text(encoding="utf-8")
    ag_before = (cfg / "AGENTS.md").read_text(encoding="utf-8")
    install_global(config_home=cfg, root=root)
    assert (cfg / "opencode.json").read_text(encoding="utf-8") == before
    assert (cfg / "AGENTS.md").read_text(encoding="utf-8") == ag_before

    # 升级语义:标记段落内容变了 → 重装整段刷新,段外内容不动。
    ag_mutated = ag_before.replace("我的全局偏好", "我的全局偏好(改)")
    (cfg / "AGENTS.md").write_text(ag_mutated, encoding="utf-8")
    install_global(config_home=cfg, root=root)
    assert "我的全局偏好(改)" in (cfg / "AGENTS.md").read_text(encoding="utf-8")

    # ── ③ 卸载:只摘自己的 ──────────────────────────────────────────────────
    u = uninstall_global(config_home=cfg, root=root)
    assert set(u["skills_removed"]) == set(r1["skills"])
    assert not (cfg / "skills" / r1["skills"][0]).exists()
    assert (cfg / "skills" / "my-own-skill").is_dir()           # 别人的 skill 还在
    g2 = json.loads((cfg / "opencode.json").read_text(encoding="utf-8"))
    assert "rootrecall" not in g2["mcp"] and g2["mcp"]["other-server"]
    ag2 = (cfg / "AGENTS.md").read_text(encoding="utf-8")
    assert _MARKER_START not in ag2 and "我的全局偏好(改)" in ag2  # 段落摘除,用户内容保留


def test_f2_uninstall_removes_lone_agents_md(tmp_path):
    """AGENTS.md 全文只有我们写的路由段 → 卸载时整个文件删掉,不留空壳。"""
    cfg = tmp_path / "cfg"
    install_global(config_home=cfg, root=install_root())
    assert (cfg / "AGENTS.md").exists()
    uninstall_global(config_home=cfg, root=install_root())
    assert not (cfg / "AGENTS.md").exists()


def test_f2_here_marks_bug_dir(tmp_path, monkeypatch):
    """`rootrecall here`:标记 + 项目 opencode.json;已有别人配置 → 备份不覆盖。"""
    monkeypatch.chdir(tmp_path)
    bug = tmp_path / "bug-0012"
    bug.mkdir()

    here(project_dir=bug, codebase="bluez-v20", root=install_root())
    assert (bug / ".git").exists()                              # 自动 git init
    assert "bluez-v20" in (bug / ".rootrecall.yaml").read_text(encoding="utf-8")
    proj = json.loads((bug / "opencode.json").read_text(encoding="utf-8"))
    assert proj["mcp"]["rootrecall"]["environment"]["ROOTRECALL_CODEBASE"] == "bluez-v20"

    # 已有自己的 opencode.json(不含 rootrecall)→ 备份 + 跳过,不覆盖。
    bug2 = tmp_path / "bug-0013"
    bug2.mkdir()
    (bug2 / "opencode.json").write_text('{"theme": "light"}', encoding="utf-8")
    r2 = here(project_dir=bug2, codebase="wpa-v25", root=install_root())
    assert "备份" in r2["opencode_json"]
    assert json.loads((bug2 / "opencode.json").read_text(encoding="utf-8")) == {"theme": "light"}
    assert (bug2 / "opencode.json.bak").exists()
    # 标记文件仍写了(agent 仍能读到默认检索库)。
    assert "wpa-v25" in (bug2 / ".rootrecall.yaml").read_text(encoding="utf-8")
