---
name: bug-rca
description: Root-cause a bug in a C / system-software codebase (Linux kernel, BlueZ, wpa_supplicant, systemd, dbus, network stacks …). Use when the user asks to find the root cause of a bug / crash / hang / regression / CVE, or asks "why does X break / leak / deadlock". You drive the reasoning + edit; Hyperion supplies memory + code intelligence + log forensics + patch validation + patch finalization via MCP tools. Delivers a root cause, a validated on-disk patch, and a memorized lesson.
allowed-tools:
  - hyperion_memory_recall
  - hyperion_search_codebase
  - hyperion_filter_logs
  - hyperion_blast_radius
  - hyperion_validate_patch
  - hyperion_export_patch
  - hyperion_memorize
  - read
  - grep
  - glob
  - edit
  - bash
---

# Bug 根因定位 playbook(Hyperion bug-RCA)

面向小白:你在给一个 C/系统软件仓库(Linux 内核 / BlueZ / wpa_supplicant / systemd 这类)定位一个
bug 的**根因**,并给出**能干净打上、落了盘的补丁**。Hyperion 不抢你的活 —— **读码、推理、改代码是你(opencode)
的强项**;Hyperion 只递工具:翻历史记忆、语义搜代码、过滤大日志、算改动影响面、验证补丁能不能 apply、
把补丁落成文件、把教训沉淀进记忆。你拿着这把"手术刀 + 记忆",自己开刀。

**这条 playbook 是灵活的**(中途发现走错随时拐弯、自己纠正),但有**三道必须过的硬门**:
- ⑥ 改完必须 `hyperion_validate_patch` 过(apply 不了的补丁不是修复);
- ⑦ 必须把补丁**落盘** `hyperion_export_patch`(没产 `.patch` 文件 = 没交付,聊天回复不算补丁);
- ⑧ 结束必须 `hyperion_memorize` 沉淀教训(不记 = 下次还得从头来)。

> **"硬门"的诚实边界**:这三步的工具本身是确定性执行(真 git / 文件 / DB 操作),但"你必须调它"靠本
> playbook + 专用 agent 的 prompt 强制(软,~95%)。opencode 驱动模式下 Hyperion 不驱动模型,拿不到 API
> 级强制(`tool_choice` / Stop hook);靠专用 agent + 给够步数 + "步数将尽优先这三步"语言保证可靠(e2e 实证)。
> 详见 [02-bug-rca.md](../../docs/设计/harness-v2/02-bug-rca.md) §硬门的诚实边界。

## 七把 Hyperion 工具(各自啥时候调)

| 工具 | 啥时候调 | 关键提醒 |
|---|---|---|
| `hyperion_memory_recall(query)` | **最先调**。查这个仓库历史上类似的 bug 教训 / 已知事实 | 返回的是**先验线索,不是答案**;和你的证据矛盾时,以证据为准 |
| `hyperion_search_codebase(query)` | 用**概念/自然语言**找入口(如 "p2p scan 结果路由"、"radio work 生命周期释放") | **别传猜的文件名**;只回索引里真实存在的 file:symbol:line(幻觉不出假路径) |
| `hyperion_filter_logs(path, keywords, since, until)` | 给了大日志时,按故障时间窗(HH:MM:SS)筛精华行 | 别一次读全量(几万行烧 token);运行时日志的 issue 关键词常是代码符号、不匹配日志措辞,**优先用时间窗** |
| `hyperion_blast_radius(changed_files)` | 动手改之前,看你打算动的文件会**连带波及谁**(调用方/被调用方) | 结构图驱动(BFS),非 LLM;图没建会提示先 `hyperion index` |
| `hyperion_validate_patch(patch, repo_path)` | **改完必调**。`git apply --check` 验证补丁能干净打到目标仓 | 执行硬门,零 LLM 判;过不了说明路径/格式/context 不匹配,别信 |
| `hyperion_export_patch(repo_path)` | **validate 过后必调**。把补丁写成磁盘文件 `data/bug_rca/<repo>.patch` | 空 diff 会报错(改错树/没保存);**没落盘 = 没交付** |
| `hyperion_memorize(kind, summary, ...)` | **收尾必调**。把本次根因/修法/影响面记进长期记忆 | kind: `bug_lesson`;下次类似 bug 能召回先验、少走弯路 |

