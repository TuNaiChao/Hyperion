# bug 根因定位工作流 — 设计文档(P2,★MVP)

> 状态:设计稿 v1(2026-07-28)· 实现阶段:**R2(MVP)**
> 上位文档:[architecture.md §6](architecture.md) · 金标准:`example/demo1`、`example/demo2`

> ⚠️ **R2 调研后修正(2026-07-29):委托后端默认从 omp 改为 opencode。** 调研发现本机 omp 未装且装不上(github 墙 + bun),opencode v1.18.3 已装、子命令齐全、`run --format json` 事件流天然绕开"思考模式不支持结构化产出"坑。**`CodingAgentDelegate` 抽象接口不变**(后端本就可换),仅默认值改。完整调研见 [r2-bug-rca-research.md](../调研/r2-bug-rca-research.md)。下方 §3 的接口示例与选型表保留 omp 作可配置后端,但 **★ v1 默认 = opencode**。

---

## 0. 这是什么 / 为什么委托(面向小白)

**类比:Hyperion 是"接案调度员",omp/opencode 是"外勤侦探"。**

你给 Hyperion 一份**案卷** = 源码 + 一份**线索**(出问题时的**日志**,或一份**漏洞报告**)。Hyperion 不自己去翻每一行代码——它做三件它擅长的事:

1. **翻笔记本**(记忆):"这库之前有没有类似的 bug?当时怎么修的?"——命中就直接复用经验。
2. **画范围**(定位漏斗 + 结构图):用 Agentless 式分层定位(file→function→line)+ code-review-graph 的 blast-radius,圈出"问题大概在这几个函数"。
3. **派外勤**(委托):把**手术刀级上下文**(症状 + 圈定的代码片段 + 历史教训 + 严格产出契约)递给 omp/opencode,让它读代码、写补丁、给根因,**结构化返回**。

外勤回来后,Hyperion **验活**(可选二次委托:编译/apply)、**写报告**(按 demo 金标准骨架渲染中文报告)、**记笔记**(把这次根因+修法沉淀进记忆)。

**为什么不自己干定位/补丁:** 那是成熟 coding agent 最擅长的通用能力,自建会烧光一人/数月预算。**Hyperion 的差异化在记忆 + 调度 + 团队知识。** 把通用能力外包,自己聚焦差异化。

> 🆕 **workspace 演进(2026-07-29 定稿,见 [workspace-design.md](workspace-design.md))**:R2 MVP 先用「localize 锚点**内联进 prompt** + `--dir <repo>`」跑通;**R2 末起演进为 workspace 模型**——每 bug 一个专用目录(`<repo>__<bug-id>__<hash>/`,七段:code/triggers/delegate/artifacts/patch/report/docs),opencode `--dir` 指此,**读全量代码 + 日志**(非内联片段,解决"补丁易错位/日志没法结合");assemble 改**方式 B 指引 prompt**(给 file:line 嫌疑起点,不内联代码,opencode 自读);**大日志 Hyperion 粗筛(grep+时间窗+addr2line)+ opencode 深挖**;补丁走 **6 步验证**(`git apply --check`/revert/build/FAIL_TO_PASS/PASS_TO_PASS/rerank)。隔离默认本地(R2/R3)、Docker R5;沙箱抽象复用 deer-flow `Sandbox`/`SandboxProvider` + `workspace_changes`。

---

## 1. 工作流七步(StateGraph,R2 实现)

```
START → 1.ingest ─┐
                   ▼
            2.recall ──────────► 翻记忆 + 取 blast-radius
                   ▼
            3.localize ────────► Agentless 漏斗(file→function→line)+ code-review-graph
                   ▼
            4.assemble ────────► 组装手术刀级提示词 + 结构化产出契约
                   ▼
            5.delegate ────────► CodingAgentDelegate.run() → StructuredResult(JSON)
                   ▼
            6.verify(可选)────► 二次委托:编译 / apply / revert
                   ▼
            7.report + memorize► 渲染中文报告(§5)+ 抽 BugLesson 入记忆 → END
```

**面向小白 ——「接案调度员(Hyperion)+ 外勤侦探(opencode)」类比:**

