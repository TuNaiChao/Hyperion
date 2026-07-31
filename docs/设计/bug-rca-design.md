# bug 根因定位工作流 — 设计文档(P2,★MVP)

> 状态:R2 MVP ✅ → R3.1 迭代 verify-refine(B)✅ → **2026-07-31 简化:砍 Hyperion 侧定位漏斗,改"工具驱动委托"**(opencode 自主定位,Hyperion 把差异化能力做成 MCP 工具给它调)。当前真相见 **§1(流程)/ §6(反向 MCP 工具)/ §7.6(verify-refine)**。
> 上位文档:[architecture.md §6](architecture.md) · 金标准:`example/demo1`、`example/demo2`

> ⚠️ **2026-07-31 架构简化(本版核心变更):** 之前 Hyperion 自己跑一套 Agentless 式定位漏斗(file→function→line)再喂给 opencode —— 这与 opencode **重复定位**(double localization)。现已**砍掉该漏斗**,改为:**opencode 自主定位**,Hyperion 把自己真正差异化、且 opencode 自己搞不便宜的能力(**记忆 recall / code_index 廉价语义检索 / 日志过滤**)做成 **MCP 工具**,opencode 经 MCP 按需调用,prompt/skill 提示**优先用这些工具缩范围**(省 token)。Hyperion 聚焦:接案调度 + 能力工具 + 验证门控 + 报告 + 记忆沉淀。详见 §2(为什么砍)/ §6(工具)。**opencode 对此原生支持**(已核实源码,见 §6)。

---

## 0. 这是什么 / 为什么(面向小白)

**类比:Hyperion 是"接案调度员 + 情报库",opencode 是"外勤侦探"。**

你给 Hyperion 一份**案卷** = 源码 + 一份**线索**(出问题的**日志**,或一份**漏洞报告**)。Hyperion **不自己去翻每一行代码**——定位/改码是成熟 coding agent(opencode)最擅长的通用能力,自建会烧光预算。Hyperion 做它真正差异化的几件事:

1. **搭工位 + 立案**(`ingest`):给外勤一个独立 workspace(全量代码 + 问题 + 契约),不污染原仓。
2. **提供情报工具**(反向 MCP,§6):把自己的**记忆**(历史 bug 教训)、**code_index 廉价检索**(语义找代码,比 grep 又准又省)、**日志过滤**做成工具,外勤现场按需查。
3. **调度 + 验活**:派外勤定位→修复;**validate_patch 执行硬门控**(git apply --check,非 LLM);verify-refine 同会话迭代。
4. **写报告 + 记笔记**:金标准骨架中文报告;根因+修法沉淀进记忆(下次同类 recall 命中)。

**为什么不自己干定位(opencode 干):** 定位/改码是 opencode 的强项,Hyperion 自建一套漏斗是**重复劳动**(且费 token)。**Hyperion 的差异化在:记忆 + 廉价检索 + 日志过滤 + 调度 + 验证 + 报告**——这些做成工具给 opencode 用,比自己重造更值。这正是 2026 主流(deer-flow 2.0 / Claude Code / OpenHands 都是"lead agent + 工具 + 子 agent",不重造能力)。

> 📌 **workspace 演进(2026-07-29 定稿,见 [workspace-design.md](workspace-design.md))**:每 bug 一个专用目录(七段:code/triggers/delegate/artifacts/patch/report/docs),opencode `--dir` 指此;补丁 = **git diff 观察 workspace/code 改动**(根治 LLM 吐 diff off-by-one);**Tier0 验证**(`git apply --check`/revert;编译/F2P/P2P 门控构建环境 R5)。隔离默认本地(R2/R3)、Docker R5。

---

## 1. 工作流(StateGraph;2026-07-31 简化为五步)

> 旧版八步(ingest→recall→localize→assemble_localize→delegate_localize_loop→assemble_repair→delegate_repair_loop→report)里,**recall / localize / assemble_localize 三步是 Hyperion 自己跑的定位漏斗**——与 opencode 重复,已**砍掉合并**。现在 opencode 在定位阶段自主完成(调 Hyperion 的 MCP 工具缩范围),Hyperion 只负责调度 + 验证 + 报告 + 记忆。