## 八步流程

### ① 先 recall(定位之前)
改任何东西之前,先 `hyperion_memory_recall(<bug 线索/现象>)`。这个仓库以前踩过类似的坑吗?
有先验就拿来当**导航线索**(聚焦该看哪片代码),但**绝不照抄先验的结论** —— 本次 bug 可能只是"像",
不是同一个。和你的证据矛盾时,**以证据为准**(主动证伪先验)。

### ② 语义搜入口(别盲 grep)
用 `hyperion_search_codebase(<一个概念>)` 找入口函数/数据结构,拿到真实的 file:symbol:line 锚点,
**再**用 read/grep 精读确认。这比一上来 grep 整个树又准又省 token。没建索引会提示先 `hyperion index`。

### ③ 过滤日志(若给了日志)
有大日志(`--log` / 用户提供)时,`hyperion_filter_logs(<path>, since=<故障时刻>, until=<...>)`
按时间窗切出精华行。运行时日志优先用时间窗(代码符号常不匹配日志措辞);确认是日志词汇再加 keywords。

### ④ 立假设 + 证伪(对抗"自己骗自己")
综合 recall 先验 + 代码 + 日志,给出一句话**根因**(why 句:为什么出这个 bug,不是"出了什么 bug"),
配 `file:line` 证据。**输出根因前主动找一条可能推翻它的反例**(日志/堆栈/调用链里仍矛盾的地方):
- 找不到任何反例 → 结论站得住,进 ⑤;
- 找到矛盾 → 退回 ② 重新聚焦,别硬下结论。

### ⑤ 查 blast-radius(动手之前)
打算改哪几个文件?先 `hyperion_blast_radius(<那些文件>)` 看**连带波及面**(谁调它们、会被牵连)。
影响面大 / 命中枢纽节点 → 改法要更保守、更小;影响面小说明改动局部、安全。

### ⑥ 改代码 + validate(**硬门**)
用 edit 直接改文件(别把 diff 贴在回复里)。改完**必须** `hyperion_validate_patch(<你的补丁>, <repo 绝对路径>)`:
- `applies=True` → 补丁干净、可继续;
- `applies=False` → 路径/格式/context 不匹配,**不算修复**,回去修到过为止。

补丁怎么来:用 `git -C <repo> diff` 观察你对工作树的改动(行号/格式天然对)。

### ⑦ 落盘补丁(**硬门**)
validate 过后,**必须** `hyperion_export_patch(repo_path=<repo 绝对路径>)` 把补丁写成磁盘文件
`data/bug_rca/<repo>.patch`。空 diff 会报错(改错树 / 没保存 / 被 gitignore)。**没落盘 = 没交付**
—— 聊天里说"我改好了"不算补丁,`.patch` 文件才是给人 / CI 看的产物。这步把 git diff 落成 unified diff
(对齐整条管线;apply 验证已在 ⑥ 做过,这里只保证有非空补丁上盘)。

### ⑧ memorize 教训(**硬门**)
收尾**必须** `hyperion_memorize(kind="bug_lesson", summary=<一句话根因>, root_cause=<完整根因>,
file=<主根因文件>, line=<行>)`。把本次 symptom/root_cause/fix/影响面记进长期记忆 —— 下次类似 bug
第一步 recall 就能命中、少走弯路。**不记 = 下次从零开始,白干。**

## 收尾汇报(给用户)
一句话讲清:① 根因(why 句 + `file:line` 证据 + 你证伪的结果);② 补丁(validate 结果 + **`export_patch` 落盘路径**);③ 记了啥教训。

## 反模式(别这么干)
- ❌ 一上来 grep 整个树盲找 —— 先 search_codebase 用概念拿锚点。
- ❌ 把记忆当答案照抄 —— 它是先验,必须对着本次证据证伪。
- ❌ 改完不 validate 就说"修好了" —— apply 不了的补丁不是修复。
- ❌ 改完不落盘就收工 —— 没产 `.patch` 文件 = 没交付,聊天回复不算补丁。
- ❌ 不 memorize 就收工 —— 不沉淀等于没学习,下次重踩。
- ❌ 顺手重构 / 无谓探索 —— 只修这个 bug,最小改动。

---
*Hyperion harness · bug-RCA skill · 转向 tool+skill server 后的标准流程(替代老固定管线)。*
