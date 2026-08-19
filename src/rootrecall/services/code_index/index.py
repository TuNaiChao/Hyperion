"""代码理解服务 · 第五步:索引编排(P1.3 index.py)。

这一层干什么
------------
把前四层串成「建索引」这条离线数据流(架构 §1 数据流 ①):
  walk 仓库 → parser 解析 → chunker 切块 → embedder 嵌入 → store 存库。
检索(retrieval.py)和导航(tools)都建立在它产出的向量库上。

两个生产级关注(设计 §10)
--------------------------
1. **原子性**:LanceDB 无事务。全量重建先写 temp 目录(`lancedb_tmp`),全部成功后原子
   swap 成正式目录(`lancedb`)——中途崩(磁盘满/进程杀)不留半成品,且带崩溃恢复。
   增量靠 store.merge_insert 的条件 upsert(批级原子,content_hash 不变跳过)。
2. **增量 + 状态清单**:`index_manifest.json` 存 {repo_commit, model_fingerprint,
   schema_version, file_manifest{相对路径:sha256}}。
   - model_fingerprint 或 schema_version 变 → 全量重建(向量空间/结构变了)。
   - 文件 sha256 没变 → 它的所有 chunk 跳过不重嵌(parse 快、embed 慢,短路在 embed 层)。

还没做(P1.3 范围外,记 backlog)
--------------------------------
- git diff 加速变化文件定位(现 walk+sha256 对账,够用;大仓再上 `git diff --name-only`)。
- ~~删除文件清理~~ ✅ 已做(2026-08-18):增量路径 delete_by_file 清「消失文件 + 重嵌文件」旧行(换 id 的符号不再留幽灵行)。
- 并行 parse(ProcessPoolExecutor)+ N 跳依赖追踪(借 CRG,P1.5/P6)。
- temp-swap 现有微秒级窗口,常驻服务时升级为无窗口(§14.4 触发器)。

对外提供
--------
- build_index(...):建/更新一个仓库的索引,返回统计 dict。
- IndexManifest / SCHEMA_VERSION:清单结构与 chunk schema 版本。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from rootrecall.services.code_index.chunker import CodeChunk, chunk_repo
from rootrecall.services.code_index.embed import Embedder
from rootrecall.services.code_index.parser import iter_source_files
from rootrecall.services.code_index.store import LanceDBStore

logger = logging.getLogger(__name__)

SCHEMA_VERSION: int = 1  # chunk schema 版本;改 chunker.CodeChunk 结构时 bump → 触发全量重建
_LANCEDB = "lancedb"
_LANCEDB_TMP = "lancedb_tmp"
_LANCEDB_BAK = "lancedb_bak"


# ──────────────────────────────────────────────────────────────────────────
# §1 索引清单(Manifest)
# ──────────────────────────────────────────────────────────────────────────


@dataclass
class IndexManifest:
    """索引清单(随 LanceDB 目录放一个 sidecar json;检索前校验 + 增量对账)。

    - repo_commit:建库时仓库锁定的 commit(评测基线 / staleness 判定)。
    - model_fingerprint:embedder.fingerprint;变 → 全量重建(向量空间变了)。
    - schema_version:chunk schema 版本;变 → 全量重建。
    - file_manifest:{相对路径: sha256} —— 增量对账(哪份文件变了要重嵌)。
    - repo_path:建库时源码仓的绝对路径(repo registry 反查用;旧清单没有 → None)。
      索引名→仓库路径从此可反查(resolve_repo_path),agent 不再问用户要路径。
    """

    repo_commit: str | None
    model_fingerprint: str
    schema_version: int
    file_manifest: dict[str, str] = field(default_factory=dict)
    repo_path: str | None = None


def _manifest_path(base_dir: Path, repo: str) -> Path:
    return base_dir / repo / "index_manifest.json"


def _read_manifest(path: Path) -> IndexManifest | None:
    """读清单;不存在或损坏返回 None(触发全量重建)。"""
    if not path.exists():
        return None
    try:
        return IndexManifest(**json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning("manifest 损坏(%s),视为全量重建", e)
        return None


def _write_manifest(path: Path, mf: IndexManifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(mf), ensure_ascii=False, indent=2), encoding="utf-8")


# ──────────────────────────────────────────────────────────────────────────
# §2 辅助:git commit / 文件 sha256 / embed+upsert
# ──────────────────────────────────────────────────────────────────────────


def _git_head(repo_path: Path) -> str | None:
    """拿仓库当前 HEAD commit sha(评测基线/staleness 用);非 git 仓库或失败返回 None。"""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True, timeout=10,
        )
        return out.stdout.strip() or None
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _file_manifest(repo_path: Path) -> dict[str, str]:
    """遍历所有源码文件 → {相对路径: sha256}(增量对账依据)。"""
    fm: dict[str, str] = {}
    for p, rel, _lang in iter_source_files(repo_path):
        try:
            fm[rel] = _sha256(p.read_bytes())
        except OSError:
            continue
    return fm


def _embed_and_upsert(
    store: LanceDBStore, repo: str, chunks: list[CodeChunk], embedder: Embedder, batch_size: int
) -> int:
    """分批 embed + upsert,返回处理 chunk 数。

    batch_size 是「内存/upsert 节奏」的分批;embedder.embed_chunks 内部还会按 batch_limit
    分批调远端 API(见 embed.py),所以这里不用管 API 条数上限。
    """
    done = 0
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        vecs = embedder.embed_chunks(batch)
        store.upsert(repo, batch, vecs)
        done += len(batch)
        logger.info("  embedded %d/%d chunks", done, len(chunks))
    return done


# ──────────────────────────────────────────────────────────────────────────
# §3 对外:build_index
# ──────────────────────────────────────────────────────────────────────────


def build_index(
    repo_path: Path | str,
    repo_name: str,
    embedder: Embedder,
    base_dir: Path | str = "data/code_index",
    *,
    force: bool = False,
    batch_size: int = 64,
) -> dict:
    """建/更新 repo_name 的向量索引。

    - force=True / 首次 / model_fingerprint 变 / schema_version 变 → 全量重建(temp 目录原子 swap)。
    - 否则增量:只重嵌 sha256 变了的那批文件的 chunk(merge_insert 条件 upsert)。

    返回 {mode, indexed, total_chunks, repo_commit}(增量额外有 changed_files)。
    """
    repo_path = Path(repo_path).resolve()
    base_dir = Path(base_dir)
    fp = embedder.fingerprint
    old = _read_manifest(_manifest_path(base_dir, repo_name))

    # 模型指纹 / schema 变 → 强制全量(向量空间或结构变了,必须重建)
    if old is not None and (old.model_fingerprint != fp or old.schema_version != SCHEMA_VERSION):
        logger.info("model_fingerprint 或 schema_version 变 → 全量重建")
        force = True

    if force or old is None:
        return _full_rebuild(repo_path, repo_name, embedder, base_dir, fp, batch_size)
    return _incremental(repo_path, repo_name, embedder, base_dir, old, fp, batch_size)


def _full_rebuild(
    repo_path: Path, repo_name: str, embedder: Embedder, base_dir: Path, fp: str, batch_size: int
) -> dict:
    """全量重建:写 temp 目录 → 原子 swap → 写 manifest。"""
    repo_dir = base_dir / repo_name
    final_dir = repo_dir / _LANCEDB
    tmp_dir = repo_dir / _LANCEDB_TMP
    bak_dir = repo_dir / _LANCEDB_BAK

    # 崩溃恢复 + 清残留:
    #   - final 缺失但 bak 在 → 上次 swap 在 final→bak 后断了,恢复旧索引
    #   - tmp 在(构建中断)/ bak 在且 final 也在(swap 后没删 bak)→ 清掉
    if not final_dir.exists() and bak_dir.exists():
        logger.warning("检测到上次 swap 中断(final 缺失),恢复旧索引 bak→final")
        os.replace(bak_dir, final_dir)
    for d in (tmp_dir, bak_dir):
        if d.exists():
            shutil.rmtree(d)

    logger.info("[%s] 全量重建:切块...", repo_name)
    chunks = chunk_repo(repo_path)
    logger.info("[%s] 切出 %d chunk,嵌入(写 temp)...", repo_name, len(chunks))

    tmp_store = LanceDBStore(base_dir, db_name=_LANCEDB_TMP)
    n = _embed_and_upsert(tmp_store, repo_name, chunks, embedder, batch_size)
    tmp_store.optimize(repo_name)
    tmp_store._tables.clear()  # 释放 temp 表句柄,腾出目录好 rename(白盒:同包内访问私有缓存)

    # 空仓库(0 chunk)时 LanceDB 没建表 → tmp 目录不存在;兜底建空目录,swap 后即为空索引
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # 原子 swap:final→bak,tmp→final,删 bak。两步保证每个 rename 的目标都不存在(POSIX 要求目录目标为空/不存在)。
    if final_dir.exists():
        os.replace(final_dir, bak_dir)
    os.replace(tmp_dir, final_dir)
    if bak_dir.exists():
        shutil.rmtree(bak_dir)

    commit = _git_head(repo_path)
    _write_manifest(
        _manifest_path(base_dir, repo_name),
        IndexManifest(commit, fp, SCHEMA_VERSION, _file_manifest(repo_path), repo_path=str(repo_path)),
    )
    logger.info("[%s] 全量重建完成: %d chunks @ %s", repo_name, n, commit or "no-git")
    return {"mode": "full", "indexed": n, "total_chunks": n, "repo_commit": commit}


def _incremental(
    repo_path: Path,
    repo_name: str,
    embedder: Embedder,
    base_dir: Path,
    old: IndexManifest,
    fp: str,
    batch_size: int,
) -> dict:
    """增量:parse 全部(快)→ 只 embed sha256 变了的那批文件的 chunk → merge_insert 条件 upsert。

    重嵌前先 delete_by_file 清掉这些文件(以及已从仓库消失的文件)的旧行:chunk id 是
    「file:限定名」,符号改名/挪作用域会换 id,只靠 merge_insert 匹配不上旧行,会留
    内容重复的幽灵行;消失文件的 chunk 更是要主动清。
    """
    logger.info("[%s] 增量:切块 + 对账...", repo_name)
    chunks = chunk_repo(repo_path)  # parse 快;embed 慢——下面按文件 sha256 短路
    new_fm = _file_manifest(repo_path)
    changed_files = {rel for rel, h in new_fm.items() if old.file_manifest.get(rel) != h}
    removed_files = set(old.file_manifest) - set(new_fm)
    to_embed = [c for c in chunks if c.file in changed_files]
    logger.info(
        "[%s] %d/%d 文件变化,%d/%d chunk 待重嵌",
        repo_name, len(changed_files), len(new_fm), len(to_embed), len(chunks),
    )

    store = LanceDBStore(base_dir, db_name=_LANCEDB)
    if changed_files or removed_files:
        store.delete_by_file(repo_name, changed_files | removed_files)
    n = _embed_and_upsert(store, repo_name, to_embed, embedder, batch_size) if to_embed else 0
    if to_embed:
        store.optimize(repo_name)

    commit = _git_head(repo_path)
    _write_manifest(_manifest_path(base_dir, repo_name), IndexManifest(commit, fp, SCHEMA_VERSION, new_fm, repo_path=str(repo_path)))
    logger.info("[%s] 增量完成: 重嵌 %d chunks @ %s", repo_name, n, commit or "no-git")
    return {
        "mode": "incremental",
        "indexed": n,
        "total_chunks": len(chunks),
        "changed_files": len(changed_files),
        "repo_commit": commit,
    }
