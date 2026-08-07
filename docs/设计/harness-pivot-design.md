# Harness 转向设计:从「调度型 orchestrator」到「tool+skill server / 领域 harness」

> 2026-08-06 定稿。用户拍板:Hyperion 从「自己调度 coding agent 跑固定 bug-RCA 管线」转向「把记忆
> + 代码情报 + 日志取证 + 补丁验证 + 标准流程 skill 做成 MCP 工具/skill,供 opencode(主)/ codex /
> claude code 调用」。bug_rca LangGraph orchestrator **降级留参考,不暴露为主**。
> 关联:[[skill-design-decision]] [[pitfall-log]] #2/#7/#8/#9 [[delegate-already-localizes]] [[avoid-overengineering]]。

## 1. 为什么转(根因 + 证据)

**根本矛盾**:老路线让 Hyperion 当 orchestrator,卡着 opencode 走固定六节点管线(`ingest→recall_lessons→
delegate_localize_loop→assemble_repair→delegate_repair_loop→report_memorize`)去定位 bug。这有三个解不开的问题:

1. **跟 opencode 在它的强项上竞争** —— "代码推理 / 读码 / 改代码"是 opencode/codex/claude code 的核心能力,
   Hyperion 自己造的委托循环永远追不上;用户也更愿意用自己顺手的 agent。
2. **固定图把开放问题压成单轨迹 → 脆弱 + 次优** —— bug 根因路径不可预知,硬塞进固定流水线导致:
   - 踩坑 #7 recursion_limit(子 agent 撞步数上限全丢)、#8 线程池懒导入死锁、#9 keying/中间件节点计数;
   - 实测产**次优补丁**(R3 收尾 e2e:verified=True 但走 35s 超时兜底,非金标的"误路由落点立即释放";
     `validate_patch` 只查 apply 不查语义最优)。
3. **价值其实在别处** —— 真正的差异化是**记忆 / 代码情报 / 日志取证 / 补丁验证 / 方法论**,这些打包成
   **agent-agnostic 的工具 + skill** 反而增值(每个 coding agent 都能复用);锁在竞争型 orchestrator 里就停滞。

**证据(2025-2026 权威 + 同流项目)**:
- **Anthropic 自己的 agent 架构师**(Barry Zhang & Mahesh Murag,Claude agent 系统 designer)talk:
  *"Don't Build Agents, Build Skills Instead"* —— 模型推理比想象通用,**难的领域专业知识打包成
  skill/tool,别为每个领域重造 agent 底盘**。"模型=处理器,skill=让它在某领域有用的软件。"
- **市场已投票**:每一个做"代码图/记忆/blast-radius"的成功项目都是 **PROVIDER(MCP tool-server)**,
  不是 DRIVER(自己跑 agent):
  - **code-review-graph** —— Hyperion 已 vendor 的参考项目,自我定位"local code graph for token-efficient
    AI code review **through MCP and CLI**",30 个 MCP 工具,纯 tool-server。
  - **Sourcegraph**(最大商业玩家)明确选"**给 agent 当 context 层**",不跟 agent 竞争推理。
  - codebase-memory-mcp / codebadger(Joern CPG)/ GitNexus / Serena —— 全是 tool-server。
- **MCP 是 2026 给 coding agent 暴露能力的共识**(opencode / codex / cursor / claude code 全原生支持)。
  deer-flow 走 REST+SSE 是因它有 Web UI + IM 网关;Hyperion 给 coding agent 用,该走 MCP。
- **踩坑 #2 的项目级泛化**:bug_rca orchestrator 正是 #2("委托型 agent 不建平行管线")警告的那条平行管线。

## 2. 新身份

> **Hyperion = 系统软件 bug-RCA 的「领域 harness 层」** —— 记忆 + 代码情报 + 日志取证 + 补丁验证 +
> 标准流程 skill,作为 **MCP tool/skill server** 供 coding agent 调用。**不再自己跑固定 agent 管线。**

harness 组件映射:Hyperion 提供 **记忆 / 代码情报(context)/ 工具 / skill / 沙箱 / 可观测**;**不提供**
agent 推理循环(那是 opencode 的活)。即"领域 harness 插进通用 harness"。

边界(踩坑 #2):**Hyperion 管 记忆 + 代码情报 + 日志 + 验证 + 沉淀;agent 管 定位推理 + 改代码**(opencode
强项)。别 double-localize。

## 3. 工具 + skill 架构

### 3.1 六个 MCP 工具(精炼,只做 coding agent 做不好/做不了的)
`src/hyperion/tools/mcp_memory.py` `build_server()`,FastMCP `@mcp.tool()`,被 opencode `hyperion*` glob 自动放行:

| 工具 | 作用 | 状态 |
|---|---|---|
| `hyperion_memory_recall` | 翻长期记忆(历史 bug 教训 / 代码库事实),带 file:line 溯源 | ✅ R1 已有 |
| `hyperion_search_codebase` | 语义+符号检索,**只回索引里真实存在的符号**(防幻觉) | ✅ 已有 |
| `hyperion_filter_logs` | 大日志按 关键字∩时间窗 过滤成有界摘录 | ✅ 已有 |
| `hyperion_blast_radius` | 改动影响面(结构图 BFS:改这些文件会波及谁) | 🆕 转向 D0 |
| `hyperion_validate_patch` | 补丁能否干净 apply(`git apply --check`,执行硬门零 LLM) | 🆕 转向 D0 |
| `hyperion_memorize` | 写一条记忆(ad-hoc / 收尾沉淀教训) | ✅ 已有 |

