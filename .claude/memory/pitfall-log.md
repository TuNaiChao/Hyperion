---
name: pitfall-log
description: "踩坑记录文档(docs/踩坑记录.md)位置 —— 项目走过的弯路汇总;设计前先查、踩坑后往上加"
metadata:
  node_type: memory
  type: reference
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-08-03T02:31:25.423Z
---

`docs/踩坑记录.md` 是专门记录**走过的弯路 / 踩过的坑**的累积文档(每条五段:现象 → 弯路 → 根因 → 教训 → 现状)。

**何时查 / 何时写**:① 设计新模块前先翻一遍(避免重复踩已知坑);② 做了设计反转 / 删了已建代码 / 用户指出过度设计 / 调研推翻既有方案 时,往上加一条(模板在文档末尾)。

**首条 #1(2026-07)**:patch 投票 rerank 的**三段反转**(当主路径 → 降级默认关兜底 → 整体移除)。根因:无 oracle 时投票平凡 + 现代 SOTA 转单轨迹+执行验证;"默认关兜底"是伪安全(死代码是债)。关联 [[rerank-mechanism-where-it-shines]]。

**#2(2026-07)**:Hyperion 侧定位漏斗(file→function→line)建到一半发现**与委托目标 opencode 重复定位**(double localization)→ 整套砍,改"opencode 自主定位 + Hyperion 把记忆/检索/日志过滤做成 MCP 工具给它调"。根因同 #1:**设计前没核前提**(opencode 本就会定位;Hyperion 是委托型不是独立 agent,误搬 Agentless 漏斗)。教训:建 Hyperion 能力前先问"opencode 会不会?",会→做工具别重造(见 [[delegate-already-localizes]])。

**#3(2026-08)**:delegate 子进程流式读用 `async for line in stream`(readline)有**隐式 64KB 行长限** —— opencode `--format json` 读大文件/大日志时单条事件 >64KB 直接 ValueError 崩(`Separator is not found, and chunk exceed the limit`)。R2 没踩到是没 `--log`;R3.1 加大日志才触发。改 `read(n)` 块读 + 之后统一 `splitlines`(行长无上限)。同 #1/#2 通病:**没核前提**(readline 行长限)就用 API。关联 [[opencode-mcp-wiring]](同源:opencode MCP 大输出还有 <8KB pipe 死锁 / listTools 5s 超时等坑)。

**#4(2026-08)**:被外部进程拉起的服务,相对路径按"调用方 cwd"解析。opencode 把 MCP server cwd 设成 workspace/code → Hyperion config 里相对 `data/` 路径(memory SQLite / LanceDB)解析到 `workspace/code/data/` → ① git add -A 连带进补丁污染(validate_patch 挂)② 记忆写临时库不持久。修法:`cmd_mcp` build_server 前 `os.chdir(Hyperion 根)`。教训:被 spawn 的服务要么 chdir 回自家根,要么 config 路径绝对化。关联 [[opencode-mcp-wiring]]。

**#5(2026-08)**:workflow 盲信 LLM 结构化输出 schema。glm-5.2 的 `evidence[].line` 偶尔给逗号多行串("3067,4105,5980")而非 int → `Evidence(line=<串>)` pydantic 崩整个 bug-rca。schema 是契约不是保证;LLM 输出→pydantic 严格模型边界必加防御 coerce(取首 int)+ 单条坏 try/except 跳过。这类方差性 bug e2e 才暴露(单测难复现),长尾逐个 coerce。同 #1-#4 通病:**没核前提**(LLM 会守 schema)。
