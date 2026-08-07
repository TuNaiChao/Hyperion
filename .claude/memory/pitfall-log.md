---
name: pitfall-log
description: "踩坑记录文档(docs/踩坑记录.md)位置 —— 项目走过的弯路汇总;设计前先查、踩坑后往上加"
metadata:
  node_type: memory
  type: reference
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-08-03T06:55:38.934Z
---

`docs/踩坑记录.md` 是专门记录**走过的弯路 / 踩过的坑**的累积文档(每条五段:现象 → 弯路 → 根因 → 教训 → 现状)。

**何时查 / 何时写**:① 设计新模块前先翻一遍(避免重复踩已知坑);② 做了设计反转 / 删了已建代码 / 用户指出过度设计 / 调研推翻既有方案 时,往上加一条(模板在文档末尾)。

**首条 #1(2026-07)**:patch 投票 rerank 的**三段反转**(当主路径 → 降级默认关兜底 → 整体移除)。根因:无 oracle 时投票平凡 + 现代 SOTA 转单轨迹+执行验证;"默认关兜底"是伪安全(死代码是债)。关联 [[rerank-mechanism-where-it-shines]]。

**#2(2026-07)**:Hyperion 侧定位漏斗(file→function→line)建到一半发现**与委托目标 opencode 重复定位**(double localization)→ 整套砍,改"opencode 自主定位 + Hyperion 把记忆/检索/日志过滤做成 MCP 工具给它调"。根因同 #1:**设计前没核前提**(opencode 本就会定位;Hyperion 是委托型不是独立 agent,误搬 Agentless 漏斗)。教训:建 Hyperion 能力前先问"opencode 会不会?",会→做工具别重造(见 [[delegate-already-localizes]])。

**#3(2026-08)**:delegate 子进程流式读用 `async for line in stream`(readline)有**隐式 64KB 行长限** —— opencode `--format json` 读大文件/大日志时单条事件 >64KB 直接 ValueError 崩(`Separator is not found, and chunk exceed the limit`)。R2 没踩到是没 `--log`;R3.1 加大日志才触发。改 `read(n)` 块读 + 之后统一 `splitlines`(行长无上限)。同 #1/#2 通病:**没核前提**(readline 行长限)就用 API。关联 [[opencode-mcp-wiring]](同源:opencode MCP 大输出还有 <8KB pipe 死锁 / listTools 5s 超时等坑)。

**#4(2026-08)**:被外部进程拉起的服务,相对路径按"调用方 cwd"解析。opencode 把 MCP server cwd 设成 workspace/code → Hyperion config 里相对 `data/` 路径(memory SQLite / LanceDB)解析到 `workspace/code/data/` → ① git add -A 连带进补丁污染(validate_patch 挂)② 记忆写临时库不持久。修法:`cmd_mcp` build_server 前 `os.chdir(Hyperion 根)`。教训:被 spawn 的服务要么 chdir 回自家根,要么 config 路径绝对化。关联 [[opencode-mcp-wiring]]。

**#5(2026-08)**:workflow 盲信 LLM 结构化输出 schema。glm-5.2 的 `evidence[].line` 偶尔给逗号多行串("3067,4105,5980")而非 int → `Evidence(line=<串>)` pydantic 崩整个 bug-rca。schema 是契约不是保证;LLM 输出→pydantic 严格模型边界必加防御 coerce(取首 int)+ 单条坏 try/except 跳过。这类方差性 bug e2e 才暴露(单测难复现),长尾逐个 coerce。同 #1-#4 通病:**没核前提**(LLM 会守 schema)。