```
START → ingest
   → [delegate_localize_loop]   阶段① 定位:opencode 自主定位(调 hyperion_recall/search_codebase/filter_logs 工具)
                                  + 证伪式自审 verdict → 没把握就 --continue 同会话重定位(max K1)
   → assemble_repair
   → [delegate_repair_loop]     阶段② 修复:opencode edit code/ → git diff 观察补丁 → validate_patch 门控
                                  → 没过就 --continue 同会话再修(max K2)
   → report_memorize ──► END
```

| 步 | 中文名 | 干啥(大白话) |
|---|---|---|
| **1 ingest** | 立案 | 接案,给外勤搭独立工位(workspace),放全量代码 + 问题描述(PDF/md/txt 经 `trigger_parser.parse_issue` 转纯文本)+ 契约,不污染原仓 |
| **2 delegate_localize_loop** | 派外勤定位(+自审) | opencode 定位根因;**优先调 Hyperion 工具缩范围**(recall 翻记忆、search_codebase 语义找代码、filter_logs 筛日志);**证伪式自审**(verdict);没把握就同会话重定位 |
| **3 assemble_repair** | 组修复卷宗 | 锁死的根因 + evidence 代码片段 + 修复契约 |
| **4 delegate_repair_loop** | 派外勤修复(+验活) | opencode 直接改 code/;**git diff 观察补丁 + `validate_patch` 门控**;没过就同会话再修 |
| **5 report+memorize** | 写报告+记笔记 | 金标准骨架中文报告;根因+修法沉淀进记忆 |

> 一句话:**立案→[定位+自审]→[修复+验活]→写报告+记笔记**。Hyperion 全程调度 + 提供情报工具;重活(读码/改码/定位)委托 opencode;**同一 opencode 会话贯穿定位+修复**(`--continue` 链,复用上下文免冷启动)。

**技术细节:**

| 步 | 动作 | 关键点 |
|---|---|---|
| **1 ingest** | 问题描述(PDF/md/txt)→ `parse_issue` 转纯文本;建 workspace | 见 [workspace-design.md](workspace-design.md) §5 |
| **2 delegate_localize_loop** | `delegate.run(prompt, cwd, schema=LOCALIZE_SCHEMA, agent="hyperion-localize", continue_session=...)`;opencode 经 MCP 调 `hyperion_recall`/`hyperion_search_codebase`/`hyperion_filter_logs`(prompt 提示优先用) | verify-refine 双循环细节见 §7.6;工具见 §6 |
| **3 assemble_repair** | 组装修复 prompt = 锁死根因 + evidence 片段 + REPAIR_SCHEMA | 手术刀级——只喂相关片段 |
| **4 delegate_repair_loop** | opencode edit code/;git diff 观察 patch;`validate_patch` Tier0 门控 | verdict 自审 + 执行硬门控,见 §7.6 |
| **5 report+memorize** | demo 骨架渲染中文报告;抽 `BugLesson` 入记忆 | 闭环 |

---

## 2. 为什么砍掉 Hyperion 侧定位漏斗(改工具驱动)

> 旧版 §2 是"Agentless 分层定位漏斗(file→function→line),Hyperion 在委托前跑"。**已砍**。原因 + 替代方案:

**为什么砍:**
1. **双重定位冗余**:opencode 本就是强定位 agent(自带 grep/read/edit + ACI);Hyperion 再跑一套漏斗圈锚点,opencode 来了还是重读重定位 → 白花一套漏斗的 token/时间。这是 2026-07-31 全流程体检 + 调研的核心发现。
2. **2026 共识 = 工具驱动**:Claude Code / OpenHands / deer-flow 2.0 全是"lead agent + 工具 + 子 agent",**不重造能力、不写死固定管线**。能力做成工具,agent 自决何时调(deer-flow 2.0 自己就从固定 planner/reporter 图迁到了单 lead agent + `task` 工具,本地代码实锤)。
3. **Hyperion 的护城河不是"重做定位"**:而是 opencode **缺/搞不便宜**的三样——**记忆**(跨会话,opencode 没有)、**code_index 廉价检索**(BM25+向量,opencode 只能 grep 烧 frontier 模型 turn)、**日志过滤**(opencode 读 16K 原始日志贵)。把这些做成工具,价值才体现。

