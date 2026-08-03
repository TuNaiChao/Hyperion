"""触发解析服务(瘦版,#53):把 bug 报告 / issue 文档(.md/.txt/.pdf)读成纯文本 +
抽一组检索关键字;并附一个超薄的日志关键字过滤(原 log_preprocess #50 砍剩下的有用切片)。

这一层干什么(面向小白)
------------------------
bug-RCA 的入口原来要人手敲一段 `--trigger`(bug 线索)。但真实输入常常是一个 issue 文档
(demo1 就是个 PDF 漏洞通报)。这层负责:
  1. 读文档 → 纯文本(PDF 用 pypdf,已在 uv.lock,无需新依赖;md/txt 直接读);
  2. 从文本里抽"检索关键字"(panic/OOPS/错误码/函数名/路径/地址)—— 喂 code_index 检索
     预筛(方案A:retrieve 把整库文件树缩成 top-N 候选);
  3. (顺带)按关键字把一大坨日志过滤成精华行 —— 原 log_preprocess(#50)砍到只剩这一刀
     (addr2line/stack-fold 对当前 demo 无 crash 栈,是 theater,defer;详见 log_filter.py)。

设计取舍(YAGNI,2026-07-31 三路调研定)
---------------------------------------
- **不做"重 NER 关键字抽取模块"**:现代 LLM 自己会从文本抽检索目标(OpenHands/SWE-agent
  无显式 NER 阶段);这里只做**薄 regex 抽**(panic 签名 / 0x 地址 / 路径 / C 标识符),
  够给 retrieve() 一个比散文更聚焦的 query 即可。
- **addr2line / stack-fold 不做**:前提(debug 符号 + 出地址的 binary)当前 demo 都不满足
  (demo2 是逻辑竞态 panic=0,demo1 没日志);且 v2 已把 log_symbolizer 显式裁给 opencode。
  要符号化时由 delegate(opencode)在沙箱里 addr2line(它有 shell)。speculative 的"LLM 折叠成
  结构化信号"也不做(2026 无验证管线)。
"""

from hyperion.services.trigger_parser.log_filter import filter_log_window
from hyperion.services.trigger_parser.parser import IssueDoc, extract_keywords, parse_issue

__all__ = ["IssueDoc", "parse_issue", "extract_keywords", "filter_log_window"]