**#6(2026-08)**:chunker #58 只修符号路径、漏 module 路径。`hyperion index wpa` 撞 400(>33000 字符);backlog 写"driver_nl80211.c/qca-vendor.h 有超长符号"→ 照单给符号路径加 `_symbol_to_chunks`,离线扫 driver_nl80211.c max 12275 以为成了。**全仓扫才发现 qca-vendor.h 仍单 chunk 304044**(它无被解析符号 → 整文件落 `_module_chunk`,module 路径无上限)。driver_nl80211.c 是红鲱鱼。根因:① backlog 根因描述只点符号路径,没区分两条产 chunk 路径;② C 头文件常无符号(struct 故意跳过降噪)→ 全进 module chunk;③ 离线只验"点名文件"不验"全仓聚合"。教训:**修"无上限"类 bug 要把所有产该产物的路径都过一遍**;**离线验证用全仓聚合统计(max/over_cap)别只验点名样本**(红鲱鱼让你误判已修)。详见 [[r32-research-e2e-handoff]] 同批 R3.2 工作。

**#7(2026-08)**:R3.2 research 子 agent 撞 `recursion_limit` **全丢**。reasoning 模型(deepseek-v4-pro)拿好工具就过度探索、撞 `GraphRecursionError`(硬抛异常)→ `ainvoke` 无返回 → 几十轮 grep/read 收集的证据**全丢**,finding 空白。调大 limit / 改 prompt 都不治本(8/8 撞墙)。两步才修:① `recursion_limit` **单位错**——它按 *superstep* 计、标准 ReAct 每轮=2,`_MAX_TURNS` 当 limit 实际只给半轮 → `_RECURSION_LIMIT=_MAX_TURNS*2`;② **优雅降级**:`astream(stream_mode=values)` 流式留最后 state,`except GraphRecursionError` 后把 `_compact_evidence`(已收集证据压成文本)喂回**裸模型**(不带 tools)逼它只产 JSON。根因:recursion_limit 是硬墙非软停(同 #3 readline 隐式硬限通病)+ 盲信"agent 会自己收尾"。教训:**ReAct 子 agent 别指望它自收尾——撞 limit 要降级、别硬抛全丢**;**prompt 约束不住 reasoning 模型的探索冲动,结构性降级(catch+强制收尾)比 prompt 可靠**。详见 docs/踩坑记录.md #7 + [[r32-research-e2e-handoff]]。

**#8(2026-08)**:@tool 在线程池里**懒导入重模块**触发 `_DeadlockError`。三个 research @tool 函数体里写 `from ...code_index... import`,langgraph tool_node 用 `run_in_executor` 线程池跑工具 → 首次在工作线程 import 重模块,包 `__init__` 争 Python import lock(非重入)→ 死锁。**隐蔽点**:e2e 里 `node_index`(research 前)已预载这些模块 → 工具只剩 `sys.modules` 查表不撞锁,**"e2e 绿、单测红"**(单测跳过 index 必挂)。修法:4 个 code_index import 提到 `_research.py` **模块顶层**。教训:**@tool / 任何被线程池调的函数,别在函数体懒导入重模块——提模块顶层**(asyncio to_thread / run_in_executor 同理);**"e2e 过、单测挂"先怀疑导入/cwd/时序差异**(某前序步骤在 e2e 顺带满足了前提,单测没那步就露馅)。关联 #3(readline)/ #4(cwd)同根线程·子进程隐式坑。详见 docs/踩坑记录.md #8 + [[r32-research-e2e-handoff]]。

**#9(2026-08)**:TurnBudget 中间件"接上了但不生效" —— 单测 80 绿但真模型 e2e 照撞 `recursion_limit(44)`。两层 bug:① keying 用 `id(runtime)`(每 superstep 可能新对象→计数每轮清零)改 `thread_id`;② **recursion_limit 漏算中间件节点**:每个中间件的 after_model/before_model hook 都是**独立图节点**各算 1 superstep,一轮 ReAct = N+2 superstep(非 2),`(max+2)*2` 严重低估 → 递归墙抢在 TurnBudget strip 前撞。改按 `len(middleware)` 动态算 recursion_limit。教训(进阶 #7):**挂中间件后每轮 superstep = 2 + 中间件数,别用 2×max_turns**;**中间件按 run 分桶别用 id(obj),用 thread_id**;**"中间件不生效"写集成探针(桩模型+真 create_agent+超大 limit)隔离**;**"单测绿集成红"因手工桩没复刻框架对象生命周期**。详见 docs/踩坑记录.md #9 + [[r32x-p1-turn-budget-handoff]]。

