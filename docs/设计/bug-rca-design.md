# bug 根因定位工作流 — 设计文档(P2,★MVP)

> 状态:设计稿 v1 → **R2 MVP ✅ + R3.1 迭代 verify-refine(B)已落**(2026-07-31)· 当前真相见 §7.6
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

> 🆕 **workspace 演进(2026-07-29 定稿,见 [workspace-design.md](workspace-design.md))**:R2 MVP 先用「localize 锚点**内联进 prompt** + `--dir <repo>`」跑通;**R2 末起演进为 workspace 模型**——每 bug 一个专用目录(`<repo>__<bug-id>__<hash>/`,七段:code/triggers/delegate/artifacts/patch/report/docs),opencode `--dir` 指此,**读全量代码 + 日志**(非内联片段,解决"补丁易错位/日志没法结合");assemble 改**方式 B 指引 prompt**(给 file:line 嫌疑起点,不内联代码,opencode 自读);**大日志 Hyperion 粗筛(grep+时间窗+addr2line)+ opencode 深挖**;补丁 = **git diff 观察workspace/code 改动**(根治 LLM 吐 diff off-by-one)+ **Tier0 验证**(`git apply --check`/revert;编译/FAIL_TO_PASS/PASS_TO_PASS 门控构建环境,R5)。**R3.1(#54-rework)改迭代 verify-refine(B)——见 §7.6**。隔离默认本地(R2/R3)、Docker R5。

---

## 1. 工作流(StateGraph;R2 七步 → R3.1 八步双循环)

> R2 是单次复合委托(七步);**R3.1 #54-rework 改八步 + 同会话双循环**(localize loop + repair loop),verify 并入 repair loop 的 `validate_patch` 门控。**双循环/自审/门控细节见 §7.6(当前真相)**;本节是概览。

```
START → ingest ──► recall ──► localize ──► assemble_localize
   → [delegate_localize_loop]   阶段① 定位:证伪式自审 verdict → 没把握就 --continue 同会话重定位(max K1)
   → assemble_repair
   → [delegate_repair_loop]     阶段② 修复:edit code/ → git diff 观察补丁 → validate_patch 门控 → 没过就 --continue 同会话再修(max K2)
   → report_memorize ──► END
```

**面向小白 ——「接案调度员(Hyperion)+ 外勤侦探(opencode)」类比:**

| 步 | 中文名 | 干啥(大白话) |
|---|---|---|
| **1 ingest** | 立案 | 接案,给外勤搭独立工位(workspace),放全量代码 + 问题描述 + 契约,不污染原仓库 |
| **2 recall** | 翻笔记本 | "这库以前有没有类似 bug?"命中就复用经验 |
| **3 localize** | 画范围 | 确定性漏斗圈出"嫌疑大概在这几个函数" |
| **4 assemble_localize** | 组定位卷宗 | 症状 + 嫌疑起点指引 + 历史教训 + 定位契约 |
| **5 delegate_localize_loop** | 派外勤定位(+自审) | opencode 定位根因,**证伪式自审**(verdict);没把握就同会话重定位 |
| **6 assemble_repair** | 组修复卷宗 | 锁死的根因 + evidence 代码片段 + 修复契约 |
| **7 delegate_repair_loop** | 派外勤修复(+验活) | opencode 直接改 code/;**git diff 观察补丁 + `validate_patch` 门控**;没过就同会话再修 |
| **8 report+memorize** | 写报告+记笔记 | 金标准骨架中文报告;根因+修法沉淀进记忆 |

> 一句话:**立案→翻笔记本→画范围→[定位+自审]→[修复+验活]→写报告+记笔记**。Hyperion 全程调度(组装精确上下文 + 调度 + 沉淀),重活(读码/改码)委托 opencode;**同一 opencode 会话贯穿定位+修复**(`--continue` 链,复用上下文免冷启动)。

**技术细节:**

| 步 | 动作 | 关键点 |
|---|---|---|
| **1 ingest** | 读问题描述(文本/txt/md/pdf)→ 抽关键字(`triggers/keywords.json`)→ 驱动日志预筛 + localize | 见 [workspace-design.md](workspace-design.md) §5 |
| **2 recall** | 记忆召回历史 bug/教训;CRG blast-radius(trigger 指向模块) | 命中"见过"→ 走首解路径 |
| **3 localize** | **Agentless 漏斗**:file→function→line | 确定性预筛,产 `file:line` 锚点 |
| **4/6 assemble** | 组装提示词 = 症状 + 锚点/evidence 片段 + 历史教训 + 产出契约(§4) | 手术刀级——只喂相关片段,不整库 dump |
| **5/7 delegate_loop** | `delegate.run(prompt, cwd, schema, agent=, continue_session=)`;v1 默认 **opencode** | verdict 自审 + `validate_patch` 门控;双循环细节见 §7.6 |
| **8 report+memorize** | demo 骨架渲染中文报告;抽 `BugLesson` 入记忆 | 闭环 |

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

**v1 默认 opencode(2026-07-29 调研后修正)** 的理由:**本机已装 v1.18.3**(omp 因 github 墙 + bun 装不上);`run --format json` 事件流是 **harness 强制、与模型思考模式无关**的结构化回执载体,直接绕开"DeepSeek 思考模式不支持结构化产出"坑(见记忆 `deepseek-structured-output-gotcha`);Go 单二进制 + 原生 MCP client(挂 `hyperion mcp serve` 即可反向查记忆);`--variant` 给 provider 特定推理档(对齐 T2L"Medium 思考档最佳")。**omp** 待本机网络/装好后切回(其 strict schema 强校验是最大价值);**claude** 需另装 CLI。

> 精确无头参数已实测(`opencode run/serve --help`,见 [r2-bug-rca-research.md §1-2](../调研/r2-bug-rca-research.md))。MVP 用 `run --format json` 一次性;`serve` 长连 + `acp` 标准协议委托通道记 backlog。

---

## 4. 结构化产出契约(两阶段;R3.1 verify-refine)

> R2 是单 JSON(root_cause+patch 混一锅);**R3.1 拆两阶段**(对齐 `nodes.py` `LOCALIZE_SCHEMA`/`REPAIR_SCHEMA`),各带 **`verdict` + `falsification`**(证伪式自审,见 §7.6)。补丁**不在 JSON 里**——repair 直接 edit `code/`,Hyperion 用 git diff 观察。

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

**工具面:** delegate 用 opencode 内置 edit/grep/read 工具(设计原则参考 [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) ACI:输出上限 + 编辑守卫,NeurIPS 2024)。

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
**多阶段(2026-07-30 修正,见 §7.5/§7.6):** R2 末起 delegate 拆 localize + repair 两阶段(解 glm-5.2 单 loop 不收敛);**R3.1 改迭代 verify-refine(B,#54-rework,见 §7.6)**(弃无脑多候选采样,投票降为兜底);R5 对抗式红队 + rerank filter+vote(oracle 就绪后)。
**不做(放 R5):** 自动 PoC 生成、多委托后端并发。

**L3/DAP(ChatDBG 式 reproduce-then-debug):** 仅当有**可复现 bug / core dump** 时才接(借 [chatdbg](https://github.com/plasma-umass/chatdbg));**事后日志分析不适用**(不能调试过去)。放 R3+/R5。

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
- **② repair**:delegate **直接 edit code/**(补丁由 git diff 观察);schema `{confidence, verdict, falsification}`(自审,见 §7.6)。
- **③ verify**:`{verified, patch, validate_log}` —— Hyperion 自跑,无 LLM,零不收敛风险。
- **④ review(可选)**:`{intervene:bool, risks[], fix_diff?}` —— 先判要不要介入,默认保留作者结构。

### 验证分层(补丁正确性)
- **Tier 0 确定性**(零 LLM,R3.1 已落):`git apply --check`(含 `--3way`/`patch -p1` 降级)+ reverse `--check`(证必要)。**编译 / FAIL_TO_PASS / PASS_TO_PASS 门控构建环境(R5),不在 Tier 0**(wpa/bluez build 是硬前提)。
- **Tier 1 执行信号**(核心):repro test(F2P)+ majority voting。MASAI 证「LLM 单独选不准 patch,加测试执行才能 rank」;1→5 sample 命中率 23%→35%。
- **Tier 2 对抗审查**(无测试时):跨家族模型审(cross-model 数据:reviewer ≥ writer 才涨点 —— Codex 自审 +12.9pp、Claude 自审 +0、弱审强 **-8.6pp 退化**);reviewer「先判 intervene」防重写。
- SWE-bench 有 7.8% overfit(测试过≠对),二次审核拦这批。
- ⚠️ **无测试套件的 C 仓(wpa/bluez 多数情况,2026-07-30 审核纠正 F4)**:Tier 1 repro test 落不了地 → 改用**日志/堆栈符号化 repro 替代**(`log_preprocess`+addr2line:patch 后 panic 条件/症状行是否不再可达)+ **LLM Selector**(Trae 式多 selector 投票,SWE-bench Verified #1 70.6%);SBFL 纯谱对无测试 C 不现实跳过。**诚实准确率**:report 必标 **METR 警示**([metr.org 2026-03-10](https://metr.org/notes/2026-03-10-many-swe-bench-passing-prs-would-not-be-merged-into-main/):~半数 test-passing PR 不会被合 → 测试是必要非充分,根因准确性靠证据纪律 + LLM Selector + 人工终审,不单靠 test-pass)。

### 落地分档
- **R2 收尾**:`node_delegate` 拆 `node_delegate_localize` + `node_delegate_repair`(中间 state 传 `localization_json`);localize prompt 用**指引**(file:line,不内联大片,避 lost-in-the-middle);verify Tier 0(tolerant apply,已有)。**`CodingAgentDelegate` 接口不改**(`run` 调多次,每次不同 schema)。
- **R3**(~~已演进,见 §7.6~~):原计划「多候选采样(N=3)+ repro test rerank」;R3.1 重审后改**迭代 verify-refine(B)**,多候选降为兜底(默认关)。workspace_changes(根治 patch 格式)+ validate Tier0 已落地。
- **R5**:Tier 2 跨模型对抗审 + 2 轮反馈循环 + 退化熔断。

### 关键参考
[Agentless](https://github.com/openautocoder/agentless)(arXiv 2407.01489)· [MASAI](https://masai-dev-agent.github.io/)(arXiv 2406.11638)· [Lost in the Middle](https://arxiv.org/abs/2307.03172)· [Cross-Model Code Review](https://arxiv.org/html/2607.21656v1)。

---

## 7.6 迭代 verify-refine(B,R3.1 #54-rework,2026-07-30)

> §7.5 原设计是「②repair 采 N 候选 → ③verify 选 top-1」(Agentless majority voting)。R3.1 重审后
> **主路径改为迭代 verify-refine(B)**:同一 opencode 会话内反复「自审 → 不行就重试」,弃无脑多采样投票。
> majority voting **降级为兜底(默认关,见下「rerank 要啥条件」)**。**本节是当前实现真相**;§7.5 留作历史推理。

### 为什么改(三铁据,非凭感觉)
1. **投票前提 wpa/bluez 全缺**(详见下「rerank 要啥条件」):无测试 oracle + C 补丁形态发散(加 guard / 提前 free / 改状态机)+ glm-5.2 近确定性 → N 个样本几乎一样 → 投票平凡 + N× token 白烧。
2. **成本亏**:K 轮 refine 复用 KV cache 比 N 次冷启动采样省 **70-96% token、TTFT 快 5-30×**;opencode 首跑已花 ~10min 读码,`git reset` 重跑等于把最贵的上下文扔掉重来。
3. **self-verify 偏差**(Stechly ICLR24 / Kamoi TACL24 / Huang ICLR24):同模型自评不可靠 → B 必须叠「证伪式自审 + 执行信号硬门控」(非纯自评)。

依据:Self-Refine(arXiv 2303.17651)/ Reflexion(2303.11366)/ SWE-Search(2410.20285)/ Aider(architect→edit→test→fix 单轨迹迭代);Agentless 投票仅在有 oracle 时有效(本地核查 + 2024-2026 论文)。

### B 设计(同会话双循环,Hyperion 外循环驱动)
```
ingest → recall → localize → assemble_localize
  → [delegate_localize_loop]  iter0 新 session → 读 verdict(confirmed?)→ 否则带 falsification 反馈 --continue 重定位(max K1)
  → assemble_repair
  → [delegate_repair_loop]    iter0 --continue 续同 session → edit code/ → git diff 观察 patch → validate_patch 门控 → 否则带 gate.log 反馈 --continue 再修(max K2)
  → report_memorize
```
- **同会话** = delegate `--continue` 链(已核查成立:opencode `run.ts:492` + `prompt.ts:1092/672-689` —— `--continue` 按 sessionID 复用全量历史 messages,与 `--agent`(per-prompt 字段)**正交**,session 内中途换 agent 显式支持;两 agent 须 `mode: primary`,否则 `--agent` 被 `run.ts:610-617` 静默吞)。per-bug workspace 唯一 cwd → session 隔离。
- **verdict 由 opencode 自审产出(B)**:schema 加 `verdict`(confirmed/needs_revisit、verified/needs_fix)+ `falsification`(主动找的反例);agent prompt 加证伪自审指令,对抗 confirm-own-error 偏差。
- **执行硬门控(Hyperion 侧,非 LLM)**:`validate_patch` Tier0 apply/revert —— 这是 test、不是 critic,不违反 B。#50 `log_preprocess` 落地后加 **repro 门控**(patch 后 symbolized-log panic 条件不再触发)= 更强信号。信号分层:**执行硬 / 对抗审次 / 自评弱**。
- **收敛不破**:每 delegate call 仍 `steps` 限制 + 单 schema(继承 §7.5 多阶段拆分的收敛性,不重蹈 glm-5.2 单 loop 97K 不收敛);外循环 max-loop 兜底。**只在 `verdict=needs_revisit`(干净返回)时重试**;infra 错误(timeout/error/schema 抽不出)直接跳出,不 `--continue` 破损 session。

### rerank 要啥条件才使得上劲(面向小白)★

**rerank / 多采样投票是啥**:类比「一道难题请 5 个人各自独立做,选**出现次数最多**的答案」。Agentless 的 majority voting 就是让模型对同一 bug 修 N 遍,N 个补丁里哪个"最像、出现最多"就选谁(self-consistency:独立得到同一答案 → 大概率对)。

**为啥光投票不靠谱 —— 缺一个「裁判(oracle)」**:万一 5 个人犯同一个错、或答案各不相同?「取多数」就没意义。所以投票要有效,得先有东西能**客观判对错**——跑一下看结果,不是猜。这个裁判叫 **oracle**:

- **测试套件**(wpa `hwsim` / bluez unit):打完补丁跑测试,过了 = 没改坏 → 测试就是裁判;
- **repro 复现**(#50 `log_preprocess`):打完补丁再走一遍崩溃复现步骤,不再触发 = 管用 → 复现就是裁判(没正式测试套件时的弱裁判)。

**filter + vote = 先筛后投(这就是「前提①:oracle 存在」)**:Agentless 不是上来就投票,而是 ① **filter** —— 先把 N 个补丁逐个交给 oracle(测试/repro)跑,扔掉跑不过的;② **vote** —— 在**剩下的、可能对的**补丁里再按"谁出现多"选 top-1。**没裁判,filter 这步直接做不了,只剩光投票**——而光投票在没裁判时基本等于瞎蒙。

**回到我们为啥默认关**:wpa/bluez 的 C 代码现在**没裁判**——hwsim/bluez unit 要先搭构建环境(R5 才搞定)、#50 repro 还没做、加上 glm-5.2 近确定性(N 个补丁几乎一样)。→ filter 做不了、vote 平凡 → N 次采样 token 白烧。所以 `delegate.rerank.enabled: false`(默认关)。

**什么时候开**:① 构建环境就绪、能跑 hwsim/bluez 测试;或 ② #50 repro 落地、有了"打完补丁看崩溃还触不触发"的弱裁判 —— 这时 filter+vote 才有用,配置开 `enabled: true`(或 `auto` 自动检测 oracle)。在那之前,主路径走迭代 verify-refine(B)就够。

> rerank 原语(`rerank.py` majority_vote)本身没删,换地方用:① **localize 文件投票**(R3.1 方案A,文件名归一化平凡、无需 oracle);② **深度调研事实一致性**(R3.2,多轨迹事实频次作置信度);③ **有 oracle 的 patch rerank**(R5)。三处复用同一「Counter + 票数/首现/简洁度」模式,换归一化函数即可。

### 落地
- **R3.1(当前实现)**:`node_delegate_localize_loop` + `node_delegate_repair_loop`(双循环);config `delegate.max_localize_loops/max_repair_loops`(默认 2);`delegate.rerank.enabled` 默认关(loop 耗尽 K2 未过 + enabled 才 fan-out 兜底)。
- **R5 / 有测试模块**:`rerank.enabled: auto`(检测到测试套件/repro 才开 filter+vote);Tier 2 跨模型对抗审。

### 关键参考
[Self-Refine](https://arxiv.org/abs/2303.17651)(2303.17651)· [Reflexion](https://arxiv.org/abs/2303.11366)(2303.11366)· [SWE-Search](https://arxiv.org/abs/2410.20285)(2410.20285)· [Agentless](https://arxiv.org/abs/2407.01489)(2407.01489)· [Stechly self-verify 天花板](https://openreview.net/forum?id=4O0v4s3IzY)(ICLR24)· [Kamoi 自校正](https://direct.mit.edu/tacl/article/doi/10.1162/tacl_a_00713)(TACL24)· [METR 警示](https://metr.org/notes/2026-03-10-many-swe-bench-passing-prs-would-not-be-merged-into-main/)。

---

## 8. R2 退出标准(★MVP 金标准)— ✅ 2026-07-30 达标

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
