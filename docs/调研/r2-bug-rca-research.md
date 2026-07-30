# R2 bug-RCA MVP 调研综合(2026-07-29)

> 状态:R2 调研完成 · 上位:[bug-rca-design.md](../设计/bug-rca-design.md) · 金标准:`example/demo2`
> 方法:5 路并行(实测优先,不臆测)——① delegate 三后端可行性 ② omp RPC 契约(源码级)③ Agentless 漏斗(源码级)④ deer-flow 委托形状 ⑤ 前沿 SOTA(WebSearch 2025-2026,已核实)。其中 ②④ 两路的子 agent 输出被 harness 注入检测命中敏感词(opencode 源码含 `--dangerously-skip-permissions`、deer-flow 触发 `<system-reminder>` 标签)而中和吞掉,改由本文直接用**实测 help + grep 源码**补齐。

---

## 0. 一句话结论(给小白)

> 想让 Hyperion 像个"接案调度员":它翻自己的笔记本(记忆)、用漏斗圈出嫌疑代码位置(定位)、然后把**精装案卷**递给一个外勤侦探(coding agent,如 opencode)去读码、写补丁、给根因。本次调研就为搞清三件事:**① 外勤侦探本机到底谁能请到?② 案卷怎么递、回执怎么收?③ 漏斗怎么搭?**

**三个决定性结论:**

1. **外勤本机只有 opencode 能请到**(v1.18.3 已装);omp/claude 都没装(omp 还装不上——github 墙 + bun)。→ **R2 v1 委托后端默认从"设计稿的 omp"改为 opencode**(抽象接口 `CodingAgentDelegate` 本就支持后端可换,只改默认值,完全在承诺范围内)。
2. **opencode `run --format json` 的 JSON 事件流是天然的回执格式**——harness 强制、与模型的"思考模式"无关,直接绕开"DeepSeek 思考模式不支持结构化产出"这个已踩过的坑。回执解析 = 订阅事件流 → 取最后一条 assistant 消息文本 → 用 R1 那套"喂 Schema + 解析 `{...}`"抠 JSON。
3. **定位漏斗 = Agentless 三段(file→function→line)的编排层移植到 Hyperion 已有的 code_index 上**;Hyperion 缺的全是"LLM 编排层"(skeleton 骨架化 / 行区间翻译 / sticky-scroll 行号化 / 几个 prompt 模板),检索底座(parser/chunker/embed/store/retrieval)已就绪且**强于** Agentless(多语言 + hybrid + cross-encoder rerank)。

---

## 1. 委托后端可行性(决定 v1 默认)—— ★实测★

| 后端 | 本机状态 | 能否 v1 用 |
|---|---|---|
| **opencode** | ✅ 已装 v1.18.3(npm 全局);`run`/`serve`/`acp`/`agent`/`mcp` 子命令齐全;已配 `Sisyphus` agent | **✅ v1 默认** |
| omp(oh-my-pi) | ❌ 未装;bun 也装不上(github 在本机被墙);源码在 `oh-my-pi/`(只读参考) | ❌ 本机不可用;留作可配置后端(网络/bun 通了再测) |
| claude code | ❌ CLI 不在 PATH(我虽是 Claude Code,但 `claude -p` 命令本身没装) | ❌ 需另装;留作可配置高档后端 |

**实测 opencode 契约(`opencode run --help` / `serve --help` / `agent list`,免费不调 LLM):**

`opencode run [message..]` 关键 flags:
- `--format json` —— **raw JSON 事件流**(默认 `default` 是格式化文本)。这是委托回执的载体。
- `--agent <name>` —— 指定子 agent(如本机的 `Sisyphus`)。
- `-m provider/model` —— 指定模型。
- `--dir <dir>` —— 工作目录(等价 omp `--cwd`)。
- `--variant high|max|minimal` —— provider 特定推理档位(对齐 T2L 的"Medium 思考档最佳"启示)。
- `--auto` —— 自动批准权限(无头必须;help 标注 `dangerous!`,即必须显式开)。
- `--attach http://host:port` —— 挂到运行中的 `serve`,避每次冷启动。
- `-c/--continue`、`-s/--session`、`--fork`、`--share` —— 会话管理。
- `-f/--file` —— 附加文件。

`opencode serve` 关键 flags:`--port`(默认 0 随机)、`--hostname`(默认 127.0.0.1)、`--mdns`、`--cors`。长连后端,R2 v1 先不上(用 `run` 一次性即可),记 backlog。