工具粒度选择(反 god-tool):~6 个命名清晰、workflow 形的工具 + skill 编排,远低于"工具过载"阈值
(arXiv:2605.24660:Claude 在几十个**语义重叠**工具才显著降准)。整条流程包成一个 god 工具是命名反模式
(rigid + opaque + 不能自纠)——否决。

### 3.2 `bug-rca` SKILL.md(playbook,替代老固定管线的方法论)
`.claude/skills/bug-rca/SKILL.md`(opencode 原生发现 `.claude/skills/`,跨平台 agentskills.io 标准)。
七步:**① recall → ② 语义搜入口 → ③ 过滤日志 → ④ 立假设+证伪 → ⑤ blast-radius → ⑥ 改+validate(硬门)→
⑦ memorize(硬门)**。advisory(灵活自纠)但 mandate 两道硬门(validate/memorize)。**opencode 激活:`skill(name="bug-rca")` 按需加载**(非 auto-inject)。

确定性靠 **skill 强制步骤 + 工具硬门**(validate_patch / recall-first / memorize),**不靠固定图**。
老管线需要的"必须 recall / 必须 validate / 必须 memorize"在 skill + 工具设计里照样保证,但不把整条轨迹钉死。

### 3.3 MCP transport:stdio(默认)+ Streamable HTTP(D0)
- `hyperion mcp serve` → stdio(默认,agent 拉起子进程 1:1,delegate 老路径 / 本地最简)。
- `hyperion mcp serve --transport http --host --port` → **streamable-http**(warm 长进程,多 agent 共用)。
  mcp SDK 1.28.1 已内置(FastMCP 构造吃 host/port → settings → uvicorn;`run(transport="streamable-http")`)。
  **解 ③ cold-boot**:省掉每修一个 bug 重启加载 ~1.2GB(sentence-transformers)的冷启动。
- 配置:`config.yaml` 新 `mcp:` 段(transport/host/port 默认);opencode 走 `config/opencode_hyperion.json`;
  codex 用 `config/codex_hyperion.toml`(`[mcp_servers.hyperion]` **下划线**,issue #3441 高频踩坑)。

## 4. orchestrator 处置(降级留参考)

- `src/hyperion/workflows/bug_rca/` 模块 docstring 标"post-pivot 参考实现,不再主路径"。
- CLI `hyperion bug-rca` 保留(向后兼容)+ 加 deprecate 提示指向新路径。
- **不**把 orchestrator 暴露成 MCP 工具。
- verify-refine 收敛 / 报告渲染 / 结构化 memorize 逻辑值得日后抢救成独立工具(本轮不动代码)。
- autopilot(整流程一个工具)**仅当** e2e traces 显示 agent 反复需要"一键端到端"才反应式加(Arcade 建议)。

## 5. 适配 opencode(主)+ 留接口给其他 agent

- **opencode(主)**:原生吃 SKILL.md(`.claude/skills/` 自动发现 + `skill()` 工具 + `permission` glob);
  MCP 走 `config/opencode_hyperion.json`。现有 `hyperion-localize`/`hyperion-repair` 自定义 agent(orchestrator
  委托用)保留;新主路径用默认 agent + bug-rca skill + 6 工具。
- **codex**:`config/codex_hyperion.toml`(`[mcp_servers]` 下划线;stdio 或 http url)。
- **claude code / 其他**:`.claude/skills/` 标准 + MCP `.mcp.json`,零额外适配。
- 跨平台靠 **SKILL.md(agentskills.io 标准)+ MCP(共识协议)** 两件套,不锁定单一 agent。

## 6. 验证 / 诚实边界

- **单测**:2 新工具(blast_radius 优雅降级 / validate_patch apply 正反)✅ 5 测绿;全量 150 测绿。
- **e2e(主路径证明)**:opencode + bug-rca skill + 6 工具 跑 demo2,**不走 orchestrator**;观察 agent 自己
  recall→search→filter→假设→blast→edit→validate→memorize。期望产合法补丁 + 入库 + 流程能自纠。
- **诚实**:补丁质量仍 **plausible**(静态+blast+LLM),非 **verified**(测试执行留 R5 沙箱)。转向换得的是
  **流程灵活 + 自纠 + 不脆弱**(根除踩坑 #7/#8/#9),不是补丁最优。

## 7. 后续(同 tool+skill 形,不本轮)

- **P-A patch 分析**:`hyperion_analyze_patch`(决策卡:正确性/作用/是否合入)+ `patch-review` skill
  + GitHub 抓取/auto-clone/Gerrit 接口。
- **feature 2 调用链 / 跨版本**:`hyperion_call_chain` + `hyperion_cross_version_diff` + 调研 skill。
- **R5 生产化**:Docker 沙箱 + 测试执行 → 把 plausible 升级到 verified(FAIL_TO_PASS/PASS_TO_PASS)。