**#10(2026-08)**:opencode 1.18.11 http(streamable-http)MCP **不注册原生工具**。e2e#3 想用 warm http 解 ③ cold-boot,agent 看不到 `hyperion_*` 原生工具 → 绕 curl 手工握 initialize→tools/call(waste token)。改回 local stdio 立刻原生注册。机理:listTools 便宜(不加载 embedder)→ stdio 冷启也能在 timeout 内注册;首次 recall/search *调用* 才冷启 embedder → timeout 要 ≥120000ms(模板默认 10000 不够)。教训:opencode↔hyperion 走 **local stdio**;http 待解注册问题;验"原生调用"看日志 `evaluated permission=<server>_<tool> action=allow`。模板 timeout 已 10000→120000(`6338e85`)。详见 [[opencode-mcp-wiring]] + docs/踩坑记录.md #10。

**#11(2026-08)**:glm-5.2 **系统性把 bug 根因误诊成「显眼日志行」**(连续 3 次 e2e wpa 孤儿都误诊成 abort-fail,金标是更早 7 秒的 scan_res_handler 误路由)。三次都 applies=True、四道硬门全过、verified=True,**但根因错、补丁治标碰巧能 work**。根因:① 注意力被 `Abort scan failed: ret=-2`(ERROR 显眼)劫持,忽略 INFO 级的误路由起点 `Scan-only results received`;② 因果时序倒置(孤儿 10:12:12 形成,abort 失败 10:12:19 才发生,-ENOENT 是症状非原因);③ 证伪没跨假设边界;④ **验证门控只查 apply 不查根因正确性(最大盲区)**。教训:**apply 过 ≠ 根因对**;LLM RCA 会抓显眼行忽略安静起点 → filter_logs/报告标因果起点 + 时序证伪(purported 根因前现象是否已存在);同 bug 反复误诊=模型偏差非偶然;有金标准必做落点/机制/时序对比校准 RCA。待办:短期 SKILL 加时序证伪(**e2e#5 已验证无效**:agent 照做但问错问题[验 abort 早于联网,非孤儿早于 abort]+ 记忆先验警告被当假设证伪反噬[agent 称无 NEW_SCAN_RESULTS 推翻误路由,实为日志解读错]);真正解 = 中期工具层 filter_logs **强制注入因果起点行** + 长期 R5 运行时验证。详见 docs/踩坑记录.md #11。

**#12(2026-08)**:bug-RCA 流程假设错——"单 session 一次走完 + apply 验证够"。e2e#4/#5 穿帮:补丁 plausible 非金标 + apply 过 ≠ 修对(系统软件无单测,真机 oracle)。根因:假设"自动验证够 + 一次走完"。教训:bug-RCA 是迭代(假设↔证伪/补丁↔验证循环,对标 POPPER/RepairAgent);validate 只验 apply;memorize/report 验证后才做。现状:SKILL/agent 改工具箱+人在环(deeab6c/b6ba4bd)。详见 docs/踩坑记录.md #12。

**#13(2026-08)**:skill/prompt 受众错位——写给人(面向小白/项目叙事:踩坑编号/误诊史/e2e/对标论文/日期)而非模型。这些是噪声(烧 context 不指导行为)。教训:skill/prompt 面向模型(指令性),项目内部知识留 docs/踩坑+memory;description=触发器;教训提炼成可执行指令别叙事;区别于代码注释(面向小白)。背书 Anthropic Agent Skills 最佳实践。现状:SKILL/agent 去叙事(b6ba4bd)+ 记 [[skill-prompt-writing-style]] 铁律。详见 docs/踩坑记录.md #13。

**互补文档**:[docs/设计演变史.md](../../设计演变史.md) —— 本项目所有设计思路转变的演变脉络(从X→Y+为什么+依据),与踩坑记录互补(踩坑=弯路五段式,演变史=决策脉络)。