---

## 2. opencode delegate 契约(R2 v1 主实现依据)

**MVP 调用形态(单轮、一次性):**
```bash
opencode run --format json --auto --dir <repo> [-m provider/model] [--agent <name>] "<prompt>"
```
- stdout = 逐行 NDJSON 事件流(`message.delta` / `tool.call` / `tool.result` / `message` 等类型)。
- 回执解析:读事件流 → 取最后一条 `role:assistant` 消息的文本块 → 按委托契约(§3)抠 JSON。
- 完成判定:进程退出(退出码 0 = 正常)。
- 错误:stderr + 非零退出码。

**结构化产出怎么稳(复用 R1 已验方案):** opencode `--format json` 给的是**事件流**,不是"符合某 schema 的对象";最终 schema 对象仍要从 assistant 文本里抠。所以走 R1 memory/extract.py 那套**「提示词喂 JSON Schema(`Pydantic.model_json_schema()`)+ 模型直出 JSON + 手动抠 `{...}` 解析」**——已验证对 DeepSeek 思考模式可用(见记忆 `deepseek-structured-output-gotcha`)。**不**用 `with_structured_output`(思考模式连踩两坑)。

**反向 MCP(delegate 查 Hyperion 记忆):** opencode 原生 MCP client(`opencode mcp` 子命令)。在 opencode 配置里挂上 Hyperion 的 MCP server(`hyperion mcp serve`,R1 已实现),外勤就能现场 `memory_recall` 翻 Hyperion 笔记。这是 §6 反向 MCP 的落地通道。

---

## 3. omp delegate 契约(源码级,备用——本机暂不可跑)

> omp 本机未装,以下来自 `oh-my-pi/` 源码精读(agent A),非实跑。等 bun/网络通了再实测,届时可切回 omp 作后端。

- **无头**:`omp -p --yolo --no-session --cwd <repo> "<prompt>"`(`-p`=发完即退;`--yolo`=自动批准,无头必须;`--no-session`=不落盘)。
- **`--mode rpc`**:NDJSON-over-stdio 自定义协议(非 JSON-RPC),有 v1/v2。生命周期:`ready` 帧 → 客户端发 `prompt` → 收 `agent_end`(完成信号,带完整 messages)→ 发 `get_last_assistant_text` 拿最终文本 → 关 stdin 退出。大帧 v2 chunking(>1MiB 切片),客户端要实现 `RpcFrameDecoder` 重组。
- **结构化产出**:走 agent 定义文件(`.omp/agents/<name>.md` frontmatter 的 `output:` schema,支持 JTD/JSON Schema)+ 模型调内部 `yield` 工具提交 + 父端 `validateJsonSchemaValue` 强校验(strict 模式产出必合法)。**没有 CLI 级 `--output-schema`**。
- **反向暴露**:rpc 的 `set_host_tools` / `set_host_uri_schemes` 可让 omp 反调 Hyperion 工具(R3+ 钩子)。
- **已知坑**:`agent_end` 的 `isTerminal` 可能为 false(后台 async task);stdout 是协议通道严禁污染;大结果触发 chunking。

> 对照 opencode:opencode 的 `run --format json` 比 omp 的 agent.md+yield+rpc 链路**简单得多**,更适合 MVP。omp 的 strict schema 强校验是它的最大价值(待本机可用时切入)。

---

## 4. Agentless 分层定位漏斗(源码级,可移植映射)

> 来源:`agentless/agentless/fl/`(agent C 精读)。Agentless **完全不用 grep/ripgrep**——"搜索"全由 LLM 在三种 LLM-friendly 表示上完成:目录树(file-level)、libcst 骨架(function-level)、行号化代码窗口+sticky scroll(line-level)。

**三段式(逐字 prompt 可移植):**

| 层 | 输入 | 方法 | 关键参数 |
|---|---|---|---|
| **file-level** | problem + 目录树(无代码内容) | 双路:① LLM-only(喂目录树,要 ≤5 文件)② embedding(`similarity_top_k=100`)→ Counter 投票融合 | top_n=3 |
| **function-level** | file-level 的 top-3 文件 | libcst 骨架化(函数体→`...`)+ LLM 标 `class:/function:/variable:` | skeleton 30/10/10;MAX_CTX=128k;retry temp 0→1 |
| **line-level** | function-level 锚点 | 符号→行区间翻译 + sticky-scroll 行号化显示 + LLM rerank 标 `line:` | context_window=10 |