**替代方案(Agentless 思想不丢,落到工具里):**
- 调研铁证:Agentless(固定漏斗,$0.34/issue)比 SWE-agent(agent loop,~$2)**便宜 ~6×**;**skeleton > 整文件**(lost-in-the-middle)。这条没变。
- 但现在这"廉价手术级检索"做成 **`hyperion_search_codebase` 工具**:opencode 调它拿**紧凑的真实符号锚点**(带 file:line provenance),而不是自己 grep 整库。**既守住了 Agentless 的省 token 红利,又不重复 opencode 的活。**
- 关键:**抽概念不抽标识符**(§6 防幻觉契约)——opencode 的查询经工具回**真实存在的符号**(validated against index),幻觉不出不存在的函数。

> **token 取舍提示:** 永远相关的廉价件(召回的历史教训)也可考虑**预进 prompt**(0 决策 turn,比工具调用省一次 turn);重/钻取件(深检索、日志过滤)做工具按需调。这是 Anthropic hybrid(CLAUDE.md 预取 + glob/grep 工具)。当前先全做工具(更纯粹、贯彻"工具>固定流程"),后续按实测调。

---

## 3. 委托抽象 `CodingAgentDelegate`(R2 实现,不变)

```python
# src/hyperion/tools/delegate.py(R2 实现)
class CodingAgentDelegate(abc.ABC):
    @abc.abstractmethod
    async def run(self, prompt, cwd, output_schema=None, *, timeout=None,
                  agent=None, continue_session=False) -> DelegateResult: ...
    #   continue_session=True → opencode run --continue 续同 cwd 最近 session(verify-refine 双循环的承载)
    #   agent → 指定子 agent(hyperion-localize/repair)

class OpencodeDelegate(CodingAgentDelegate):  # ★ v1 默认(本机已装 v1.18)
    # opencode run --format json --auto --dir <repo> [--continue] [--agent <name>] "<prompt>"
    # → 流式解析 NDJSON 事件流 → 聚 assistant 文本 → 喂 Schema 抠 JSON
    ...
class OmpDelegate(CodingAgentDelegate):       # 备选(本机暂未装)
    ...
class ClaudeDelegate(CodingAgentDelegate):    # 可选高档后端(需另装 claude CLI)
    ...
```

**配置切换:** `config.yaml` 的 `delegate.backend: opencode | omp | claude`。抽象接口从第一天起,三后端可换。

**v1 默认 opencode** 的理由:**本机已装 v1.18.3**(omp 因 github 墙 + bun 装不上);`run --format json` 事件流是 harness 强制、与模型思考模式无关的结构化回执载体,绕开"DeepSeek 思考模式不支持结构化产出"坑(见记忆 `deepseek-structured-output-gotcha`);**Go 单二进制 + 原生 MCP client**(挂 `hyperion mcp serve`,§6);`--variant` 给 provider 特定推理档。**omp** 待本机装好后切回(strict schema 强校验是其最大价值);**claude** 需另装 CLI。

> 精确无头参数已实测(`opencode run/serve --help`,见 [r2-bug-rca-research.md §1-2](../调研/r2-bug-rca-research.md))。

---

## 4. 结构化产出契约(两阶段;R3.1 verify-refine)

> 拆两阶段(对齐 `nodes.py` `LOCALIZE_SCHEMA`/`REPAIR_SCHEMA`),各带 **`verdict` + `falsification`**(证伪式自审,见 §7.6)。补丁**不在 JSON 里**——repair 直接 edit `code/`,Hyperion 用 git diff 观察。

