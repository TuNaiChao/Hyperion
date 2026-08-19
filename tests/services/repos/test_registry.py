"""F1 · repo registry 单测(data/repos.yaml 读写 / upsert / 模糊查找 / 名字→路径反查)。

零网络、零 git(反查链里的 clone 分支在 test_resolver.py 已覆盖)。注册表写到 tmp_path。
"""

from __future__ import annotations

import subprocess

import pytest
import yaml

from rootrecall.services.repos.registry import (
    ROLE_BASELINE,
    ROLE_EPHEMERAL,
    RepoRegistry,
    resolve_repo_path,
)


@pytest.fixture()
def reg(tmp_path):
    return RepoRegistry(tmp_path / "repos.yaml")


# ── CRUD / upsert 语义 ────────────────────────────────────────────────────────


def test_register_and_get_roundtrip(reg, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    reg.register("bluez-v20", path=str(src), url="https://intr/bluez.git",
                 role=ROLE_BASELINE, branch="v20-stable")
    rec = reg.get("bluez-v20")
    assert rec is not None
    assert rec.path == str(src)
    assert rec.role == ROLE_BASELINE
    assert rec.branch == "v20-stable"
    assert rec.index_name == "bluez-v20"  # 未显式 codebase → 同 name


def test_register_upsert_keeps_unmentioned_fields(reg, tmp_path):
    """改 role/path 不丢 url/branch(register 是 upsert:None = 保留现值)。"""
    reg.register("wpa", path=str(tmp_path), url="https://x/wpa.git", branch="main")
    reg.register("wpa", path=str(tmp_path), role=ROLE_EPHEMERAL)  # 只改 role/path
    rec = reg.get("wpa")
    assert rec.role == ROLE_EPHEMERAL
    assert rec.url == "https://x/wpa.git"  # 未传 → 保留
    assert rec.branch == "main"


def test_yaml_is_human_readable_and_reloadable(reg, tmp_path):
    """落盘是人可读 YAML(手改即真相),重开 RepoRegistry 能读回;未知字段被忽略不炸。"""
    reg.register("bluez", path=str(tmp_path))
    p = tmp_path / "repos.yaml"
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert "bluez" in data["repos"]
    assert data["repos"]["bluez"]["role"] == "unmanaged"
    p.write_text(yaml.safe_dump({"repos": {"bluez": {"path": str(tmp_path), "future_field": 1}}}), encoding="utf-8")
    assert RepoRegistry(p).get("bluez").path == str(tmp_path)  # future_field 被忽略


def test_bad_role_rejected(reg):
    with pytest.raises(ValueError, match="role"):
        reg.register("x", role="important")


def test_remove_and_list(reg, tmp_path):
    reg.register("b", path=str(tmp_path), role=ROLE_BASELINE)
    reg.register("a", path=str(tmp_path), role=ROLE_EPHEMERAL)
    assert [r.name for r in reg.list()] == ["b", "a"]  # 排序:baseline 先于 ephemeral
    assert reg.remove("b") is not None
    assert reg.get("b") is None
    assert reg.remove("b") is None  # 再删 → None,不炸


# ── 模糊查找(agent 侧「bluez 5.50.61」→ 候选仓)──────────────────────────────


def test_find_by_project_and_version(reg, tmp_path):
    reg.register("bluez", path=str(tmp_path), role=ROLE_BASELINE)
    reg.register("bluez-v20", path=str(tmp_path), role=ROLE_BASELINE, branch="v20")
    reg.register("bluez-v20-5.50.61", path=str(tmp_path), role=ROLE_EPHEMERAL, branch="5.50.61")
    reg.register("wpa", path=str(tmp_path), role=ROLE_BASELINE)

    hits = reg.find("bluez", "5.50.61")
    assert [r.name for r in hits] == ["bluez-v20-5.50.61"]  # 版本号串精确收窄
    hits = reg.find("bluez")
    assert {r.name for r in hits} == {"bluez", "bluez-v20", "bluez-v20-5.50.61"}
    # baseline 优先排序(baseline 想要,ephemeral 排后)
    assert hits[0].role == ROLE_BASELINE
    assert reg.find("wpa") and not reg.find("systemd")


# ── 名字 → 路径反查链 ─────────────────────────────────────────────────────────


def test_resolve_prefers_registry_over_others(tmp_path):
    """注册表命中优先于 clone_dir/index 清单(它是人显式声明的真相)。"""
    reg_dir = tmp_path / "idx_root" / "code_index" / "wpa"
    reg_dir.mkdir(parents=True)
    (reg_dir / "index_manifest.json").write_text('{"repo_path": "/nonexistent/old"}', encoding="utf-8")
    (tmp_path / "idx_root" / "repos" / "wpa").mkdir(parents=True)
    declared = tmp_path / "declared" / "wpa"
    declared.mkdir(parents=True)

    reg = RepoRegistry(tmp_path / "repos.yaml")
    reg.register("wpa", path=str(declared))
    p, source = resolve_repo_path(
        "wpa", registry=reg,
        code_index_dir=tmp_path / "idx_root" / "code_index",
        clone_dir=tmp_path / "idx_root" / "repos",
    )
    assert p == declared.resolve()
    assert source == "registry[unmanaged]"


def test_resolve_falls_back_to_manifest_and_clone_dir(tmp_path):
    """注册表没有时:索引清单 repo_path(F1 起记录)→ data/repos 老落点,逐级兜底。"""
    idx_root = tmp_path / "code_index"
    src = tmp_path / "real_src"
    src.mkdir()
    (idx_root / "bluez" ).mkdir(parents=True)
    (idx_root / "bluez" / "index_manifest.json").write_text(
        f'{{"repo_commit": null, "repo_path": "{src}"}}', encoding="utf-8")
    reg = RepoRegistry(tmp_path / "repos.yaml")  # 空注册表
    p, source = resolve_repo_path("bluez", registry=reg, code_index_dir=idx_root)
    assert p == src.resolve() and source == "index-manifest"

    clone_dir = tmp_path / "repos"
    (clone_dir / "wpa").mkdir(parents=True)
    p, source = resolve_repo_path("wpa", registry=reg, clone_dir=clone_dir)
    assert p == (clone_dir / "wpa").resolve() and source == "clone-dir"


def test_resolve_local_path_and_miss(tmp_path):
    d = tmp_path / "dir"
    d.mkdir()
    reg = RepoRegistry(tmp_path / "repos.yaml")
    p, source = resolve_repo_path(str(d), registry=reg)
    assert p == d.resolve() and source == "local-path"
    p, source = resolve_repo_path("nope", registry=reg)
    assert p is None and "未注册" in source


def test_ensure_repo_registers_after_clone(tmp_path, monkeypatch):
    """ensure_repo clone 成功 → 顺手登记注册表(下次秒出,repo ls 可见)。"""
    from rootrecall.platform.config import AppConfig, PatchConfig, PatchGitConfig
    from rootrecall.services.repos.resolver import ensure_repo

    src = tmp_path / "remote"
    src.mkdir()
    subprocess.run(["git", "-C", str(src), "init", "-q"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(src), "config", "user.email", "t@t"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(src), "config", "user.name", "t"], check=True, capture_output=True)
    (src / "a.c").write_text("int a;\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(src), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(src), "commit", "-qm", "init"], check=True, capture_output=True)

    reg_file = tmp_path / "repos.yaml"
    monkeypatch.setenv("ROOTRECALL_REPOS_FILE", str(reg_file))
    cfg = AppConfig(patch=PatchConfig(git=PatchGitConfig(
        clone_dir=str(tmp_path / "data/repos"), remotes={"myrepo": str(src)})))

    _, cloned = ensure_repo("myrepo", cfg=cfg)
    assert cloned is True
    rec = RepoRegistry(reg_file).get("myrepo")
    assert rec is not None and rec.role == "unmanaged" and rec.url == str(src)

    # 第二次:走注册表命中(不重 clone),仍是幂等。
    _, cloned2 = ensure_repo("myrepo", cfg=cfg)
    assert cloned2 is False