**关键移植点:**
- **sticky-scroll**(`line_wrap_content`):每个可见区间头部先打出当前行所有外层 `class`/`def` 作用域行——让 LLM 不丢"当前行属于谁",Agentless 论文里是 line-level 准确率的关键差异点。**必须移植。**
- **超长砍尾**:`while tokens(msg) >= 128000 and len(files)>1: 砍最后一个文件`——保证 prompt 不爆。
- **retry 策略**:第 1 次 temp=0,失败后 temp=1.0,最多 5 次。

**可移植到 code_index 的映射(结论):**

| Agentless 组件 | Hyperion 现状 | 复用度 |
|---|---|---|
| `parse_python_file`(正则解析)→ structure dict | `parser.py`(tree-sitter 抽 `Symbol`,多语言、更准) | **直接复用(更强)**,structure dict 换成 `list[Symbol]` |
| `EmbeddingIndex`(整文件/每符号一 Document + metadata) | `chunker.py`(符号边界切块 + `fts_text`,kind 字段) | **直接复用(更强)**,粒度对齐 |
| `VectorStoreIndex`(纯向量 top-100) | `store.py`+`retrieval.py`(LanceDB + BM25 + 向量 + RRF + cross-encoder rerank) | **直接复用(更强)**,hybrid 比纯向量稳 |
| `combine.Counter` 投票融合 | 无 | 需新建(小,~20 行) |
| `get_skeleton`(libcst,仅 Python) | 无 | **需新建**,基于 **tree-sitter**(多语言,优于 libcst) |
| `transfer_arb_locs_to_locs`(符号→行区间翻译) | 无 | 需新建(中等,~150 行,薄层:按 kind/name 在 `list[Symbol]` 查 start/end_line) |
| `line_wrap_content`(行号+区间+sticky scroll) | 无 | 需新建(中等,~80 行,纯文本工具,**差异化关键**) |
| `LLMFL` 四个定位方法(prompt+调LLM+retry+多采样) | 无 | **需新建(核心)**,prompt 逐字移植 |

> **总评:Hyperion 缺的全是"LLM 编排层",不是检索底座。** 这与 R2 路线"委托重活、自做召回+组装"完全吻合——定位漏斗的确定性部分(检索)已就绪,要补的是漏斗的 LLM rerank 编排。

---

## 5. deer-flow 委托形状(借 `SubagentResult` schema)

> 来源:`deer-flow/.../subagents/executor.py`(我直接 grep 补,子 agent 输出被中和)。deer-flow 的 `SubagentExecutor` 委托的是**内部** LangGraph 子 agent(非外部 coding agent),所以隔离 loop/后台 task 机制不照搬。**借的是 `SubagentResult` 的数据形状**。

`SubagentExecutor`(executor.py:395)关键形状:
- `SubagentStatus`(Enum,`is_terminal()` 判终态)→ 借作 delegate 的状态契约。
- `SubagentResult`(dataclass:final_text + status + token usage records + error)→ **直接借作 Hyperion `DelegateResult` schema**(最终文本 / 状态 / token / 错误四件套)。
- `execute()`(同步)/ `execute_async(task_id)`(异步,返回 task_id,可查/取消/列)→ 借作委托的生命周期管理形状(R2 v1 只做同步 `execute`,异步留 backlog)。
- `_extract_final_result(final_state)` / `_extract_llm_error_fallback()` → 借作"从 agent 末态抽最终结果 + 错误兜底"的思路。

**StateGraph 编排:** deer-flow workflow 定义在 backend 主代码(非 tests);Hyperion 七步 workflow 已在 bug-rca-design §1 定好 StateGraph 结构,实现时参照 deer-flow 的节点函数签名 + 状态 schema + checkpointer。

---

## 6. 前沿 SOTA 2025-2026(WebSearch,已核实)—— 5 条关键启示

> 来源:agent E(WebSearch + `git ls-remote`/arXiv 核实)。repo 全部 `git ls-remote` 核实存在;sst/opencode `8cbea4fb`、OpenAutoCoder/Agentless `5ce5888b`、T2L arXiv 2510.02389(ICML 2026)、Code Researcher arXiv 2506.11060。