| 步 | 中文名 | 干啥(大白话) |
|---|---|---|
| **1 ingest** | 立案 | 接案,给外勤搭独立工位(workspace),放全量代码 + 问题描述 + 契约,不污染原仓库 |
| **2 recall** | 翻笔记本 | "这库以前有没有类似 bug?当时怎么修的?"命中就复用经验,省推导 |
| **3 localize** | 画范围 | 不让外勤整库乱翻,先用确定性漏斗圈出"嫌疑大概在这几个函数" |
| **4 assemble** | 组卷宗 | 把案卷递给外勤:症状 + 嫌疑起点指引 + 历史教训 + 产出契约 |
| **5 delegate** | 派外勤 | opencode 在 workspace 自己读码、定位根因、改文件修 bug |
| **6 verify** | 验活 | 检查补丁:改对没、能不能打上 |
| **7 report+memorize** | 写报告+记笔记 | 按金标准骨架写中文报告;把根因+修法沉淀进记忆(下次复用) |

> 一句话:**立案 → 翻笔记本 → 画范围 → 组卷宗 → 派外勤 → 验活 → 写报告+记笔记**。Hyperion 全程是调度员(组装精确上下文 + 调度 + 沉淀),重活(读码/改码)委托给 opencode;记忆贯穿始终(接案 recall 复用、结案 memorize 沉淀)。

**技术细节:**

| 步 | 动作 | 关键点 |
|---|---|---|
| **1 ingest** | 确保源码已索引;**读问题描述**(文本/txt/md/pdf,ingest 解析成统一文本)→ **抽关键字**(错误码/函数名/符号/症状,写 `triggers/keywords.json`)→ 关键字**驱动日志预筛 + 代码 localize**(见 [workspace-design.md](workspace-design.md) §5) | 日志:文本行;问题描述:文本/txt/md/pdf |
| **2 recall** | 记忆召回该库相关历史 bug/教训;code-review-graph 取 trigger 指向模块的 blast-radius | 命中"见过"→ 直接走首解路径,省推导 |
| **3 localize** | **Agentless 分层定位漏斗**:file→class→function→line(embedding + LLM rerank) | 确定性预筛,产 `file:line` 锚点;不靠 agent 自由探索 |
| **4 assemble** | 组装提示词 = 症状 + 精确代码片段(blast-radius 内)+ 相关历史教训 + **严格产出契约**(§4 JSON) | 手术刀级——只喂相关片段,不整库 dump(省 token) |
| **5 delegate** | `CodingAgentDelegate.run(prompt, cwd, schema)`;v1 默认 omp | 结构化 JSON 返回,不解析 prose |
| **6 verify** | 可选二次委托:在沙箱编译 / `git apply` / `git revert` | 验补丁可应用、能编译 |
| **7 report+memorize** | 按 demo 骨架渲染中文报告;抽 `BugLesson` + 图边入记忆 | 闭环 |

---

## 2. Agentless 分层定位漏斗(委托前的确定性预筛)