**① LOCALIZE_SCHEMA**(只定位,禁补丁):
```json
{
  "root_cause": "scan_only_handler 收到本该给 p2p 的扫描结果,未释放 p2p_scan_work → 孤儿 radio work 阻塞所有站点扫描",
  "trigger_chain": ["① p2p-scan 启动", "② 并发 Interface.Scan 覆写 handler", "③ 结果误路由到 scan_only_handler", "④ 不释放 p2p_scan_work → 孤儿"],
  "evidence": [{"file": "wpa_supplicant/scan.c", "line": 2446, "snippet": "...", "why": "..."}],
  "blast_radius_files": ["wpa_supplicant/scan.c", "wpa_supplicant/p2p_supplicant.c"],
  "verdict": "confirmed",
  "falsification": "查了 radio_work_free 调用链,无反例"
}
```

**② REPAIR_SCHEMA**(根因已锁,直接 edit code/):
```json
{
  "confidence": 0.9,
  "verdict": "verified",
  "falsification": "re-read 改动,确认释放 p2p_scan_work 且无新问题"
}
```
补丁(patch)= Hyperion 用 `git diff --cached` 观察 `workspace/code/` 改动生成(行号/格式天然对),**不信任 delegate 吐的 diff**(根治 R2 off-by-one)。

**工具面:** delegate 用 opencode 内置 edit/grep/read 工具 + Hyperion MCP 工具(§6)。设计原则参考 [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) ACI:输出上限 + 编辑守卫(NeurIPS 2024)。

---

## 5. 报告渲染(基于 demo 金标准骨架)

> 金标准:`example/demo1`(漏洞 PDF 驱动)、`example/demo2`(日志驱动,271 行)。**证据纪律是签名:每条结论锚 file:line、原文引用 C、日志按行号、覆盖率用 %。**

中文报告骨架(渲染器按此填):
```
元数据表(编号/等级/组件/时间窗/产出补丁)
→ TL;DR(现象+根因+修复,3-5 条)
→ 环境/影响
→ 现象
→ 定位与根因
    ├ trigger-chain 图(ASCII/编号,内嵌 file:line)
    ├ 引用 C 原文(file:line)
    └ (日志驱动时)日志时间线表(时间 → 日志行号 → 事件 → 含义)
→ 修复方案(方案对比表)
→ 补丁 + 补丁分析(正确性/TOCTOU/覆盖率/兼容性 表)
→ 验证(编译 / apply-revert / PoC 或日志回放覆盖率%)
→ 集成方式 / 风险评估与选型
→ 复现 & 回归用例
→ 附录(代码位置表 / 日志行号表 / 术语表 / 交付清单)
```

> 借 deer-flow 2.0 报告的"证据/引用前置(放第 2 位)"防幻觉规则:证据靠前摆,别埋文末,模型不易编造来源。

---

## 6. 反向 MCP —— Hyperion 能力作工具给 opencode(★当前真相)

> **不是 MCP 驱动 delegate,而是 delegate(opencode)查 Hyperion。** opencode 干活时,经 MCP 调 Hyperion 的差异化能力工具。2026-07-31 经源码核实:**opencode 原生支持**(MCP 一等公民 + 自定义 agent prompt/工具白名单/steps + `--continue` 续会话)。

### 6.1 三个 MCP 工具( Hyperion 暴露)

| 工具(MCP 名 `hyperion_*`) | 包 | 作用 | 防幻觉 |
|---|---|---|---|
| **`hyperion_recall`** | `tools/mcp_memory.py`(R1 已有) | 翻历史记忆(同类 bug 教训) | — |
| **`hyperion_search_codebase`** | 包 `code_index.retrieve`(BM25+向量+RRF+rerank) | 语义+符号找代码,回**紧凑锚点**(file:symbol:line + 片段) | **只回索引里真实存在的符号**(validated against `parser.py` Symbol 表);**抽概念不抽标识符** → 幻觉结构上不可能 |
| **`hyperion_filter_logs`** | 包 `trigger_parser.filter_log_window` | 大日志按关键字∩时间窗过滤 → 有界摘录(原始全量日志路径仍给 opencode 可回查) | — |