### 6.1 委托通道:四协议分层,MCP 不做 agent↔agent 委托
- **ACP(Agent Client Protocol,Zed 2025-08,JSON-RPC 2.0 over stdin/stdout nd-JSON)** 是"editor/client ↔ coding agent"的标准——opencode 原生支持 `opencode acp`。最适合做 Hyperion 的"委托通道"。
- **MCP** 留给"把 Hyperion 的 memory/nav 工具暴露给 coding agent"(反向查记忆)。
- → **R2 委托通道:opencode `run --format json`(MVP 起点)→ `acp`(标准协议,后续);MCP 反向查记忆。双向分离,不搅在一起。**

### 6.2 bug-RCA 方法论锚点:T2L-Agent(arXiv 2510.02389)
- **ATA(Agentic Trace Analyzer)★核心**:把多种运行时证据(crash log / sanitizer / stack trace / GDB / 静态分析)**合并成单一 evidence block** 喂 LLM——让 LLM 做全局推理,而非逐证据聚合。crash 点常远离真因,ATA 专治"症状→根因行"鸿沟。
- **Divergence Tracing**:同一 evidence block 跑多条并行推理分支聚合排名(跨模块 bug 提升最大,Qwen3 +48.9pp)——低成本高收益差异化点。
- **Detection Refinement(粗→精两段)**:粗筛圈可疑区 → 抽相关代码追加回 evidence → 第二轮更新候选,直到无新候选或预算耗尽。
- **思考预算:Medium 最佳**(High 反而更差,过度探索+工具错误累积)——对齐记忆里 DeepSeek 思考模式踩坑。
- **量化**:去掉 ATA,GPT-5/Claude4 det/loc **双双跌到 0%**——没有运行时证据桥接,LLM 直接定位彻底失效。
- ⚠️ **澄清**:T2L-ARVO 基准**不含 bluez/wpa**(它评测 opensc/yara/php-src 等);bluez/wpa 是 **Hyperion 自己的 demo2**。二者问题形态(C/C++ 系统软件 + crash 证据 → 行级根因)同构,非同集。文档须明示,免被 reviewer 抓。

### 6.3 commit-log 因果推理:Code Researcher(arXiv 2506.11060)
- 首个"面向代码的 deep research agent",给系统软件 crash 生成缓解补丁。多步推理 over code 语义 + 代码模式 + **commit history**。
- **kBenchSyz(Linux kernel crash)CRR:Code Researcher 48% vs Agentless 31% vs SWE-agent 31.5%**(同 GPT-4o)——**纯 Agentless 漏斗在系统软件 crash 上效果差,commit-log 推理是关键增量。**
- → **commit-log(`git log`+blame+message 因果链)作为 evidence block 一等公民**(与 T2L 的 crash evidence 并列)。这是 Hyperion P2(RCA)与 P3(记忆)的天然交汇。

### 6.4 结构化产出:两段式 + 容错解析(复用 R1)
- 推理/思考模式与强结构化产出**互斥**(Anthropic 拒 `tool_choice`+thinking;Kimi 等 provider 同)。
- 最佳实践:① 两模型(推理模型自由思考 → 快非推理模型套 schema 强制);② **喂 Schema + 直出 JSON + 正则兜底抠 `{...}`**(Hyperion R1 已采);③ provider capability 表分支。
- → **R2 委托契约不依赖被委托方的强结构化能力;opencode `--format json` 事件流是 harness 强制契约,最稳。**

### 6.5 评测:自建 mini-bench,别碰 SWE-bench
- SWE-bench Verified(Python)2026 已 90%+ 饱和,且 UTBoost 研究 ~169 例误判(分数虚高)。
- 系统软件 C/C++ crash 远未饱和(Code Researcher kBenchSyz 仅 48%)——**Hyperion 赛道正确**。
- mini-swe-agent ~100 行 Python 在 SWE-bench Verified 拿 ~74% → **harness(ACI)比 agent loop 本身更重要**,学术背书"重活外包给成熟 coding agent"。
- → **R2 评测以 demo2 为金标准,仿 T2L-ARVO 的 crash-family 划分自建小评测**,不碰 SWE-bench。

---

## 7. demo2 金标准(对照靶子,已精读)

`example/demo2`:
- `journalctl_b.txt`(2.6MB wpa_supplicant 全量日志)
- `fix-p2p-scan-orphan-minimal.patch`(最小补丁,3 文件:scan.c/p2p_supplicant.c/p2p_supplicant.h)
- `WiFi扫描不到-P2P扫描孤儿根因分析报告.md`(271 行中文金标准报告)

