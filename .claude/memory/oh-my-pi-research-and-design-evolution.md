---
name: oh-my-pi-research-and-design-evolution
description: 2026-07-27 深读 oh-my-pi(omp)+2026 最佳实践后的后续设计报告位置与核心结论(三层代码智能栈)。
metadata: 
  node_type: memory
  type: project
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-07-27T08:08:33.434Z
---

> **⚠️ v2 更正(2026-07-28 产品重规划,优先以此为准):** 下文"三层代码智能栈"是**用户的心智模型**——深读 omp 源码确认 vector(L1)/LSP(L2)/DAP(L3)三件**都真实存在但并未被 omp 串成一条管线**,各自独立。Hyperion 把它们组合起来是**本项目原创设计**。且 omp **无专门的 bug-RCA 子系统**。**记忆:** v2 不直接用 omp mnemopi 作底座,而是自建 `MemoryService` 契约(deer-flow MemoryManager ABC + omp backend-swap 形状),v1 后端组合已有 code_index + code-review-graph,**只借鉴 mnemopi 的巩固/衰减/veracity 设计**;omp 同时是 bug-RCA 的**委托目标**(默认 `omp -p`)。详见 [[agent-project-overview]] + [memory-design.md](../../docs/设计/memory-design.md) + [bug-rca-design.md](../../docs/设计/bug-rca-design.md)。

2026-07-27 深读了 can1357/oh-my-pi(omp,生产级 Rust+TS coding agent,本地 `oh-my-pi/`,只读参考)+ 2026 最佳实践,生成后续设计报告:[docs/调研/后续设计演进报告-oh-my-pi与最佳实践.md](../../docs/调研/后续设计演进报告-oh-my-pi与最佳实践.md)。新可借鉴项已登入 [[backlog-production-grade]](#17–#27)。

**核心结论(报告主线)**:Hyperion 的代码理解要从一层模糊检索演进为**三层代码智能栈**——
**向量检索(模糊召回,P1.3 已成 L2 recall@5=0.65)→ LSP/clangd(精确语义导航:references/definition/hover)→ DAP/lldb·gdb(运行时真相:attach/读栈/读变量)**。对 C 系统软件(bomez/wpa_supplicant/systemd)Bug-RCA,后两层把"调用链定位""崩溃根因"从模糊变确定——ChatDBG/KernelDiag 是学术背书。

**最高 ROI 三件事(先做)**:① clangd 三件套(P1.5,Python 用 multilspy,**不要用 pygls**);② read tree-sitter BFS 摘要+elision footer+二进制守卫(P1.4 复用已有 parser);③ grep 升级正则+ignore+二进制守卫+FS 缓存(P1.4,backlog #1)。

**omp 几个被纠正的常见误解**:Hashline 精髓不在哈希(16 位 xxHash)而在"零复述+版本绑定+三层 fail-closed";TTSR 的"survive compaction"是盖戳状态存活不是文本存活;snapcompact **不是 LLM summarize 而是把丢弃历史渲染成 PNG 喂视觉模型**(零 LLM,本体研究级 v1 不做);"in-process ripgrep"是用 ripgrep 库 crate 经 N-API 不是 link 二进制。

**五条设计演进**:① 三层栈(P1.5/P2);② 平台护栏(Hashline/TTSR/advisor/搜索/snapcompact 思路,对应 backlog #1–3);③ 三场景深化(Bug-RCA=调试器驱动循环+第二意见 / 深度研究=typed 子 agent fan-out / PR 跟踪=`pr://` scheme FS);④ 记忆 P3(按轴心智模型+veracity 打分+软失效+polyphonic recall,溯源超越 omp 到 file:line);⑤ 修订路线图。见 [[research-deerflow-first]]、[[align-to-deerflow-production-grade]]。
