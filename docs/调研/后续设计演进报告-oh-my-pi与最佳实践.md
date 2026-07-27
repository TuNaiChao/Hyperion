# Hyperion 后续设计演进报告

> **版本**:v1.0(2026-07-27)
> **目的**:在 P1.3(代码理解检索层,L2 recall@5=0.65 达标)之后,综合 **oh-my-pi(omp)、deer-flow、code-review-graph(CRG)** 三个参考实现 + **2026 年 agent 开发最佳实践与学术先验**,给出 Hyperion 后续阶段的整体设计演进。
> **不是**:逐特性翻译 omp,而是提炼"能提升 Hyperion coding 能力"的机制,映射到 Hyperion 的三场景(Bug-RCA / 深度研究 / PR 跟踪)+ 共享平台,排期到生产级。
> **配套**:设计依据见 [P1 设计报告](../设计/p1-code-understanding-design.md)、[向量数据库报告](向量数据库设计分析报告.md)、[CRG 调研报告](code-review-graph-调研与借鉴.md);生产级待办见 `.claude/memory/backlog-production-grade.md`。

---

## 0. 执行摘要

调研最关键的结论是一句话:

> **Hyperion 的"代码理解"要从一层模糊检索,演进为「三层代码智能栈」:**
> **向量检索(模糊召回,P1 已成)→ LSP/clangd(精确语义导航)→ DAP/lldb·gdb(运行时真相)**。
> 对 C 系统软件(bomez/wpa_supplicant/systemd/pipewire)的 Bug-RCA,后两层是把"调用链定位""崩溃根因"从模糊变确定的关键跃迁——这正是 omp 的核心卖点,也有 ChatDBG / KernelDiag 等学术先验背书。

围绕这条主线,本报告给出 **五条设计演进**,并落到修订后的路线图:

