"""超薄日志过滤(原 log_preprocess #50 砍剩下的有用切片):关键字 ∩ 时间窗。

为什么只留这一刀(2026-07-31 调研 + YAGNI)
-----------------------------------------
- demo2 的 journalctl 1.6 万行,真信号就 ~26 行(radio work 生命周期);不预筛会把 delegate
  上下文撑爆(且 opencode grep 一大坨也慢、费 token)。
- 光按关键字 grep 会撞 400 行上限、还可能漏掉真正的故障窗口(同样的 radio work 活动日志里
  到处都有,前 400 行未必是出 bug 那段)→ 所以**关键字 ∩ 时间窗**一起用才外科手术。
- **addr2line / stack-fold 不做**:demo2 是逻辑竞态(`panic=0 / Call Trace=0`),"地址"全是堆
  指针 / nl80211 flag,没东西可符号化;demo1 干脆没日志。前提(debug 符号 + 出地址的 binary)
  当前 demo 都不满足,且 v2 已把 log_symbolizer 显式裁给 opencode。真有 crash 栈时让 delegate
  在沙箱里 addr2line(它有 shell),不在 Hyperion 侧重建符号化。
- **LLM"折叠成结构化信号"不做**:2026 无验证管线支持(speculative),记 backlog。

所以这层就一句:**关键字 ∩ 时间窗 grep + 封顶行数** → 返回精华行给 delegate context。
跨天 / dmesg uptime 秒的时间解析要 dateutil + boot time,先不做(记 backlog)。
"""

from __future__ import annotations

import re

# 时间戳里的 HH:MM:SS 部分(journalctl "Jul 30 10:12:11" / ISO "2026-07-30T10:12:11" 都含)
_TOD = re.compile(r"\b(\d{2}:\d{2}:\d{2})\b")


def filter_log_window(
    log_text: str,
    keywords: list[str] | None = None,
    *,
    since: str | None = None,
    until: str | None = None,
    max_lines: int = 400,
) -> str:
    """按「关键字 ∩ 时间窗」把大日志过滤成精华行,封顶 max_lines 行。

    一行被保留当且仅当:过了时间窗(没给 since/until 就算过)**且**过了关键字(没给 keywords
    就算过)。即:都给 → AND(窗口内的关键字行,最外科手术,~26 行级别);只给一个 → 那一个;
    都不给 → 前 max_lines 行(不误删语义,但也不无限塞)。

    时间窗用 **HH:MM:SS 字符串**比较(同一天内,够 demo2 的 36 秒窗用):
      - 从每行时间戳抽 HH:MM:SS(journalctl / ISO 都含这个子串);抽不到(如 dmesg "[1234.567]"
        是 uptime 秒,跨天要 boot time)→ 该行不算"在窗内"(只靠关键字留)。
      - since/until 也当 HH:MM:SS(用户从 issue 里读出故障窗口填 --since/--until)。
    同格式同天的 HH:MM:SS 字典序比较即正确;跨天 / 多格式正经解析记 backlog。

    ⚠️ 关键字轴对**日志**常不可靠(2026-07-31 实测 demo2):issue 抽的关键字是**代码符号**
    (scan_res_handler / p2p_scan_work),而日志里是**运行时散文形**(radio work 'p2p-scan' /
    radio_work_free)—— 子串不匹配,AND 出来 287 行噪声、0 行真信号。所以**默认接线对日志只传
    时间窗、不传 issue 关键字**(issue 关键字喂给 code 检索方案A,那里符号 ↔ 符号才对得上);
    keywords 参数留给"已知是日志词汇"的场景或用户显式 --log-keywords。
    """
    if not log_text:
        return ""
    lines = log_text.splitlines()
    kws = [k for k in (keywords or []) if k]
    kws_lower = [k.lower() for k in kws]
    has_window = bool(since or until)
    if not kws and not has_window:
        return "\n".join(lines[:max_lines])

    kept: list[str] = []
    for line in lines:
        if kws_lower:  # 有关键字:必须命中其一,否则丢弃
            low = line.lower()
            if not any(k in low for k in kws_lower):
                continue
        if has_window:  # 有窗口:必须在窗内,否则丢弃
            tod = _extract_tod(line)
            if tod is None or not _in_window(tod, since, until):
                continue
        kept.append(line)
        if len(kept) >= max_lines:
            break
    return "\n".join(kept)


def _extract_tod(line: str) -> str | None:
    """从日志行抽 HH:MM:SS(时间戳的时分秒部分);抽不到 → None。"""
    m = _TOD.search(line)
    return m.group(1) if m else None


def _in_window(tod: str, since: str | None, until: str | None) -> bool:
    """HH:MM:SS 字符串窗口比较(同格式同天,朴素字典序即正确)。"""
    if since and tod < since:
        return False
    if until and tod > until:
        return False
    return True
