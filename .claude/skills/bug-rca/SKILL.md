---
name: bug-rca
description: 在 C/系统软件仓库(Linux 内核、BlueZ、wpa_supplicant、systemd、dbus、网络栈)定位 bug 根因并修复。用户让你查 bug/崩溃/挂起/回归/CVE 的根因、问"为什么 X 会断/泄漏/死锁"、或修这类 bug 时用。你负责推理和改代码;RootRecall 工具提供记忆、代码检索、影响面、补丁 apply 校验、补丁/报告落盘(日志用你自己的 grep/awk 切)。补丁只有在干净 apply 且经人/真机验证后才算正确——在此之前持续迭代。
allowed-tools:
  - rootrecall_memory_recall
  - rootrecall_search_codebase
  - rootrecall_blast_radius
  - rootrecall_when_introduced
  - rootrecall_validate_patch
  - rootrecall_export_patch
  - rootrecall_memory_memorize
  - rootrecall_export_report
  - read
  - grep
  - glob
  - edit
  - bash
---

# Bug 根因定位 + 修复

你负责在 C/系统软件仓库定位根因并修复。推理和改代码是你的活;`rootrecall-*` 工具提供记忆、代码情报、日志取证、影响面、apply 校验、落盘。

## 运行模式:迭代,不是走流水线

根因很少一次猜中,补丁很少一次到位。按循环做,不要按固定顺序走:

- **假设 ↔ 证伪循环**:用 `memory_recall` / `search_codebase` 取证,大日志用 grep/awk 按时间窗切(别一次读全量);每轮主动证伪当前根因;经住证伪才定论。
- **补丁 ↔ 验证循环**:`edit` → `validate_patch`(能否干净 apply)→ `export_patch`(落盘)→ 验证(人/真机)→ 没修对就再 `edit` 再 `export`。每出一版补丁就落盘一版。
- **验证后才沉淀**:`memorize` 和 `export_report` 是验证通过后的收尾。未验证就 memorize = 把没坐实的根因/补丁写进记忆,误导后续同类 bug。

## 工具(按需调,无固定顺序)

| 工具 | 何时调 | 要点 |
|---|---|---|
| `rootrecall_memory_recall(query)` | ① 定位前(发散找线索)② 候选定稿前(定向复核,必调)——本仓库历史同类 bug | 先验是线索不是答案,以本次证据为准;定向复核的 query 用 problem_summary(现象一句话),别用原始日志原文;`codebase` 传项目名(如 `wpa`,不带版本号 —— 教训跨版本共享) |
| `rootrecall_search_codebase(query)` | 找入口符号——传概念,别传猜的文件名 | 只回真实存在的符号,不会编路径 |
| `rootrecall_blast_radius(files)` | 改之前——看连带波及谁 | 图驱动;图没建会提示 |
| `rootrecall_when_introduced(repo_path, symbol=\|file+line)` | 候选难分胜负时——「这段缺陷逻辑哪个 commit 带进来的」 | 纯 git 候选表(时间倒序+added/removed);引入 commit 通常是最老 added>0/removed==0 那条,中间成对的多是重构搬移;哪条真引入语义裁决(git show 逐条读);引入 commit 的 message/diff 常直接暴露根因意图——假设循环的辅助证据,不是硬门 |
| `rootrecall_validate_patch(patch, repo_path)` | 每版补丁都调 | 只验 **apply,不验修对** |
| `rootrecall_export_patch(repo_path)` | 每出一版补丁就调 | 落 `data/bug_rca/<repo>.patch`,供人/真机验证 |
| `rootrecall_memory_memorize(...)` | 验证通过后才调 | kind=bug_lesson;`codebase` 传项目名(不带版本号,教训跨版本共享) |
| `rootrecall_export_report(content, repo_path)` | 验证通过后才调 | 最终报告落 `data/bug_rca/<repo>-rca.md` |

## 硬约束

- `validate_patch` 过 ≠ 修对。它只查补丁能否 apply。系统软件通常没有单元测试,**真正的 oracle 是人/真机复现原故障**。
- 未经验证不 `memorize`。
- **大日志用 grep/awk 自己切**(无专门切片工具 —— 切片 opencode 的 read/grep/awk 就够,deer-flow/omp 均无专门工具,重造即踩坑#2):按故障时间窗(HH:MM:SS)+ **日志词汇**关键词(scan/result/p2p/timeout,**别用代码符号** 如 scan_res_handler —— 日志是散文形,子串不匹配)筛,封顶行数;别一次 read 全量(1.6 万行撑爆上下文)。read 给行范围、grep 给上下文,够用。
- **切窗是线索不是答案**:根因形态多样 —— 可能在窗口上游更早、很久以前的持久化状态/配置、别的日志源、或源码逻辑,不一定在本窗口、不一定是某条日志行。窗口只见现象(abort/ERROR)没看到因时:**逐步扩大窗口 / 换日志源 / 查源码与配置**,别锚定窗口里最响的行。
- **日志是线索,代码是确定答案**:日志切到的现象只是线索;真根因(状态机/分支逻辑/持久化状态)用 `search_codebase` 在源码里确定。代码情报比日志推断可靠 —— 重心放代码。

## 证伪纪律(避免误诊)

模型会锚定显眼日志行(ERROR/失败)误当根因。对抗:

- **先列候选再淘汰,别认定**:定位阶段先列 **2-3 个候选根因**(按记忆先验 + 日志线索),逐个找证据/反证,按证据强度淘汰;单候选思维 = 锚定。全部候选被淘汰才回头扩大搜索(更早窗口/别的日志源/别的子系统)。候选难分胜负时可查引入史:根因锚定到符号/file:line 后 `when_introduced` 出候选表,引入 commit 的 message/diff 常暴露缺陷意图(辅助证据路,非硬门)。
- 立根因后,**先找推翻它的证据**,找不到再定论。
- **时序检查**:现象不得早于 purported 根因。早于 = 你抓的大概率是症状(如"abort failed"其实是"扫描早完成、状态没清"的后果),回去往更早查。
- **别用残缺证据证伪先验**:日志切片可能漏了更早事件时,不能断言"X 没发生过"。
- **候选定稿前必查记忆**:根因候选收敛成 1-2 个时,**必须**用 `memory_recall(query=problem_summary)` 再召回一次本仓历史修法(定位前的召回是发散找线索,这次是定向复核——"这模式之前见过吗?当时怎么修的?")。命中的先验对照本次证据复核,与候选冲突时优先解释冲突再定论。

## 不要

- 强行一次走完固定流程——迭代。
- 把 `validate_patch` 通过当"修对"——只查 apply。
- 未验证就 `memorize`。
- 抓最响的日志行当根因,不查它之前的现象。
- 只立一个候选就闷头修——先列 2-3 个候选淘汰。
- 候选定稿前跳过 `memory_recall` 定向复核。
- 顺手重构——只做修这个 bug 的最小改动。