| # | 演进 | 借鉴源 | 价值场景 | 落点阶段 |
|---|---|---|---|---|
| 1 | **代码理解三层栈**(vector → LSP → DAP) | omp LSP/DAP + ChatDBG + KernelDiag | Bug-RCA(核心) | P1.5 / P2 |
| 2 | **共享平台护栏加固**(Hashline / TTSR / advisor / 原生搜索 / read 摘要 / snapcompact 思路) | omp harness + deer-flow 中间件 | 全场景 | P1.4–P2(对应现有 backlog #1–3) |
| 3 | **三场景工作流深化**(调试器驱动 RCA 循环 / typed 子 agent fan-out / `pr://` scheme FS) | omp task + scheme FS + atomic commit | 三场景各自 | P2 / P4 / P5 |
| 4 | **记忆与持续学习**(按轴分解的心智模型 + veracity 打分 + 软失效 + polyphonic recall) | omp mnemopi/hindsight + A-MEM + Learn-to-Memorize | 持续学习(P3) | P3 |
| 5 | **修订路线图**(把以上插进 P1.4 → P6) | 综合 | — | — |

**最高 ROI 三件事(先做)**:① clangd `references/definition/hover` 接成 LangGraph 工具(调用链从模糊变精确);② read 工具加 tree-sitter BFS 摘要 + 二进制守卫 + elision footer(复用已有 parser,半天~1 天,信息密度跃升);③ 字面子串 grep 升级为正则 + ignore 清单 + 二进制守卫(backlog #1,治本)。

---

## 1. 调研方法与各参考项目角色

### 1.1 方法
- **前沿调研**(WebSearch,2025–2026):context engineering、子 agent fan-out、agent 记忆与持续学习、调试器驱动 RCA。
- **源码精读**:派 6 个 Explore agent 深读 omp 各子系统(Hashline / TTSR+advisor / LSP+DAP / 记忆 / subagent+scheme-FS+atomic-commit / 原生搜索+snapcompact+read),每个返回带行号的设计结论。
- **学术先验**:ChatDBG(LLM 驱动 lldb/gdb/pdb 做 RCA)、KernelDiag(agent 化内核崩溃定位)、A-MEM / Learn-to-Memorize / Letta learning-sdk(agent 记忆与持续学习)。

### 1.2 四个参考各自贡献什么(避免重复计数)

| 参考源 | 贡献维度 | 对 Hyperion 的角色 |
|---|---|---|
| **deer-flow**(Python/LangGraph) | 中间件链(ToolErrorHandling / ToolOutputBudget / ReadBeforeWrite / LoopDetection / TokenBudget / Summarization)、声明式工具注册、沙箱 ABC + provider、env 刮密、反射加载 | **harness 骨架**:Hyperion 已对齐(P0 demo),是护栏的"基础层" |
| **code-review-graph**(Python) | SQLite 图存储原子性(BEGIN IMMEDIATE+WAL)、远端 provider 三态加载 + 冷却自愈、响应 index 三分支校验、自定义 UA | **provider 硬化 + 图存储**:已部分对齐(P1.2/P1.3 backlog #8/#12),是检索/嵌入的"硬化层" |
| **oh-my-pi**(Rust+TS,生产级 coding agent) | LSP/DAP 集成、Hashline 编辑、TTSR 流式规则、advisor 副驾、scheme FS、typed 子 agent、mnemopi 记忆、原生搜索 + snapcompact + read 摘要 | **能力上限参考**:给 Hyperion 提供"生产级长什么样"的目标,逐特性选择性移植 |
| **学术先验**(ChatDBG/KernelDiag/A-MEM…) | 调试器驱动 RCA 的对话循环、内核崩溃 agent 定位、可学习/可链接的记忆 | **理论背书 + 机制依据**:证明 omp 的 DAP/记忆设计不是孤例,有可引用的方法论 |

**定位原则**:deer-flow/CRG 是已对齐的"基础+硬化层";omp 提供能力上限和具体机制;学术先验给理论依据。本报告聚焦 **从 omp 借什么、怎么借、何时借**,叠加在已有的 deer-flow/CRG 基础上。

---

## 2. 核心设计演进:代码理解「三层栈」

这是本报告最重要的设计决策。当前 P1.3 只完成了**最底层**。

### 2.1 为什么一层不够

Hyperion 现状:tree-sitter 切块(P1.0/1.1)→ embedding(P1.2)→ LanceDB 混合检索 + reranker(P1.3,L2 recall@5=0.65)。对"语义相似的代码"召回好,但对 C 系统软件 RCA 的两类硬需求力不从心:

| RCA 硬需求 | 现状(向量+tree-sitter) | 缺口 |
|---|---|---|
| "这个错误处理函数被谁调用" | 向量模糊召回,漏调用点 | **跨文件精确调用链** |
| "崩溃栈里这函数定义在哪、签名、宏展开" | tree-sitter 单文件切块,碰不到系统头 | **含头文件的精确跳转** |
| "这个结构体字段在哪被赋值" | 无 | **字段级引用** |
| "为什么走到这个 error 分支 / 崩溃时局部变量是什么" | 无 | **运行时现场** |

### 2.2 三层栈定义

```
┌─────────────────────────────────────────────┐
│ L3  DAP / lldb-dap·gdb        运行时真相       │  ← attach 进程 / 读栈 / 读变量 / 断点
│     (Bug-RCA 现场深挖,可复现 bug)            │
├─────────────────────────────────────────────┤
│ L2  LSP / clangd              精确语义导航     │  ← references / definition / hover / diagnostics
│     (调用链从模糊变精确,全工作区)            │
├─────────────────────────────────────────────┤
│ L1  向量 + BM25 混合检索       模糊召回         │  ← P1.3 已成,L2 recall@5=0.65
│     (语义相似代码、快速定位起点)              │
└─────────────────────────────────────────────┘
```

三层**互补不互替**:L1 模糊召回找"起点"(哪个文件大概相关)→ L2 精确导航顺"调用链"(谁调谁、定义跳转)→ L3 现场验证"为什么"。一个 RCA 任务通常 L1 起手、L2 串链、L3 收口。

### 2.3 LSP 层(clangd)—— 最高 ROI

**机制**(omp `packages/coding-agent/src/lsp/`):agent 工具 → JSON-RPC over stdio → clangd 子进程。14 个操作,Hyperion 先做三件套:

- **`references(file,line,symbol)`** —— `textDocument/references`,精确列出全工作区调用点。omp 带 **2 次重试 + 250ms 退避**(防 clangd 索引未完成只命中声明本身,`lsp/index.ts:2510-2556`)。
- **`definition(file,line,symbol)`** —— 跳定义,**含系统/库头文件**(`<bluetooth/bluetooth.h>`、`glib.h`),tree-sitter 切块根本碰不到。
- **`hover(file,line,symbol)`** —— 函数签名、宏展开、枚举值、结构体字段。

定位统一 `file+line+symbol`(symbol 子串,支持 `name#N`),project-aware server **强制要求 symbol**,拒静默回退到行首——防误定位。

**硬前提**:`compile_commands.json`(clangd 没有 it,references 质量骤降)。
- bluez(autotools):`bear -- make` 生成。
- systemd(cmake):`cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON`。

**Python 接入选型**:用 **multilspy**(微软开源 Python LSP **client** 库,内部处理 stdio JSON-RPC + initialize 握手 + 文件同步)。**不要用 pygls**(那是写 server 的)。

### 2.4 DAP 层(lldb-dap / gdb)—— 高阶现场武器

**机制**(omp `packages/coding-agent/src/dap/`):28 个操作,Hyperion 取高价值子集:

- **`attach(pid)`** —— 连已存在进程(bluetoothd/wpa_supplicant 守护进程关键);gdb(`-i dap`)/lldb-dap/codelldb 都支持。
- **`set_breakpoint(file,line)` / `continue`** —— 在可疑路径下断、跑到命中。
- **`stack_trace()` / `scopes()` / `variables(ref)`** —— 读栈、读作用域、**递归展开 `struct btd_device *` 看字段** → 正是 RCA 想要的"现场快照"。

**attach 流程**(omp `dap/session.ts:357-418`):spawn adapter → initialize(缓存 capabilities)→ **先订阅 stop 事件再发 attach**(5s 捕获初始 stop)→ configurationDone 握手 → 预取栈顶帧。

**学术背书**:这正是 **ChatDBG** 的范式——LLM 被赋予自主发调试器命令(`where`/`up`/`down`/`print`/`locals`)、读栈读变量、对话式追溯根因。ChatDBG 已覆盖 C/C++/Python/Rust 的 pdb/lldb/gdb。**KernelDiag** 进一步证明 agent 化内核崩溃定位可行(bluez/systemd 强相关)。Hyperion 的 Bug-RCA = ChatDBG 模式 + omp 的 DAP 工程化 + Hyperion 的日志驱动入口。

**门槛(必须正视)**:
1. 目标带调试符号(`-g` 或装 dbgsym 包)。
2. `ptrace_scope` 权限(`echo 0 > /proc/sys/kernel/yama/ptrace_scope` 或 `setcap`)。
3. D-Bus 激活的守护进程,attach 时机要卡准(可能需 launcher 脚本让进程一启动就 `SIGSTOP` 等挂)。
4. **价值边界**:DAP 集中于"可复现 bug 的现场深挖";不可复现的 bug 以日志驱动为主,attach 用不上。post-mortem(已崩溃)需 core dump。

**Python 接人**:**无成熟 Python DAP client 库**(空白),需手写 ~600–800 行。但 DAP 与 LSP 都用 `Content-Length` 分帧,可复用一套 framing。先支持 stdio 模式的 `lldb-dap`。

### 2.5 分阶段(已成型)

| 阶段 | 内容 | 周期 | ROI |
|---|---|---|---|
| L2-a | clangd `references/definition/hover`(multilspy)+ compile_commands 生成 | 1–2 周 | **最高**(调用链精确化) |
| L2-b | clangd `diagnostics/documentSymbol/workspaceSymbol`(改补丁后验编译、定位符号) | 2–3 周 | 高 |
| L3-a | 手写 DAP client(lldb-dap)+ `attach/set_breakpoint/continue/stack_trace/scopes/variables` | 3–4 周 | 高(可复现 bug 现场) |
| L3-b(暂缓) | instruction/data breakpoint、memory R/W、disassemble | — | 低(RCA 用得少) |

> 写入路径的 LSP(writethrough 写后诊断、rename_file 跨文件 willRenameFiles)对 RCA 价值低(RCA 以读为主),暂缓;若 P2 做"agent 自动出补丁",再抄 willRenameFiles 的多 server edit 合并(`lsp/index.ts:1978-2031`)。

---

## 3. 共享平台护栏加固(对应现有 backlog)

Hyperion 的 agent 中间件链目前是空的(`create_agent(..., middleware=[])` 裸跑),grep 是字面子串,read 直 dump,str_replace 有截断风险。omp 的 harness 给了生产级答案,这一节对应 backlog #1–3,是**跨场景**的底座。

### 3.1 Hashline 治 str_replace 截断(backlog #2)

**问题**:P0 的 `str_replace` 读整文(50000 字符截断)→ 替换 → 写回,>50KB 文件被截断标记污染、丢数据;且 str_replace 首次命中率低(旧串匹配失败、改错同名行)。

**omp 机制**(精读纠正了"哈希"的误解,精髓在约束系统):
- **行锚点补丁语言 + 整文件哈希 tag**:模型编辑时**只输出变化行,零复述旧文本**(对比 str_replace 必须输出完整旧段+新段)——这是 −61% output token、首次命中率十倍的根因。
- **三层 fail-closed 防破坏**:① tag 校验(整文件 xxHash32 低 16 位)② **seen-line 守卫**(模型只能改它实际读到过的行,未读行拒绝,错误里 inline 实际行内容)③ **recovery**(文件被改过但 tag 还能解析历史快照时,行映射重放,歧义即拒)。永远"宁可让模型重读,也不猜"。
- **格式**:`[path#TAG]` 头 + `SWAP/DEL/INS.PRE|POST|HEAD|TAIL` 行级 + `SWAP.BLK` 块级(tree-sitter 展开);分隔符刻意用 `.=`(避开 diff/markdown 语义)。原作者用 **lark 文法**(Python 直接有 `lark` 包)。
- **⚠️ 一个文档措辞的坑**:"injections survive compaction" 是指 TTSR 的(见 3.2),不是 Hashline。Hashline 的 `all-or-nothing` 多文件(`prepare→commit` 两段式,preflight 全过才落盘)对 PR 跟踪"一次改多文件"有用。

**Hyperion 落地(三阶段)**:
1. **MVP(1–2 天)**:行级 ops + `computeFileHash`(Python `hashlib`/`zlib.crc32 & 0xffff`)+ 内存 `SnapshotStore`(`cachetools.LRUCache`)+ `applyEdits`(自下而上 per-line bucket)+ `MismatchError`。**这一版就根治截断丢数据 + 改错同名行**。
2. **seen-line 守卫 + 容错解析**(再 1–2 天):`seenLines: set[int]` + 未读行拒绝 + 容错(`SWAP N:`/漏冒号/裸 body 补 `+`)。
3. **recovery + 块锚(按需)**:`difflib.SequenceMatcher` 对应其 `diffLineRuns`;`tree-sitter-c` 支持 `SWAP.BLK`(对 C 大函数是杀器,直接替换整个函数体不用数行号)。

> boundary repair(apply.ts:822-1011 那 200 行括号配平修复)先留空,跑 benchmark 看模型常犯什么错再决定。C 代码缩进/括号相对规整,可能比 TS 更不需要它。

### 3.2 TTSR(流式规则注入)治空中间件链(backlog #3)

**omp 机制**:
- **事件驱动按需注入**:规则平时**零 prompt token**(休眠,只是几个 RegExp),只有主 agent 流式输出命中 regex/AST 模式时,**中断当前流 → 注入规则为 system reminder → 从断点重试**。"time-traveling" 指 `contextMode:discard` 把违规 partial 输出从对话**抹掉**。
- **省 context tax**:休眠零成本 + 只在违规轮付税 + discard 净负成本 + 盖戳去重(`repeat:once`)+ AST 节流。**只适合高置信/低频/高代价错误**(禁用某 API、强制走 wrapper),不适合"偏好提示"。
- **⚠️ 文档措辞纠正**:"injections survive compaction" **不是说规则文本存活**。注入的 `<system-interrupt>` 消息**会被 compaction 删掉**;真正存活的是**"已触发规则"的盖戳状态**(resume 时 `restoreInjected` 重写),保证 `repeat:once` 规则不误重触发。移植按源码理解,别照营销文案。

**Hyperion 落地**(LangGraph):TTSR 不是标准 node 间中间件,而是**包在 model call 节点外的 wrapper**:
- `astream_events_v2` 的 `on_chat_model_stream` 累 token 进 scoped buffer → regex 匹配 → 命中 `break`+`cancel` in-flight → 从 `state["messages"]` 移除 partial AI message → append `SystemMessage(rule.content)` → `Command(goto="agent")` 重入。
- 盖戳:`state["fired_ttsr_rules"]: set[str]` + SQLite/JSONL side-log(hydrate on resume)。
- 规则配置抄 omp frontmatter(YAML:`condition` regex 列表=OR、`astCondition` tree-sitter 模式、`scope: tool:edit(*.c)`、`interruptMode`)。

**C 场景规则示例**:
```yaml
description: BlueZ callback 注册必须配对清理
condition: "g_dbus_register_property"
scope: "tool:edit(*.c), tool:write(*.c)"
interruptMode: never   # 命中不中断,只在工具结果前置提醒
---
g_dbus_register_property 注册的 interface 必须在 object 析构时
配对调用 g_dbus_unregister_property,否则 client 访问已释放的 property table。
```

### 3.3 Advisor(副驾模型)—— Bug-RCA 的"第二意见"

**omp 机制**:
- 独立 model + 独立 Agent 实例,主 turn 结束只送**增量 delta**(游标 + wyhash 指纹去重)。`advise({note, severity})` 工具三档 → 三通道:nit→aside(非中断排队)/ concern→steer(中断)/ blocker→steer(即使已 done 也强制新 turn)。
- **核心设计**:`<advisory guidance="weigh, don't blindly obey">`——**主 agent 的 system prompt 从不提 advisor**,引导语只藏在 tag 属性,避免主 agent 被"你在被监督"分心。advisor 是"陪审团"不是"上诉法庭"。
- **完全隔离**:独立 ToolSession(防污染主 seen-lines)、默认只读 `{read,grep,glob}`、独立 cost 归因、**失败永不阻塞主**。
- **⚠️ 三层 emission-guard 是 load-bearing 的**:omp 实测一个 advisor 刷了 309 次 advise、114×"Stop."。必须抄:① normalize(小写+NFKC+折空白)② content-free 黑名单(stop/done/lgtm…)③ exact-text FIFO 去重 + 每 prompt 一条。advisor 永远只看到 "Recorded."。**别用比主 agent 更强的模型当 advisor**(要"换视角"不是"更聪明",还贵)。

**Hyperion 嫁接(分阶段)**:
1. 单 advisor 只读只发 `nit`(aside 通道,每 turn 末增量 delta 喂 advisor model,note 写进 state 作 SystemMessage)。**先把 emission-guard + dedupContextMessage 抄过来**。
2. 加 `concern`/`blocker` + steer(LangGraph 1.x `Command(interrupt=...)`)。
3. 多 advisor(Bug-RCA 配"反方假设"+"修复副作用"两个,各独立 subgraph + 独立 model;**用不同模型家族**避免同源盲区)。

> 成本警告:第二模型读每轮会显著增加成本。对策:仅高风险 RCA 按需启用、用更便宜模型、增量 delta 而非全量。

### 3.4 原生搜索 + read 摘要 + snapcompact 思路(backlog #1 + context)

> **前提**:Hyperion 是 Python 项目,Rust 内嵌不现实,但 omp 的**策略/算法/契约**全可 Python 落地。

**(a) 补强字面子串 grep(backlog #1,按 ROI 排序)**:
1. **二进制守卫(最高优先,最低成本)**:读前 8192 字节,含 NUL 或 `decode('utf-8')` 失败即判二进制,拒绝并提示 `:raw`。Python 十几行,当天上线。抄 `packages/utils/src/binary.ts:29-37`。
2. **ignore 清单**:别自写 glob matcher,用 `pathspec`(GitWildMatchPattern)加载 `.gitignore` + 内建 skip(`.git`/`node_modules`/`.venv`/`__pycache__`/`target`/`build`)。`.git` 永远剪枝。
3. **FS 扫描缓存**:缓存键 `(canonical root, include_hidden, respect_gitignore, skip_dirs, detail)`;TTL 1s + **空结果 200ms 重检**(防"刚 grep 没命中→文件其实刚加")+ 写后按路径前缀失效(rename 双侧失效)。只缓存"扫描条目"不缓存最终 grep 结果。Python `dict[key,(created_at,entries)]` + `threading.Lock`,16 条 LRU。
4. **正则替代字面子串**:`re` + 抄 `grep.rs` 的 brace sanitize(模式里 `${platform}` 当 malformed repetition 时先转义再 compile,失败则括号转义重试)。
5. **最大延迟收益来自 FS 缓存命中**(不是引擎本身)。即便纯 Python grep,加缓存重复 glob/find 也能从几十 ms 降到 µs 级。

**(b) read = tree-sitter 摘要(最快实际收益,复用已有 parser)**:
- **决策树**(抄 `read.ts:2523-2556`):**显式 selector(`:N`/`:N-M`/`:raw`)走 verbatim 范围读,绕过摘要**;无 selector + 文件够大才摘要。agent 主动 `read f:10-40` 时它知道要什么,别糊弄;裸 `read f` 是"摸底",摘要比 dump 全文信息密度高得多。
- **tree-sitter BFS unfold 算法**(抄 `crates/pi-ast/src/summary.rs:108-160`):收集可折叠节点(函数体 ≥4 行、块注释 ≥6 行)成 forest;BFS unfold 到可见行 ≥50,单次展开会破 100 则跳过该子树(不饿死兄弟);渲染 `… {`+`}` 合并单行。Python ~30 行,复用 P1.0 的 tree-sitter parser。
- **elision footer(必抄,否则摘要=丢信息)**:末尾用真实 selector 举例(`# 完整正文用 read path:10-40,52-61 恢复`),让 agent 一回合捞回省略 body。
- **非对称 padding**(leading=1/trailing=3):omp 实测 follow-up 多是跳跃不是邻接,对称 padding 不划算。
- per-session LRU memo(48,`cachetools.LRUCache`)。

**(c) snapcompact → LangGraph context 管理(思路借鉴,本体不做)**:
- **⚠️ 认知纠正**:snapcompact **不是 LLM summarize**,是把丢弃历史渲染成 PNG 像素字体图喂视觉模型,**零 LLM 调用**,把 token 从文本换成更便宜的视觉 token 且不丢信息。
- **直接抄的(纯 Python)**:序列化预算契约——tool result head+tail 截断(2000 字符,0.6 头比)、tool call 参数 per-value(500)/per-call(2000)双限额(write/edit body 不限额会灌爆归档)、**useless 结果成对丢弃**(零匹配搜索、超时)、thinking 对 Claude 设 `includeThinking=false`(reasoning 喂回触发 `reasoning_extraction` 分类器)。
- **pre-compaction pruning**(比 summarize 便宜的第一道防线):保护最近 40k token 的 tool output;要求至少省 20k token 才剪;**不剪低于 50 token 的结果**(placeholder 本身 ~8 token,剪了反亏 + 破 prompt cache)。
- **Archive.text 思路**:归档存"bounded 源文本"而非"上次 summary",下次从源文本重算,避免 summary-of-summary 衰减。
- **本体(渲染成图)是研究级**(omp 在 `research/` 跑了几十个 200k-token eval 才定每模型的 shape),**v1 不做**;v1 = 序列化预算 + 剪枝 + LLM summarize。snapcompact 给的是"上限参考":压缩不一定丢信息,可换载体。

---

## 4. 三场景工作流深化

### 4.1 Bug-RCA:调试器驱动循环(P2 核心)

**设计**:ChatDBG 模式 + omp DAP 工程化 + Hyperion 日志入口。工作流:
1. **日志驱动起手**:解析日志/栈 → 抽可疑符号/文件(L1 向量检索找起点)。
2. **L2 精确串链**:clangd `references`/`definition` 拉调用链、跳定义(含头文件)。
3. **L3 现场验证**(可复现 bug):`attach` 到进程 → 可疑路径下断 → `continue` 到命中 → `stack_trace`/`scopes`/`variables` 读现场。
4. **第二意见**:advisor(不同模型家族)读每轮,发 concern(blocker:根因没解释关键日志行 / concern:跳过复现直接修 / concern:修了症状没修根因)。
5. **报告 + Memorize**:带溯源(`file:line`/commit/日志片段)+ 置信度的报告,内化进记忆(P3)。

**补丁**:若 agent 出补丁,用 Hashline(3.1)而非 str_replace;改后 clangd `diagnostics` 验编译。

**学术锚点**:ChatDBG(LLM 自主发调试器命令做 RCA)、KernelDiag(agent 化内核崩溃定位)。

### 4.2 深度研究:typed 子 agent fan-out(P5)

**设计**:omp `task` 的 typed schema 结果 + LangGraph superstep 并行。

**omp 机制(最值得抄的)**:子 agent **不写散文**,调 `yield` 工具提交结构化数据。
- schema 三级优先(call-site > agent 定义 > session);增量 yield(`findings` 数组累积,适合研究边查边报)vs 终结 yield(`result`);校验+重试三态 **valid / invalid(retry) / unavailable**,strict 拒绝重试 vs permissive 带警告接受。父拿 `StructuredSubagentOutput{status,data,error}` 机器可读对象。
- fan-out:`tasks[]` batch + `mapWithConcurrencyLimitAllSettled`(**不 fail-fast**,一个方向挂不影响其他)+ 可缩放 Semaphore。
- `AgentOutputManager` 把 yield 落成 `<id>.jsonl` + 嵌套命名(`Anna.Bob`)+ resume 从磁盘 seed → `agent://` 句柄可寻址,对"带引用研究报告"产物持久化有用。

**LangGraph 落地**:
- yield+schema+retry → conditional edge(invalid 回子 graph 重跑,给校验失败反馈);增量 yield 贴研究多轮搜集。
- fan-out 用 LangGraph superstep + typed reducer schema + **显式 partial-failure 处理**(digitalapplied 5 模式 / LangChain docs)。
- **别照搬 worktree 隔离**(研究/分析不并发改码),但留**隔离思想**:子 agent 从空白历史起步,只通过 `context` 显式注入契约,避免上下文污染。
- **澄清**:`/handoff` 是会话级(长会话压成文档开新会话),**不是**子 agent→父的 typed 回传(那是 yield),别混。

### 4.3 PR 跟踪:`pr://` scheme FS + 改动分析(P4,直接价值最高)

**设计**:把 GitHub/PR/issue 当文件系统,read/grep 透明解析——**模型只学一个工具**。

**omp 机制**:
- 14 个 scheme(omp/agent/artifact/memory/local/vault/skill/rule/mcp/issue/pr/history/ssh/xd),单全局 router,每 scheme 一个无状态 handler,统一 `resolve/write/complete`。`read src/foo.ts` 和 `read pr://1428` 走**同一条分页/selector 通道**。加新数据源只加 handler,工具面零膨胀——"把数据源和工具动作解耦"。MLflow 工具最佳实践也佐证:**窄工具优于宽工具**。
- **`pr://` 切片对大 PR 是刚需**:`pr://N/diff`(列文件)→ `pr://N/diff/<i>`(单文件)→ read 的 `:50-100` 行范围,治"大 PR diff 爆 context"。
- SQLite 缓存(soft TTL 5min / hard TTL 7天 + 后台刷新 + 失败回退带 stale 警告)正合周期性 tracker。
- `ResolveContext.cwd` 多 session 正确性(哪个 repo 是 `pr://123`)。

**Hyperion 落地**:
- Python `dict[scheme, Handler]`,handler `resolve(url,ctx)->Resource{content,source_path,immutable}`;read/grep 先 `canHandle(path)`。
- 扩展自己的 scheme:`commit://`(上游某 commit)、`agent://`(历史分析结论)、`rule://`(本地合入策略),同一接口。

**"是否合入本地"分析(借 atomic commit 算法,不借提交流程)**:
- **`getFilePriority` + 源码优先 + token 预算**(源码=100 > manifest=70 > test=10 > 文档=20,binary=−100;大 diff 头15+尾10+elided)→ 直接用于 PR diff 分析,比"整份 diff 塞模型"省 token 且更准。
- **拓扑排序 + 环检测反过来用 = PR 改动归类**:判断"原子改动 vs 混了多件事"——混合多目的 PR 风险高,这正是"是否合入"的关键信号。
- lock 文件 + sibling manifest 确定性归组(Cargo.lock/go.sum 影响面)。
- advisor 做"差异审计":单独跑一遍 diff,看主 agent 有没有漏掉 reviewer 提的关键风险。

---

## 5. 记忆与持续学习(P3)

**先纠正概念**:omp 记忆不是"retain/recall/Hindsight 三层",而是**两个正交维度 + 一个横切概念**:
- **工具层**:`retain`(存持久事实)/ `recall`(原始条目检索)/ `reflect`(跨记忆合成)。
- **后端层(4 选 1)**:`off` / `local`(磁盘 md,离线两阶段 LLM 流水线)/ `hindsight`(远程服务端)/ `mnemopi`(本地 SQLite+向量,schema 最全)。
- **"心智模型"是横切第三概念**:把累积记忆压成命名摘要、下一 session 首轮加载。

### 5.1 omp 比 Hyperion P3 规划做得好的 6 点(直接可抄)

1. **心智模型按轴分解,非单一 summary**(hindsight `seeds.json`,create-only):user-preferences/project-conventions/project-decisions,各有独立 `source_query`+token 预算+刷新触发。**Hyperion 应拆成 bug-patterns / module-architecture / failure-modes / team-conventions / regression-timeline**(后两个对 Bug-RCA 尤其值钱)。
2. **Veracity 一等打分维度**:stated/inferred/tool/imported/unknown/false **直接进 recall 排序权重**(1.0→0)和 consolidation。Hyperion 的"实测确认 vs 推测 vs 文档转述"正好映射,**置信度要参与检索排序,不只是元数据**。
3. **软失效 `superseded_by` 不删原行**:Bug-RCA 常出现"旧结论被新证据推翻",需保留被推翻结论供回溯。
4. **分层降级治膨胀**(30 天截 800 字+废向量,180 天抽关键信号到 300 字):Hyperion 报告天然很大,长期跑必爆,比 evict 优雅。
5. **反反馈包装 + "current repo wins"**:`stripMemoryTags` 在 retain 前剥掉已注入记忆块防闭环;"repo state wins,memory alone is NEVER sufficient proof,验证后才提置信度"——coding agent **安全刚需**。
6. **Polyphonic recall(4 路 RRF)**:vector+graph+fact+temporal 四声 RRF 融合。对 Bug-RCA,**temporal 声("这 bug 何时引入/修过")和 graph 声("相关模块历史故障")**比纯向量 RAG 强很多。

### 5.2 Hyperion 应超越 omp 的 3 点

- **溯源深化**:omp Phase1 抽取溯源只到 session_id;Hyperion 必须到**代码 `file:line` / commit / 日志片段**(Bug-RCA 硬需求)。
- **置信度闭环**:omp confidence 静态;Hyperion 加"被后续 bug 验证为真则上调"闭环(schema 里 `validation_count` 字段已存在)。
- **跨项目维度**:三场景(尤其 PR 跟踪)需多报告跨项目检索,可能要"团队共享 bank"维度。

### 5.3 P3 落地 7 条(基本成型)

1. schema 直接抄 mnemopi(SQLite+FTS5,字段 source/importance/confidence/veracity/valid_until/event_date/superseded_by/metadata)。
2. 心智模型按轴拆分(每个轴 `source_query`+独立预算+`refresh_after_consolidation`+create-only)。
3. retain 前强制 strip 已注入记忆块(防闭环)。
4. recall 融合 vector+FTS+importance+veracity+temporal(Weibull 衰减)。
5. 项目隔离用 `basename+hash(abspath)`,**不依赖 git root**(#2412 教训)。
6. 两阶段压缩(per-报告抽取 → 跨报告综合,lease+heartbeat 并发安全)。
7. 分层降级 30/180 天。

**学术锚点**:A-MEM(agent 自组织记忆+链接)、Learn to Memorize(可学习记忆周期)、Letta learning-sdk(持续学习,防灾难性遗忘)、ICLR 2026 记忆 workshop。

---

## 6. 修订路线图(把以上插进 P1.4 → P6)

> 原 P0–P6 见 [architecture.md §11](../设计/architecture.md)。本节是叠加演进后的建议序列。

| 阶段 | 内容 | 借鉴 |
|---|---|---|
| **P1.4**(当前下一步) | code_nav 工具:`grep_symbol`/`read_function`/`search_code` 接入 agent(设计 §8)+ **read tree-sitter 摘要 + elision footer + 二进制守卫**(3.4b)+ **grep 正则/ignore/二进制守卫/FS 缓存**(3.4a) | omp read/grep + backlog #1 |
| **P1.5** | **LSP 层(clangd references/definition/hover via multilspy)+ compile_commands 生成**;此时"三层栈"L2 成形 | omp LSP + ChatDBG(2.3) |
| **P2** | **Bug-RCA 工作流**:日志解析 → L1 起手 → L2 串链 → 报告;**Hashline 替代 str_replace**(3.1,根治截断);**TTSR + advisor 中间件链**(3.2/3.3,backlog #3);**DAP 层(lldb-dap attach/读栈读变量)** 进 L3(2.4) | omp DAP/Hashline/TTSR/advisor + ChatDBG/KernelDiag |
| **P3** | **记忆与持续学习**:mnemopi schema + 按轴心智模型 + polyphonic recall + 软失效 + 分层降级;溯源到 `file:line`;置信度闭环(第 5 节) | omp mnemopi/hindsight + A-MEM |
| **P4** | **PR 跟踪**:`pr://`/`issue://` scheme FS + SQLite 缓存 + 改动分析(源码优先/拓扑归类/lock 归组)+ advisor 差异审计(4.3) | omp scheme FS + atomic commit |
| **P5** | **深度研究**:typed 子 agent fan-out + yield + superstep 并行 + `agent://` 产物寻址(4.2) | omp task + LangGraph fan-out |
| **P6** | **生产化**:多 provider 硬化(CRG backlog #12)、Docker 沙箱、可观测(Langfuse)、snapcompact 本体(若主攻 vision 模型)、评测扩量(backlog #13–16) | 综合 |

**贯穿(任何阶段都可顺手做)**:二进制守卫(P0 优先)、read 摘要、grep 升级——这些都是半天到几天的小改,ROI 高,不必卡阶段。

---

## 7. 决策清单(采用 / 延后 / 跳过)

**✅ 采用(先做)**:
- clangd references/definition/hover(L2,P1.5,ROI 最高)
- read tree-sitter BFS 摘要 + elision footer + 二进制守卫(P1.4,复用已有 parser)
- grep 正则 + ignore + 二进制守卫 + FS 缓存(P1.4,backlog #1)
- Hashline MVP 替代 str_replace(P2,根治截断)
- 记忆 mnemopi schema + 按轴心智模型(P3)

**🟡 延后(有明确触发条件再做)**:
- DAP attach/读栈读变量(P2,可复现 bug 才用得上,门槛高)
- TTSR + advisor(P2,中间件链成型后;advisor 有成本)
- clangd diagnostics/documentSymbol(P2 之后)
- Hashline seen-line 守卫/recovery/块锚(MVP 之后渐进)
- PR scheme FS(P4 专项)

**🔴 跳过 / 暂不做**:
- snapcompact 本体(渲染成图,研究级,v1 用 summarize)
- LSP writethrough / rename_file(RCA 以读为主)
- DAP instruction/data breakpoint / memory R-W / disassemble(RCA 用得少)
- omp worktree 多后端隔离 PAL(研究/分析不并发改码)
- omp 整套 agentic commit(Hyperion 是分析不是替人提交)

---

## 8. 风险与开放问题

| 风险 | 应对 |
|---|---|
| clangd 对无 compile_commands 的 C 项目 references 质量骤降 | P1.5 前置:为 bluez/wpa_supplicant 建 compile_commands 生成脚本(bear/cmake) |
| DAP attach 的 ptrace 权限 + D-Bus 激活时机 | 写 launcher 脚本(SIGSTOP 起步);文档化 ptrace_scope 设置;post-mortem 走 core dump |
| 手写 DAP client 工作量(~600–800 行) | 复用 LSP 的 Content-Length framing;先只支持 stdio 模式 lldb-dap |
| advisor 第二模型成本 | 按需启用、用更便宜模型、增量 delta、emission-guard 防噪 |
| LSP/DAP 子进程生命周期管理(长驻 vs 按需) | 参考 omp `ensureFileOpen`/didChange 同步 + per-cwd client 缓存 |
| 记忆反馈环(recall→retain 自我强化) | `stripMemoryTags` + "repo state wins" 硬约束(P3 安全刚需) |
| 长期记忆膨胀 | 分层降级 30/180 天(P3) |
| omp 是 Rust/TS,直接移植有阻抗 | 本报告已标注每项的 Python 等价(lark/difflib/pathspec/multilspy/tree-sitter-c/cachetools) |

**开放问题(需用户决策)**:
1. **DAP 优先级**:P2 是否同时上 LSP+DAP,还是 LSP 先行、DAP 推到 P2 末/P3?(DAP 门槛高,建议 LSP 先行)
2. **advisor 默认开/关**:Bug-RCA 默认开 advisor 增成本,还是高危任务才开?
3. **snapcompact 是否排期**:若 Hyperion 主攻 vision 模型(Claude/Gemini),snapcompact 可作 P6 精研项;否则跳过。

---

## 9. 参考文献

**oh-my-pi(源码精读,带行号见正文)**:
- [can1357/oh-my-pi](https://github.com/can1357/oh-my-pi) — 生产级 coding agent(omp),Rust+TS,fork 自 Pi。
- 关键包:`packages/hashline/`(编辑)、`packages/coding-agent/src/{lsp,dap,task,internal-urls,advisor,memory-backend,mnemopi,hindsight}/`、`packages/snapcompact/`、`packages/mnemopi/`、`crates/{pi-walker,pi-natives,pi-ast}/`。

**deer-flow / code-review-graph(只读参考)**:`deer-flow/`、`code-review-graph/`(均已在前序阶段精读,见 [CRG 报告](code-review-graph-调研与借鉴.md))。

**学术先验**:
- [ChatDBG: Augmenting Debugging with LLMs](https://arxiv.org/abs/2403.16354) / [GitHub](https://github.com/plasma-umass/chatdbg) — LLM 自主驱动 pdb/lldb/gdb 做 RCA(C/C++/Python/Rust)。
- [KernelDiag: Agent-Based Root Cause Diagnosis for Kernel Crashes](https://arxiv.org/html/2607.17722v1) — agent 化内核崩溃定位。
- [A-MEM: Agentic Memory for LLM Agents](https://www.youtube.com/watch?v=49ERSQeHC-Y) — agent 自组织记忆+链接。
- [Learn to Memorize](https://openreview.net/forum?id=EQ3TwO84Cs) — 可学习记忆周期。
- [Letta learning-sdk](https://zylos.ai/research/2026-04-09-continual-learning-catastrophic-forgetting-ai-agents/) — 持续学习 SDK,防灾难性遗忘。
- [ICLR 2026 Memory for LLM-Based Agentic Systems](https://iclr.cc/virtual/2026/workshop/10000792)。
- [Memory for Autonomous LLM Agents: Mechanisms (Survey)](https://arxiv.org/html/2603.07670v1)。

**最佳实践(2026)**:
- [Context Engineering: A Practical Guide for AI Agents (Sourcegraph)](https://sourcegraph.com/blog/context-engineering)。
- [Context Architecture for AI Agents: 2026 Guide (Atlan)](https://atlan.com/know/context-architecture-for-ai-agents/)。
- [AI Agent Tool Use Best Practices (MLflow)](https://mlflow.org/articles/ai-agent-tool-use-best-practices-for-practitioners/) — 窄工具优于宽工具。
- [Subagents (LangChain docs)](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents) / [Multi-Agent Orchestration: 5 Patterns (digitalapplied)](https://www.digitalapplied.com/blog/multi-agent-orchestration-5-patterns-that-work) — fan-out + typed reducer + partial-failure。
- [The Harness Problem (omp blog)](https://blog.can.ac/2026/02/12/the-harness-problem/) — harness 比模型更重要(harness layer > model)。

---

> **下一步建议**:本报告是设计依据。可据此 ① 更新 `.claude/memory/backlog-production-grade.md`(新增 LSP/DAP/scheme-FS/TTSR/advisor/snapcompact/Hashline/atomic-commit-分析 等可借鉴项);② 进 P1.4 code_nav 工具时,顺手把 read 摘要 + grep 升级 + 二进制守卫一并做掉(3.4,ROI 最高且不卡阶段)。
