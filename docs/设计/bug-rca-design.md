# bug 根因定位工作流 — 设计文档(P2,★MVP)

> 状态:设计稿 v1(2026-07-28)· 实现阶段:**R2(MVP)**
> 上位文档:[architecture.md §6](architecture.md) · 金标准:`example/demo1`、`example/demo2`

---

## 0. 这是什么 / 为什么委托(面向小白)

**类比:Hyperion 是"接案调度员",omp/opencode 是"外勤侦探"。**

你给 Hyperion 一份**案卷** = 源码 + 一份**线索**(出问题时的**日志**,或一份**漏洞报告**)。Hyperion 不自己去翻每一行代码——它做三件它擅长的事:

1. **翻笔记本**(记忆):"这库之前有没有类似的 bug?当时怎么修的?"——命中就直接复用经验。
2. **画范围**(定位漏斗 + 结构图):用 Agentless 式分层定位(file→function→line)+ code-review-graph 的 blast-radius,圈出"问题大概在这几个函数"。
3. **派外勤**(委托):把**手术刀级上下文**(症状 + 圈定的代码片段 + 历史教训 + 严格产出契约)递给 omp/opencode,让它读代码、写补丁、给根因,**结构化返回**。

外勤回来后,Hyperion **验活**(可选二次委托:编译/apply)、**写报告**(按 demo 金标准骨架渲染中文报告)、**记笔记**(把这次根因+修法沉淀进记忆)。

**为什么不自己干定位/补丁:** 那是成熟 coding agent 最擅长的通用能力,自建会烧光一人/数月预算。**Hyperion 的差异化在记忆 + 调度 + 团队知识。** 把通用能力外包,自己聚焦差异化。

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

| 步 | 动作 | 关键点 |
|---|---|---|
| **1 ingest** | 确保源码已索引(code_index + code-review-graph);读 trigger(日志/漏洞报告) | 日志:文本行;漏洞报告:PDF/文本(抽关键句) |
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

class OmpDelegate(CodingAgentDelegate):       # ★ v1 默认
    # omp -p "<prompt>"  在 cwd;或 omp --mode rpc 走 NDJSON 流式
    ...
class OpencodeDelegate(CodingAgentDelegate):  # 团队分发后端(R4)
    # opencode run "<prompt>" 在 cwd
    ...
class ClaudeDelegate(CodingAgentDelegate):    # 可选高档后端
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

**v1 默认 omp** 的理由:本地已装零摩擦;结构化子 agent 结果正好对上 Hyperion 要的 JSON 契约(无脆弱 prose 解析);`/review` 的 P0-P3 判级是现成 RCA 骨架;`--mode rpc` 给干净的可编排控制。**opencode** 单二进制 + 社区大,是 R4 团队分发的更好默认。

> 精确无头参数 / `--mode rpc` 与 `serve` 的 API 契约,留 R2 实现时实测核实(README 级证据已足以下决策)。

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

**做:** 单委托、单轮、结构化 JSON 契约、直出报告、记忆沉淀。
**不做(放 R5):** 多轮 refine、多 candidate 补丁对比、自动 PoC 生成、对抗式红队二次验证、多委托后端并发。

**L3/DAP(ChatDBG 式 reproduce-then-debug):** 仅当有**可复现 bug / core dump** 时才接(借 [chatdbg](https://github.com/plasma-umass/chatdbg));**事后日志分析不适用**(不能调试过去)。放 R2 末/P3。

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
