---
name: backlog-production-grade
description: "最小实现 → 对齐 deer-flow 生产级" 的补齐待办清单(跨阶段)
metadata:
  type: project
---

记录所有"当前为最小实现、后续须对齐 deer-flow 补到生产级"的点位。每条标注:位置、deer-flow 参照、目标阶段。原则见 [[align-to-deerflow-production-grade]]。

**当前清单:**

1. **`grep` / `glob`(LocalSandbox)** — `src/hyperion/platform/sandbox/local.py`。
   - 现状:字面子串 grep、`rglob` 无忽略清单、返回 `list`、无二进制/大文件/ReDoS/符号链接守卫。
   - 对齐:移植 deer-flow [search.py](deer-flow/backend/packages/harness/deerflow/sandbox/search.py) 整文件(`find_glob_matches` / `find_grep_matches` / `GrepMatch` + `IGNORE_PATTERNS` + `should_ignore_name` + `is_binary_file` + ReDoS 行长守卫 + 符号链接 `is_relative_to` 守卫);`local.py` 改成薄包装;返回改 `tuple[list, bool]`(截断标志);grep 支持正则/literal/case/glob 过滤。
   - 目标阶段:**P1**(代码理解服务,搜索是主力)。P0 demo 不直接暴露 grep/glob 给 agent,故可延后。

2. **`str_replace` 大文件截断风险** — `src/hyperion/tools/sandbox.py:str_replace_tool`。
   - 现状:经 `read_file`(50000 字符截断)读整文再写回;>50KB 文件会被截断标记污染、丢数据。
   - 对齐:deer-flow 的 read-before-write hash 门(读全文 + 内容哈希校验 + 同路径串行锁 `sandbox/file_operation_lock.py`)。
   - 目标阶段:**P2**(Bug-RCA 真实改补丁,文件可能较大)。P0 workspace 文件小,暂可接受。

3. **agent 中间件链(生产级)** — `src/hyperion/platform/agent.py:build_middlewares`。
   - 现状:`create_agent(..., middleware=[])` 裸跑,无任何中间件护栏。
   - 对齐:deer-flow 的中间件链(`deer-flow/backend/.../agents/lead_agent/agent.py:build_middlewares`,~30 项)。Hyperion 需要的子集:`ToolErrorHandlingMiddleware`(工具异常转 ToolMessage 不崩)、`ToolOutputBudgetMiddleware`(工具输出进上下文前的预算)、`ReadBeforeWriteMiddleware`(写前哈希门,配合 backlog 第 2 条)、`LoopDetectionMiddleware`(重复工具调用循环检测)、`TokenBudgetMiddleware`(per-run token 上限)、`SummarizationMiddleware`(上下文压缩)。
   - 目标阶段:**P2**(Bug-RCA 多步长链路需要这些护栏)起逐步加,按需逐个挂。

(后续每发现一处最小实现就追加一条。)
