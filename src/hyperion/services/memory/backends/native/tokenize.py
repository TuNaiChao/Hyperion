"""CJK 分词(Phase 3):让 FTS5 BM25 路能检索中文记忆。

背景:FTS5 的 unicode61 tokenizer 只按空格/标点切词,中文没空格 → 整段汉字被当成
一个 token,"扫描" 匹配不上 "阻塞所有站点扫描"。此前纯中文查询只能靠向量路补语义
(见 store.py _fts_query 的注释),BM25 路对 domain_knowledge(中文协议知识)基本失明。

方案(2026 业界对照后的取舍):
  - trigram(FTS5 内置):零依赖但要 ≥3 字查询,而中文技术词大量是 2 字(溢出/死锁/
    竞态/越界)→ 不合身。
  - ICU tokenizer:多数 Python sqlite3 构建没编译进去 → 不可靠。
  - jieba Python 侧分词:不加 SQLite C 扩展,索引侧+查询侧用同一分词器,分出的词
    按空格连回 → unicode61 按空格切正好一词一 token。选它。

架构约束:原 FTS 是 external-content + SQL 触发器同步,触发器在 SQL 层调不了 Python
分词 → 本模块启用后 FTS 表改 standalone(contentless 同构:文本列自存,upsert() 在
Python 里维护,见 store.py 的 _fts_sync)。文本列(summary/detail/root_cause)只有
upsert 一处写入 → 维护点唯一,不会漏同步。

降级:jieba 没装(可选依赖)→ _segment 原样返回,FTS 退回 unicode61 行为(英文/
混合查询不受影响;纯中文回到"靠向量路"的现状)。记忆是核心,分词是增强,绝不崩。
"""

from __future__ import annotations

import re

# 惰性加载 jieba(首次 ~0.5s 建词典,之后缓存);没装 → None → 原样返回。
_jieba = None
_jieba_tried = False

# CJK 统一表意文字 + 扩展 A + 兼容表意。够用:中文记忆正文都在这几个区段。
_CJK_RE = re.compile(r"[一-鿿㐀-䶿\f900-﫿]+")

# 过滤停用词(的/了/是/在…):BM25 里它们 IDF≈0 只添噪音,还把 FTS 索引撑大。
# 只列最常见的十几粒——jieba cut_for_search 本身不滤,这里做最小集,不做完备 NLP 停用词表(YAGNI)。
_STOPWORDS = {"的", "了", "是", "在", "和", "与", "也", "有", "就", "不", "为", "这", "那", "对", "被", "把", "让", "从", "到", "会", "能"}


def _load_jieba():
    global _jieba, _jieba_tried
    if _jieba_tried:
        return _jieba
    _jieba_tried = True
    try:
        import jieba  # noqa: PLC0415 - 可选依赖,运行时才知道装没装

        _jieba = jieba
    except Exception:  # noqa: BLE001 - 没装/词典坏 → 降级 unicode61 行为,不崩
        _jieba = None
    return _jieba


def segment(text: str) -> str:
    """中文段 jieba 分词、英文段原样,空格连回(unicode61 按空格切 = 一词一 token)。

    只切 CJK 段:英文标识符(sdp_extract_seqtype)、路径、补丁文本不进 jieba
    (切了反而碎:_/_/_ 反而毁 FTS 匹配)。停用词滤掉。
    jieba 未装 → 原样返回(降级)。
    """
    jb = _load_jieba()
    if jb is None or not text:
        return text

    def _cut(m: re.Match[str]) -> str:
        # 中文段两边补空格:与前后英文/标点隔开("传输,扫描"这类贴着半角标点的 CJK 段,
        # 切完的词才不会和标点粘成一个 token,unicode61 空格切词才准)。
        return " " + " ".join(
            w for w in jb.cut_for_search(m.group(0))
            if w.strip() and w not in _STOPWORDS
        ) + " "

    return _CJK_RE.sub(_cut, text)


def jieba_available() -> bool:
    """jieba 是否可用(测试/诊断用;不触发加载)。"""
    return _load_jieba() is not None