> 借自 [openautocoder/agentless](https://github.com/openautocoder/agentless)(MIT,~2.1k)——无 agent 循环的 localize→repair→validate 管线,~$0.34/issue。其 localize 是**分层**的。

**为什么用它:** 在把活派给 omp/opencode **之前**,先用一个**确定性、可复现**的漏斗圈出嫌疑位置,而不是让 delegate 在整库里自由探索(费 token、不可控)。漏斗直接建在 Hyperion 已有的 `code_index` 上:

```
trigger(日志/漏洞) → 抽关键词/符号/错误码
   → file-level   : code_index 语义检索 + BM25,取 top-N 文件
   → function-level: 在候选文件内,rank_bM25+向量 取 top-N 函数(用 chunker 的符号边界)
   → line-level   : LLM 对候选函数 rerank,标嫌疑行(产 file:line 锚点)
```

产物:一组 `[(file, function, line, why)]` 锚点 + code-review-graph 给的 blast-radius(这些函数的 callers/callees)→ 喂给步骤 4 的 assemble。

> AutoCodeRover 的 **SBFL**(有复现测试时,按测试通过/失败 + 覆盖率给每个组件"可疑度")作为可选先验,放 backlog(需要可复现测试用例时)。

---

## 3. 委托抽象 `CodingAgentDelegate`(R2 实现)

```python
# src/hyperion/tools/delegate.py(R2 实现,展示在窗口由用户手敲)
class CodingAgentDelegate(abc.ABC):
    @abc.abstractmethod
    async def run(self, prompt: str, cwd: str, output_schema: dict) -> StructuredResult: ...

class OpencodeDelegate(CodingAgentDelegate):  # ★ v1 默认(2026-07-29 调研后定)
    # opencode run --format json --auto --dir <repo> "<prompt>"
    # → 解析 NDJSON 事件流 → 取最后 assistant 文本 → 喂 Schema 抠 JSON
    ...
class OmpDelegate(CodingAgentDelegate):       # 可配置后端(本机暂未装)
    # omp -p --yolo --no-session --cwd <repo>  或 omp --mode rpc 走 NDJSON 流式
    ...
class ClaudeDelegate(CodingAgentDelegate):    # 可选高档后端(需另装 claude CLI)
    # claude -p  或  claude-agent-sdk
    ...
```

**配置切换:** `config.yaml` 的 `delegate.backend: omp | opencode | claude`。**两者都支持、可自定义**(抽象接口从第一天起)。

**delegate 选型对比(针对"喂精确上下文→拿回结构化根因+补丁"):**

| 维度 | omp(默认) | opencode | claude code |
|---|---|---|---|
| 无头非交互 | `omp -p` ✓ | `opencode run` ✓ | `claude -p` ✓ |
| DeepSeek/多 provider | 40+ provider ✓ | 无关,支持本地 ✓ | **锁 Anthropic** ✗ |
| 结构化产出 | **`task` 子 agent 直接返 schema JSON** ✓✓ | `run` 返文本,需 prompt 要 JSON | 返文本 |
| 编排控制 | `--mode rpc`(NDJSON 流式)✓ | `opencode serve`(HTTP) | SDK |
| 安装/分发 | Bun+TS+Rust(本地已装);团队分发重 | **Go 单二进制**,团队最省事 | 需 Anthropic 账号 |
| bug-RCA 内建 | LSP + **DAP 调试** + `/review` P0-P3 判级 + hindsight 记忆 | LSP + plan/build 子 agent | 强推理 |

**v1 默认 opencode(2026-07-29 调研后修正)** 的理由:**本机已装 v1.18.3**(omp 因 github 墙 + bun 装不上);`run --format json` 事件流是 **harness 强制、与模型思考模式无关**的结构化回执载体,直接绕开"DeepSeek 思考模式不支持结构化产出"坑(见记忆 `deepseek-structured-output-gotcha`);Go 单二进制 + 原生 MCP client(挂 `hyperion mcp serve` 即可反向查记忆);`--variant` 给 provider 特定推理档(对齐 T2L"Medium 思考档最佳")。**omp** 待本机网络/装好后切回(其 strict schema 强校验是最大价值);**claude** 需另装 CLI。

> 精确无头参数已实测(`opencode run/serve --help`,见 [r2-bug-rca-research.md §1-2](../调研/r2-bug-rca-research.md))。MVP 用 `run --format json` 一次性;`serve` 长连 + `acp` 标准协议委托通道记 backlog。

---

## 4. 结构化产出契约(delegate 必须返回的 JSON)

```json
{
  "root_cause": "scan_only_handler 收到本该给 p2p 的扫描结果,未释放 p2p_scan_work,孤儿 radio work 阻塞所有站点扫描",
  "evidence": [
    {"file": "wpa_supplicant/scan.c", "line": 2446, "snippet": "static void scan_only_handler(...)"},
    {"file": "wpa_supplicant/p2p_supplicant.c", "line": 294, "snippet": "wpas_p2p_trigger_scan_cb(...)"}
  ],
  "trigger_chain": ["① p2p-scan 启动 → 设 scan_res_handler", "② 并发 Interface.Scan 覆写 handler", "③ 结果误路由到 scan_only_handler", "④ 不释放 p2p_scan_work → 孤儿"],
  "patch": "<unified diff>",
  "confidence": 0.85,
  "blast_radius_files": ["wpa_supplicant/scan.c", "wpa_supplicant/p2p_supplicant.c", "wpa_supplicant/p2p_supplicant.h"],
  "alternatives": [{"name": "medium", "summary": "...", "why_not": "..."}]
}
```

**SWE-agent ACI 借鉴:** delegate 暴露给 Hyperion 的工具面遵循 [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) ACI(swe-agent 已停维护,用活跃的最小化重写版;`find_file/search_dir/search_file/edit` + 输出上限 + 编辑守卫)的设计原则——这是 SWE-bench 上定位成功最大的贡献因子(ACI 概念源自 SWE-agent,NeurIPS 2024)。Hyperion 给 delegate 的提示词里申明这套工具契约。

---

## 5. 报告渲染(基于 demo 金标准骨架)

> 金标准:`example/demo1`(漏洞 PDF 驱动,445 行)、`example/demo2`(日志驱动,271 行)。**证据纪律是签名:每条结论锚 file:line、原文引用 C、日志按行号、覆盖率用 %。**

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

> 报告最终格式"到时候再讨论"(用户原话);v2 先按此骨架,后续按用验调。渲染器输入 = delegate 的 `StructuredResult`(§4)+ 步骤 3 的锚点。

---

## 6. 反向 MCP(delegate 现场查 Hyperion 记忆)

delegate(omp/opencode)干活时,经 MCP 查 Hyperion 的:`memory_recall`(查历史教训)、`code_index`(语义找代码)、`code-review-graph`(blast-radius/结构)。**不是 MCP 驱动 delegate,而是 delegate 查 Hyperion。** 让外勤能随时翻接案员的笔记本。

---

## 7. v1 取舍(用户:第一版别太复杂)

**做(R2 MVP):** 结构化 JSON 契约、直出报告、记忆沉淀。
**多阶段(2026-07-30 修正,见 §7.5):** R2 末起 delegate 拆 localize + repair 两阶段(解 glm-5.2 单 loop 不收敛);R3 多 candidate;R5 对抗式红队。
**不做(放 R5):** 自动 PoC 生成、多委托后端并发。

**L3/DAP(ChatDBG 式 reproduce-then-debug):** 仅当有**可复现 bug / core dump** 时才接(借 [chatdbg](https://github.com/plasma-umass/chatdbg));**事后日志分析不适用**(不能调试过去)。放 R2 末/P3。

---

## 7.5 多阶段委托(2026-07-30 演进,解 glm-5.2 单 loop 不收敛)

> R2 MVP 是单次复合委托(opencode 单 agent loop 定位+补丁+报告一次产)。实测 glm-5.2 在单 loop 跑 97K token 全工具调用,最后 prose「让我阅读...」**不收敛**,无 JSON 产出。**2026-07-30 改多阶段委托**(调研 Agentless + MASAI + 补丁审核两路)。

### 为什么(调研铁证)
- **Agentless**(arXiv 2407.01489)同模型 GPT-4o:**分阶段 32%/$0.70/78K token** vs SWE-agent 单 loop 18.3%/$2.53/**498K**。分阶段质量/成本/token 三项全胜 —— 不是「贵但稳」,是「又便宜又稳又准」。
- 消融:**skeleton(698 行,58% 命中)完胜整文件(778 行,53.7%)** = lost-in-the-middle(arXiv 2307.03172)。glm-5.2 内联大片代码(±15 行×多锚点)过载 = 不收敛根因(手动测 15 锚点碰巧收敛、端到端 17 锚点更多反不收敛)。
- Agentless 论文列 agent loop 三大失败(复杂工具/决策失控/自我反思有限)精准命中 glm-5.2 症状(97K token 全工具 + 最后 prose 不总结)。

### 设计(对齐 Agentless 三阶段 + MASAI ⟨Input,Strategy,Output⟩ 子 agent)
```
①localize_delegate(有工具,只定位 root_cause/evidence,禁补丁)→ JSON
     ↓ state["localization_json"]
②repair_delegate(根因已锁,只改局部,采 N 候选)→ patch
     ↓ N 候选 → artifacts/candidate_patches/
③verify(Hyperion 自跑,无 LLM:Tier 0 apply--check/编译/apply-revert + Tier 1 repro test rerank)→ 选 top-1
④review_delegate(可选,Tier 2 跨家族对抗审,reviewer 先判 intervene)→ verdict
⑤report+memorize
```
**MASAI 式「子 agent 不对话,靠 input/output 串接」**(走 workspace 文件),比自由对话稳得多。

### 每阶段产出契约(短、目标单一)
- **① localize**:`{root_cause, trigger_chain[], evidence[{file,line,snippet,why}], blast_radius_files[]}` —— **无 patch 字段**(禁补丁,glm-5.2 不用纠结 diff 格式)。
- **② repair**:`{patches[{id,summary,diff}], confidence}` —— 根因锁死,只改局部(glm-5.2 最擅长的「照着改几行」)。
- **③ verify**:`{verified, patch, validate_log}` —— Hyperion 自跑,无 LLM,零不收敛风险。
- **④ review(可选)**:`{intervene:bool, risks[], fix_diff?}` —— 先判要不要介入,默认保留作者结构。

### 验证分层(补丁正确性)
- **Tier 0 确定性**(零 LLM):`git apply --check` + 编译 + apply-revert(证必要)+ PASS_TO_PASS(无回归)。
- **Tier 1 执行信号**(核心):repro test(F2P)+ majority voting。MASAI 证「LLM 单独选不准 patch,加测试执行才能 rank」;1→5 sample 命中率 23%→35%。
- **Tier 2 对抗审查**(无测试时):跨家族模型审(cross-model 数据:reviewer ≥ writer 才涨点 —— Codex 自审 +12.9pp、Claude 自审 +0、弱审强 **-8.6pp 退化**);reviewer「先判 intervene」防重写。
- SWE-bench 有 7.8% overfit(测试过≠对),二次审核拦这批。
- ⚠️ **无测试套件的 C 仓(wpa/bluez 多数情况,2026-07-30 审核纠正 F4)**:Tier 1 repro test 落不了地 → 改用**日志/堆栈符号化 repro 替代**(`log_preprocess`+addr2line:patch 后 panic 条件/症状行是否不再可达)+ **LLM Selector**(Trae 式多 selector 投票,SWE-bench Verified #1 70.6%);SBFL 纯谱对无测试 C 不现实跳过。**诚实准确率**:report 必标 **METR 警示**([metr.org 2026-03-10](https://metr.org/notes/2026-03-10-many-swe-bench-passing-prs-would-not-be-merged-into-main/):~半数 test-passing PR 不会被合 → 测试是必要非充分,根因准确性靠证据纪律 + LLM Selector + 人工终审,不单靠 test-pass)。

### 落地分档
- **R2 收尾**:`node_delegate` 拆 `node_delegate_localize` + `node_delegate_repair`(中间 state 传 `localization_json`);localize prompt 用**指引**(file:line,不内联大片,避 lost-in-the-middle);verify Tier 0(tolerant apply,已有)。**`CodingAgentDelegate` 接口不改**(`run` 调多次,每次不同 schema)。
- **R3**:多候选采样(N=3)+ repro test rerank(workspace §6 六步验证)+ workspace_changes 生成 diff(根治格式)。
- **R5**:Tier 2 跨模型对抗审 + 2 轮反馈循环 + 退化熔断。

### 关键参考
[Agentless](https://github.com/openautocoder/agentless)(arXiv 2407.01489)· [MASAI](https://masai-dev-agent.github.io/)(arXiv 2406.11638)· [Lost in the Middle](https://arxiv.org/abs/2307.03172)· [Cross-Model Code Review](https://arxiv.org/html/2607.21656v1)。

---

## 8. R2 退出标准(★MVP 金标准)

1. **`uv run hyperion bug-rca --repo example/demo2/wpa --log example/demo2/journalctl_b.txt`** → 产出 `.patch` + `.md`。
2. **人工对照 `example/demo2`**:报告骨架一致(元数据/TL;DR/定位与根因/补丁分析/验证/附录)、证据纪律达标(每结论锚 file:line、日志按行号)。
3. demo1(漏洞 PDF 触发)同理跑通。
4. 产出抽成 `BugLesson` 入记忆;再跑一次同类问题能 `recall` 命中(验证闭环)。

## 9. 待办(记 backlog)

- delegate 结构化产出契约字段 R2 定稿;omp `--mode rpc` / opencode `serve` API 实测。
- Agentless 漏斗的 file/function/line 三级阈值与 rerank 策略。
- AutoCodeRover SBFL(有复现测试时)接入。
- 多轮 refine / 多 candidate / 自动 PoC(R5)。
- L3 DAP(ChatDBG 式,可复现 bug)。
