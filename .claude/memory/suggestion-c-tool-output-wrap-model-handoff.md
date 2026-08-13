---
name: suggestion-c-tool-output-wrap-model-handoff
description: 建议 C 落地:补 ToolOutputBudgetMiddleware.wrap_model_call 兜底历史漏网大消息(对齐 deer-flow)。校正 architecture-review 短板 2「不二次压缩」误判 —— synopsis 累积靠摘要(建议 B),不二次压。
metadata:
  type: project
---

2026-08-13 落地 architecture-review §五 建议 C(历史 ToolMessage 兜底)。

## 关键校正:原建议 C 表述错了

architecture-review §2.1 短板 2 原话「历史 ToolMessage 不二次压缩」,§五 建议 C 原做法写「SummarizationMiddleware 触发时顺手把已外置的旧 synopsis 再裁一遍」—— **这是基于误判**。本会话调研(Explore agent 遍历 deer-flow 生产实现)推翻:

1. **deer-flow 生产级也不二次压 synopsis**。synopsis(~3K 字符)≪ fallback_max_chars(20K),到不了任何截断阈值。synopsis 的累积完全靠 SummarizationMiddleware 在 token 阈值触发时把旧消息(含 ToolMessage)整体摘要掉 —— 而**这正是建议 B(token 触发 32K)已做的**。
2. **真实增量 = `wrap_model_call` 钩子**(deer-flow 的 ToolOutputBudgetMiddleware 有、Hyperion 原缺)。它对历史 ToolMessage **只做 `fallback_max_chars` 级 head+tail 兜底截断**(不压 synopsis)。
3. **wrap_model_call 的价值 = 兜底「漏网的大 ToolMessage」**:断点续跑(R3 核心特性)/ 改过阈值 / 从旧 checkpoint 恢复时,历史里可能混进未经 `wrap_tool_call` 处理的大消息。每轮扫一遍兜底。

**所以建议 C 从「二次压 synopsis」校正为「补 wrap_model_call 对齐 deer-flow 架构兜底 + 校正短板 2 表述」。** 不做激进 synopsis 二次压(YAGNI:deer-flow 没做、摘要已兜底、踩坑#2 别造摘要的等价物)。

## 改了啥

- **`_budget_content` / `_patch_tool_message` 加 `externalize: bool = True` 参数**(tool_output.py):历史路径(wrap_model_call 调)传 `False` → 跳过外化分支,只走 fallback head+tail 截断。现有 `wrap_tool_call` 调用不传(默认 True)→ 行为不变。
- **新 `_is_over_fallback(msg, config)`**:预扫描判据(非文本/豁免 → False;文本 → `len > fallback_max_chars`)。
- **新 `_patch_model_messages(messages, config)`**:抄 deer-flow `_patch_model_messages`(tool_output_budget_middleware.py:539-565)的**预扫描模式** —— `any()` 判无超阈值 → 直接返 None 不重建 list(99% 干净轮次零开销);有则逐条 `_patch_tool_message(externalize=False)`。
- **`ToolOutputBudgetMiddleware.wrap_model_call` / `awrap_model_call`**:抄 loop_detection.py:233-238 先例,`request.override(messages=patched)`(immutable API,langchain types.py:201)。中间件已在 factory 链里(factory.py:64),加方法即生效,**不改装配**。
- 顶部 docstring 第 13-14 行 TODO 更新:原「R3.0 只做新工具结果,历史推 R3.2」→ 补两条钩子说明(wrap_tool_call 主路径 + wrap_model_call 历史兜底,synopsis 累积靠摘要)。
- 3 单测:`test_wrap_model_call_truncates_oversized_history`(大消息截断 + 小 synopsis 不动)+ `test_wrap_model_call_skips_externalize_on_history`(不外化:无磁盘文件 + fallback 标记)+ `test_wrap_model_call_returns_none_when_clean`(干净历史返 None)。全 runtime 29 绿。

## 设计要点(防回退踩坑)

1. **历史路径 `externalize=False` 的理由**:历史里已有的 ToolMessage 要么是已外化的 synopsis(~3K,保留不动),要么是漏网大消息(只需截断)。重新外化会再写一份磁盘文件 + 生成第二份 synopsis,冗余且 synopsis 累积靠摘要中间件治理(建议 B)。deer-flow 历史路径也是 `outputs_path=None`。
2. **预扫描返 None 的价值**:`wrap_model_call` 每轮都跑,但 99% 轮次历史是干净的(工具结果已被 wrap_tool_call 处理成 synopsis)。`any(_is_over_fallback(...))` 先判,无超阈值直接返 None 不重建 list,零拷贝零开销。
3. **synopsis ~3K < fallback_max_chars 20K**:这是「不二次压 synopsis」的机制保证 —— `_is_over_fallback` 用 `len > fallback_max_chars` 判,synopsis 永远不到这条线。想压 synopsis 得靠摘要中间件整体吃(token 阈值),不是逐条截断。
4. **抄先例不抄复杂度**:loop_detection 的 `wrap_model_call` 就是 `request.override(messages=...)` + `handler(request)` 四行;deer-flow 的 `_patch_model_messages` 就是预扫描 + 逐条 patch。没引入新依赖、没改 factory、没碰 SummarizationMiddleware。

## 故意不做(YAGNI)

- **不二次压缩 synopsis** —— deer-flow 生产级也没做;synopsis ~3K < fallback_max_chars 20K;累积靠摘要(建议 B 32K 触发)。造 = 踩坑#2 + 重复摘要的活。
- **不设「历史 synopsis 单独小阈值」(激进路线)** —— 同上,deer-flow 评估过不值。
- **不在历史路径重新外化** —— 漏网大消息只需 fallback 截断;已外化的 synopsis 不重复处理。
- **不改 factory 装配** —— wrap_model_call 是中间件自带钩子,ToolOutputBudgetMiddleware 已在链里,加了方法就生效。

相关:[[suggestion-b-token-summarization-handoff]] 同批建议 B(摘要 token 触发,治 synopsis 累积);[[runtime-middleware-policy]] pull-by-need 加中间件;[[avoid-overengineering]] YAGNI 筛(原建议 C 表述被筛掉)。