入口:`hyperion mcp serve`(扩展现有 `tools/mcp_memory.py` 加后两个工具)。

### 6.2 opencode 侧接线(已核实)

- **`config/opencode_hyperion.json` 加 `mcp` 段**(stdio 最简,免 daemon):
  ```jsonc
  "mcp": { "hyperion": { "type":"local",
    "command":["hyperion","mcp","serve","--codebase","{env:HYPERION_CWD}"],
    "enabled":true, "timeout":30000 } }
  ```
  (`delegate.py` 已注入 `OPENCODE_CONFIG` 指此文件;要长驻 daemon 用 `"type":"remote"`,`url:"http://127.0.0.1:PORT/mcp"`。)
- **工具名带前缀**:opencode 把 MCP 工具注册成 `hyperion_search_codebase`(server名_工具名)——prompt 里得用全名。
- **agent prompt 加 nudge**(`hyperion-localize.prompt` 任意文本,注入系统消息首位):*"定位前先调 `hyperion_search_codebase`(语义找代码)和 `hyperion_recall`(翻历史教训);大日志用 `hyperion_filter_logs` 粗筛。比 grep 更准更省。"*
- **permission 放行** `"hyperion*":"allow"`(headless `--auto` 不卡)。
- **(可选,3 行)** `delegate.py:_parse_stream` 加收 `tool_use` 事件(`part.tool`/`part.state.input`/`output`)→ 审计 opencode 调了哪些工具(数据已在 NDJSON 流 + `delegate_log` 里)。

### 6.3 opencode 能力核实结论(本地源码 file:line)

