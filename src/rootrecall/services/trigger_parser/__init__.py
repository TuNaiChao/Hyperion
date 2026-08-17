"""触发解析服务(瘦版,#53):issue 文档 → 纯文本。

这一层干什么(面向小白)
------------------------
bug-RCA 的入口原来要人手敲一段 `--trigger`(bug 线索)。但真实输入常常是一个 issue 文档
(demo1 就是个 PDF 漏洞通报)。这层负责:
  1. `parse_issue` 读文档(.md/.txt/.pdf)→ 纯文本(PDF 用 pypdf,已在 uv.lock)。

设计取舍(YAGNI,2026-07-31 三路调研定 + 2026-08-10 复核)
---------------------------------------
- **不做 regex 关键字抽取**(踩坑 #2):代码符号 ≠ 日志散文形,regex 抽不准;现代 LLM 自己会
  从文本抽检索目标(OpenHands/SWE-agent 无显式 NER 阶段)。要抽时由 delegate/opencode 自己搞,
  或经 rootrecall_search_codebase 工具(emit-concept 防幻觉)。
- **addr2line / stack-fold 不做**:前提(debug 符号 + 出地址的 binary)当前 demo 都不满足
  (demo2 是逻辑竞态 panic=0,demo1 没日志);且 v2 已把 log_symbolizer 显式裁给 opencode。
  要符号化时由 delegate 在沙箱里 addr2line(它有 shell)。speculative 的"LLM 折叠成结构化信号"
  也不做(2026 无验证管线)。
- **日志时间窗过滤(filter_log_window)2026-08-10 撤**:调研(deer-flow/omp 双证)专门日志切片
  工具没必要 —— opencode 的 read/grep/awk 能做等价切片且更灵活(踩坑#2);领域知识(从更早切、
  用日志词汇、窗口可能漏根因)转进 bug-rca SKILL/prompt。原 log_filter.py 删除。
"""

from rootrecall.services.trigger_parser.parser import IssueDoc, parse_issue

__all__ = ["IssueDoc", "parse_issue"]
