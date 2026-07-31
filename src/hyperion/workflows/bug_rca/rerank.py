# src/hyperion/workflows/bug_rca/rerank.py
"""majority_vote 原语(对标 Agentless majority_voting)—— R3.1 #54-rework 后定位。

面向小白:这套「采 N 个 → 归一化 → 投票 → 票多/首现/简洁度挑 top-1」的原语本身没错,错的是
**用错了地方**。它有效的条件(二选一):① 有廉价可靠 oracle(测试/repro);② 样本多样且能归一化
到同一规范解(self-consistency)。bug-RCA 的 patch 选择两条都不沾(无测试 + C 补丁形态发散 +
glm-5.2 近确定性 → N 样本雷同 → 投票平凡),所以 bug-RCA 主路径已改成「迭代 verify-refine(B)」,
本原语在 bug-RCA 里**降级为兜底(默认关,见 config delegate.rerank)**——仅当 repair loop 跑满
max_repair_loops 仍未过、且 delegate.rerank.enabled=true 时,fan-out 几个独立样本做最后兜底。

它真正的用武之地(配置/路线):
  A. localize 漏斗 file/function 选择投票(R3.1 方案A):文件名归一化平凡、无需 oracle。
  B. 深度调研多视角生成 + 事实一致性合并(R3.2):N 条轨迹,事实出现频次作置信度。
  C. 有 oracle 的 patch rerank(R5 / 有测试套件模块):filter+vote 才有效。
三处复用本文件 majority_vote 的「Counter + 票数/首现/简洁度」模式,换归一化函数即可。

依据:Agentless 32%/$0.70;1→42 sample + 测试过滤 +4pp(无测试时退化)。METR:test-pass≠对。
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass


@dataclass
class Candidate:
    """一个候选补丁 + 其 Tier 0 验证结果。"""

    patch: str  # 观察出的 unified diff(git diff)
    verified: bool  # Tier 0 apply --check 过没
    method: str = ""  # apply 路径(strict/3way/patch/empty/failed)
    sample_id: int = 0  # 第几个样本(0-based;首现序 tie-break 用)


def normalize_patch(patch: str) -> str:
    """归一化补丁文本 → majority vote 的 key(同修法、不同空白/行号 → 同 key)。

    Agentless 用 Python ast.parse/unparse 归一(C 仓不适用)→ 改文本归一:
    ① 去行尾空白;② @@ hunk 行号偏移归一(只留 @@ 标记,同修法不同 context 行号应归一);
    ③ 合并连续空行。目标是让「本质同一修法、只差空白/行号偏移」的补丁归一到同一 key。
    """
    if not patch or not patch.strip():
        return ""
    out = []
    for line in patch.splitlines():
        if line.startswith("@@"):
            line = "@@"  # 行号偏移不计(同修法不同 context 行号应归一)
        out.append(line.rstrip())
    norm = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", norm)  # 合并连续空行


def modified_length(patch: str) -> int:
    """补丁改动行数(+/- 行,去 ---/+++ 头)—— 简洁度 tie-break(改动少优先)。

    对标 Agentless rerank.py:139 modified_length。
    """
    n = 0
    for line in patch.splitlines():
        if len(line) > 3 and (line.startswith("---") or line.startswith("+++")):
            continue
        if line.startswith("+") or line.startswith("-"):
            n += 1
    return n or 1  # 防 0(空补丁)


def majority_vote(candidates: list[Candidate]) -> tuple[str, Candidate | None, dict]:
    """多候选 majority voting(无测试路径)→ 选 top-1。

    流程(对标 Agentless rerank.py:256-269 无测试 fallback):
      1. 过滤:只留 verified(Tier 0 过);都没过 → 退到全部(记降级)。
      2. 归一:normalize_patch → key。
      3. 投票:Counter(key)。
      4. 排序:票数 desc → 首现序 asc → 简洁度 asc(改动少优)。
    返回 (best_patch, best_candidate, summary),summary 含票数/降级/METR 警示(供 report)。
    """
    if not candidates:
        return "", None, {"reason": "no candidates"}
    # 1. 过滤到 verified
    pool = [c for c in candidates if c.verified]
    fallback = False
    if not pool:
        pool = candidates  # 都没过 apply → 全部(记降级)
        fallback = True
    # 2-3. 归一 + 计票
    keys = [normalize_patch(c.patch) for c in pool]
    votes = Counter(keys)
    # 4. 排序:票多 / 首现早 / 改动少(都用 max → 后两个取负让「小」变成「大」)
    first_appear = {}
    for i, k in enumerate(keys):
        if k not in first_appear:
            first_appear[k] = i
    best_idx = max(
        range(len(pool)),
        key=lambda i: (
            votes[keys[i]],  # 票数多优先
            -first_appear[keys[i]],  # 首现早(=idx 小)优先:取负后小的变大的
            -modified_length(keys[i]),  # 改动少(=length 小)优先:取负后小的变大的
        ),
    )
    best = pool[best_idx]
    summary = {
        "reason": "fallback-no-verified" if fallback else "majority",
        "n_candidates": len(candidates),
        "n_verified": sum(1 for c in candidates if c.verified),
        "winner_votes": votes[keys[best_idx]],
        "metr_caveat": (
            "test-pass ≠ correct(约半数 SWE-bench PR 不会被合);多候选投票是必要不充分,"
            "需人工 / LLM Selector 终审(METR 2026-03-10)"
        ),
    }
    return best.patch, best, summary