- MCP 一等公民:`packages/opencode/src/mcp/index.ts` + `catalog.ts:42-63`(MCP 工具注册成 `dynamicTool`,像原生 read/grep 一样 mid-session 可调)。
- 传输:stdio(`mcp/index.ts:354`)+ remote HTTP/SSE(`:243`)。
- 自定义 agent:`opencode_hyperion.json` `agent` 段(prompt/permission/steps)——Hyperion 已在用。
- 工具调用进 `--format json` 事件流:`tool_use` 带 `part.state.input`(run.ts:715-727)。
- `--continue` 跨 localize→repair 续会话:全量历史 + 工具结果保留(run.ts:456-533)。
- ⚠ **坑**:别开 `experimentalCodeMode`(塌缩 MCP 工具成单 `execute`);MCP 调用留在 primary agent(别塞 `task` 子 agent,其 tool_use 不进流,issue #33397);`listTools` 默认 5s 超时,长检索要流式进度。

---

## 7. v1 取舍(用户:第一版别太复杂)

**做(R2 MVP):** 结构化 JSON 契约、直出报告、记忆沉淀。
**多阶段(2026-07-30,见 §7.5):** delegate 拆 localize + repair 两阶段(解 glm-5.2 单 loop 不收敛)。
**verify-refine(B,R3.1,见 §7.6):** 同会话双循环 + 证伪自审 + `validate_patch` 硬门控;**patch 多候选投票 rerank 于 2026-07-31 整体移除**(无 oracle 平凡白烧)。
**工具驱动简化(2026-07-31,本版):** 砍 Hyperion 侧定位漏斗,改 opencode 自主定位 + MCP 工具(§6)。
**不做(放 R5):** 自动 PoC 生成、多委托后端并发、Tier2 跨模型对抗审。

**L3/DAP(ChatDBG 式 reproduce-then-debug):** 仅当有**可复现 bug / core dump** 时才接;**事后日志分析不适用**。放 R3+/R5。

---

## 7.5 多阶段委托(2026-07-30 演进,解 glm-5.2 单 loop 不收敛)

> R2 MVP 是单次复合委托。实测 glm-5.2 单 loop 跑 97K token 全工具调用,最后 prose「让我阅读...」**不收敛**。**改多阶段委托**(调研 Agentless + MASAI)。
> ⚠️ **2026-07-31 更新:** 本节原描述"Hyperion 跑 Agentless 三阶段漏斗"——该漏斗**已砍**(§2),opencode 现自主定位(调 §6 工具)。但**多阶段拆分(localize 阶段 vs repair 阶段)+ MASAI 式"子 agent 靠 input/output 串接不对话"的思想保留**;verify-refine 循环机制见 §7.6(当前真相)。

### 为什么(调研铁证,仍然成立)
- **Agentless**(arXiv 2407.01489)同模型 GPT-4o:**分阶段 32%/$0.70/78K** vs SWE-agent 单 loop 18.3%/$2.53/**498K**。分阶段质量/成本/token 三项全胜。
- 消融:**skeleton(58% 命中)完胜整文件(53.7%)** = lost-in-the-middle(arXiv 2307.03172)。glm-5.2 内联大片代码过载 = 不收敛根因。
- Agentless 论文列 agent loop 三大失败(复杂工具/决策失控/自我反思有限)精准命中 glm-5.2 症状。

### 每阶段产出契约(短、目标单一)—— 保留
- **① localize**:`{root_cause, trigger_chain[], evidence[{file,line,snippet,why}], blast_radius_files[], verdict, falsification}` —— **无 patch**(禁补丁)。opencode 用 §6 工具自主产。
- **② repair**:delegate **直接 edit code/**(补丁由 git diff 观察);schema `{confidence, verdict, falsification}`。
- **verify**:`validate_patch` Tier0(Hyperion 自跑,无 LLM)并入 repair loop 门控(§7.6)。

### 验证分层(补丁正确性)—— 保留
- **Tier 0 确定性**(零 LLM,R3.1 已落):`git apply --check`(+ `--3way`/`patch -p1` 降级)+ reverse `--check`(证必要)。**编译 / F2P / P2P 门控构建环境(R5)**(wpa/bluez build 是硬前提)。
- **Tier 1 执行信号**:repro test / 日志符号化 repro(需 #50 / 构建环境)。
- ⚠️ **无测试套件的 C 仓**:Tier 1 落不了地 → 日志/堆栈符号化 repro 替代(addr2line:patch 后 panic 条件是否不再可达)。**诚实准确率**:report 必标 **METR 警示**([metr.org](https://metr.org/notes/2026-03-10-many-swe-bench-passing-prs-would-not-be-merged-into-main/):~半数 test-passing PR 不会被合 → 测试是必要非充分)。

### 关键参考
[Agentless](https://github.com/openautocoder/agentless)(arXiv 2407.01489)· [MASAI](https://masai-dev-agent.github.io/)(arXiv 2406.11638)· [Lost in the Middle](https://arxiv.org/abs/2307.03172)。

---

## 7.6 迭代 verify-refine(B,R3.1 #54-rework)— 当前真相

> 主路径 = **迭代 verify-refine(B)**:同一 opencode 会话内反复「自审 → 不行就重试」,弃无脑多采样投票。**patch 多候选投票(rerank / majority voting)已于 2026-07-31 整体移除**(无 oracle + 模型近确定性 → 平凡 + N× token 白烧;现代 SOTA 转单轨迹+执行验证)。

### B 设计(同会话双循环,Hyperion 外循环驱动)
```
ingest
  → [delegate_localize_loop]  iter0 新 session → opencode 用 §6 工具定位 → 读 verdict(confirmed?)
                              → 否则带 falsification 反馈 --continue 重定位(max K1)
  → assemble_repair
  → [delegate_repair_loop]    iter0 --continue 续同 session → edit code/ → git diff 观察 patch
                              → validate_patch 门控 → 否则带 gate.log 反馈 --continue 再修(max K2)
  → report_memorize
```
- **同会话** = delegate `--continue` 链(已核查:`run.ts:492` + `prompt.ts:672-689` —— `--continue` 按 sessionID 复用全量历史,与 `--agent` 正交;两 agent 须 `mode: primary`)。per-bug workspace 唯一 cwd → session 隔离。
- **verdict 由 opencode 自审产出(B)**:schema 加 `verdict` + `falsification`;agent prompt 加证伪自审指令,对抗 confirm-own-error 偏差。
- **执行硬门控(Hyperion 侧,非 LLM)**:`validate_patch` Tier0 apply/revert —— 是 test、不是 critic,不违反 B。
- **收敛不破**:每 delegate call 仍 `steps` 限制 + 单 schema;外循环 max-loop 兜底。**只在 `verdict=needs_revisit`(干净返回)时重试**;infra 错误(timeout/error/schema 抽不出)直接跳出,不 `--continue` 破损 session。

### rerank 要啥条件才使得上劲(面向小白)
**rerank / 多采样投票** = 类比"一道难题请 5 个人独立做,选出现最多的答案"。要有效得先有**裁判(oracle)**能客观判对错:① 测试套件(wpa hwsim / bluez unit);② repro 复现(#50)。Agentless 是 **filter(先过 oracle 筛)+ vote(再取多数)**,没裁判 filter 做不了,光投票 = 瞎蒙。
**为啥移除**:wpa/bluez 现在**没裁判**(hwsim/bluez unit 要搭构建环境 R5、#50 repro 没做)+ glm-5.2 近确定性(N 个补丁几乎一样)→ filter 做不了、vote 平凡 → N× token 白烧。留"默认关"兜底 = 扛死代码 + 和主路径哲学矛盾。故 `rerank.py`/`RerankConfig`/`_rerank_fallback`/state candidates 全删。
**以后再加**:只在真有 oracle 时(构建环境就绪 / repro 落地),按 Agentless filter+vote 重写(~40 行,git 史可查),**不预建**(YAGNI)。

### 落地
- **R3.1(当前)**:`node_delegate_localize_loop` + `node_delegate_repair_loop`;config `delegate.max_localize_loops/max_repair_loops`(默认 2);loop 耗尽 K2 仍未过 → 取末次 patch + `verified=False`。
- **2026-07-31 移除项**:`rerank.py`、`_rerank_fallback`、`RerankerConfig`、`delegate.rerank` 配置、state `candidates`/`rerank_summary`、rerank fallback 测试。**检索 rerank**(`memory.native.rerank` cross-encoder)是不同机制,保留。

### 关键参考
[Self-Refine](https://arxiv.org/abs/2303.17651)· [Reflexion](https://arxiv.org/abs/2303.11366)· [SWE-Search](https://arxiv.org/abs/2410.20285)· [Agentless](https://arxiv.org/abs/2407.01489)· [METR 警示](https://metr.org/notes/2026-03-10-many-swe-bench-passing-prs-would-not-be-merged-into-main/)。

---

## 8. R2 退出标准(★MVP 金标准)— ✅ 2026-07-30 达标

1. `uv run hyperion bug-rca --repo example/demo2/wpa --log example/demo2/journalctl_b.txt` → 产出 `.patch` + `.md`。
2. 人工对照 `example/demo2`:报告骨架一致、证据纪律达标(每结论锚 file:line、日志按行号)。
3. demo1(漏洞 PDF 触发)同理跑通。
4. 产出抽成 `BugLesson` 入记忆;再跑同类问题能 `recall` 命中。

## 9. 待办(记 backlog)

- **§6 工具落地**(下一步):`hyperion mcp serve` 加 `search_codebase`/`filter_logs`;`opencode_hyperion.json` 加 `mcp` 段 + agent prompt nudge + permission 放行;`_parse_stream` 审计 `tool_use`。
- 砍旧漏斗:`workflows/bug_rca/localize.py` + `nodes.py` 的 recall/localize/assemble_localize 节点移除/合并(改 delegate_localize_loop 内 opencode 自主定位)。
- omp `--mode rpc` / opencode `serve` 长连委托通道(R3.3/#55)。
- 构建环境就绪后的 Tier1(测试/repro)+ 届时再评估 Agentless filter+vote(不预建)。
- L3 DAP(ChatDBG 式,可复现 bug)、Tier2 跨模型对抗审(R5)。