**证据纪律(报告的"签名",R2 渲染器必须复刻):**
- 每条结论锚 **file:line**(`scan.c:2446`、`p2p_supplicant.c:294`)。
- 引用 C 原文(贴函数签名/关键分支)。
- 日志按**行号**(时间线表:时刻 → 行号 → 事件 → 含义;关键计数如"`Scan-only results received` 全日志仅 1 次")。
- 覆盖率用 **%**(minimal 补丁对日志回放覆盖率 100%)。
- 骨架:元数据表 → TL;DR → 环境/影响 → 现象 → **定位与根因(trigger-chain ASCII 图 + 引用 C + 日志时间线)** → 修复方案(方案对比表)→ 补丁+补丁分析(正确性/TOCTOU/覆盖率/兼容)→ 验证(编译/apply-revert/日志回放%)→ 风险评估与选型 → 复现&回归 → 附录(代码位置表/日志行号表/术语表/交付清单)。

**这个 bug 的特点(影响漏斗设计):**
- 根因是**并发覆写函数指针 → 误路由 → 泄漏**——跨文件(scan.c / p2p_supplicant.c / dbus_new_handlers.c / events.c)的因果链,单文件检索难命中。**正好需要 blast-radius(code-review-graph)+ trigger-chain 推理**,不是简单关键词命中。
- 触发证据在**日志**(行号 2228 并发 Interface.Scan、2452 误路由、3865 超时)——日志是 evidence block 的一等公民。

---

## 8. 对 R2 设计的修正决策(进实现前定稿)

| 决策 | 设计稿原值 | 调研后修正 | 理由 |
|---|---|---|---|
| **v1 默认委托后端** | omp | **opencode** | omp 本机装不上(github 墙+bun);opencode v1.18.3 已装;`--format json` 绕结构化坑。抽象接口本就支持可换 |
| 委托回执格式 | omp agent.md+yield | **opencode `run --format json` 事件流 → 取最后 assistant 文本 → 喂-Schema 抠 JSON** | harness 强制契约,不依赖模型思考模式 |
| 定位漏斗 | Agentless 三段(概念) | **三段编排层移植到 code_index;补 skeleton+行区间翻译+sticky-scroll+skeleton.py/loc_translate.py** | 检索底座已就绪,缺 LLM 编排层 |
| evidence block | 未细化 | **单一 evidence block**(日志摘要 + blast-radius + 相关代码片段 + 记忆召回 + commit-log 摘要),合并喂不逐条 | T2L ATA + Code Researcher 双背书 |
| 反向 MCP | §6 概念 | **opencode 原生 MCP client 挂 `hyperion mcp serve`** | R1 已实现 MCP server,直接复用 |
| 评测 | demo2 金标准 | demo2 + 仿 T2L-ARVO crash-family 自建 mini-bench;不碰 SWE-bench | Python 已饱和;系统软件是赛道 |

**未变(沿用设计稿):** 七步 workflow(ingest→recall→localize→assemble→delegate→verify→report+memorize)、`CodingAgentDelegate` 抽象接口、结构化产出契约 JSON 字段(bug-rca-design §4)、报告骨架(§5)、v1 单轮单委托不做多 candidate。

**新增 backlog(R2 后/R5):** opencode `serve` 长连 + `--attach` 避冷启动;`acp` 标准协议委托通道;委托异步 task 管理(借 deer-flow execute_async);Divergence Tracing 多分支并行;omp 后端(网络通了);claude 后端(装了 CLI 后);commit-log 因果推理作 evidence block 一等公民(Code Researcher);自建 mini-bench crash-family。

---

## 9. 待实测项(R2 实现中边做边验)

- [ ] opencode `run --format json` 的**真实事件流字段**(跑一个最小例子,确认最后 assistant 文本在哪类事件的哪个字段)。
- [ ] opencode `--auto` 在无 DEEPSEEK key 时的行为(确认 opencode 用哪个 provider;可能要给它配 provider)。
- [ ] opencode MCP client 挂 Hyperion MCP server 的配置方式(`opencode mcp` 怎么加 server)。
- [ ] Agentless 漏斗移植后,对 demo2 wpa 的 file-level 召回是否命中(scan.c/p2p_supplicant.c)——wpa 是 C,**tree-sitter-c grammar 要补**(R1 只装了 Python)。
