---
name: opencode-serve-persistent-research
description: "③ #55 opencode serve persistent 调研结论(2026-08-06 agent 查实,待单独一轮实施):opencode -s/--session 精确续早有;serve+run --attach REST(POST /session/{id}/message、SSE /event)是官方免 MCP cold boot 路径,Critique 生产在用;真痛点=MCP cold boot(hyperion mcp serve 加载 sentence-transformers ~1.2GB/Call,K1+K2≈4×/bug);实施=新 OpenCodeServeDelegate 后端。本轮已做 P0 记忆闭环(②[a]+②[b]),③ 拆出单独一轮。"
metadata:
  node_type: memory
  type: reference
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-08-07T09:03:34.020Z
---

2026-08-06 调研 ③ opencode serve persistent(#55)的结论(agent 查实,供单独一轮实施直接用,不重查)。

## 修正两个前提
1. **精确 session 续接 opencode 早就有**:`-s/--session <id>`(本机 v1.18.11 `opencode run --help` 实测:`-s, --session  session id to continue`)。当前 bug_rca 用 `--continue`(续"最近")其实在**单 bug 单 cwd** 流程里已够准(只有一个 session)→ `-s` 替换近乎零增量价值,**不是 ③ 的价值点**。
2. **`opencode serve` + `run --attach` 是官方"免 MCP cold boot"路径**:官方 CLI 文档原文"You can also attach to a running `opencode serve` instance to **avoid MCP server cold boot times on every run**"。

## 真痛点 = MCP cold boot(非二进制加载)
每次 `opencode run` 子进程都重拉 `hyperion mcp serve`,它 import hyperion + 加载 sentence-transformers embedder(~1.2GB)。bug_rca 一条 bug K1+K2≈4 次 delegate call = **4× MCP 冷启**。这才是延迟大头(官方原话背书)。正确性不受影响(当前 e2e GREEN),③ 是**纯性能优化**。

## serve 的真实 API 表面(官方 https://opencode.ai/docs/server/,REST + JSON + SSE)
- 启动:`opencode serve [--port 4096] [--hostname 127.0.0.1]`;鉴权 `OPENCODE_SERVER_PASSWORD`(HTTP basic,user 默认 opencode)。**端口 `--help` 写默认 0/随机、docs 表写 4096,有出入 → 生产显式传 `--port`**。
- 精确续 session:`POST /session` 建新 → 拿 `.id`;反复 `POST /session/{id}/message`(body `{model?, agent?, parts}`,返 `{info, parts}`)。异步 `POST /session/{id}/prompt_async`(204)+ `GET /event` SSE 订阅进度。
- 其他:`GET /session` 列、`POST /session/{id}/fork`、`POST /session/{id}/abort`、`POST /instance/dispose` 释放。
- 客户端:官方 SDK 只 JS/TS(`@opencode-ai/sdk`,`createOpencodeClient({baseUrl})`);Python **社区版**(anomalyco/opencode-sdk-python)非官方 → **Hyperion(Python)用 httpx 直连 REST**(端点十几个、全 JSON、鉴权简单),类型按需从 `GET /doc`(OpenAPI)生成。

## 生产先例:Critique Coding Agent API(https://www.critique.sh/blog/coding-agent-api-persistent-sessions,2026-06)
E2B 沙箱里起 `opencode serve` + `POST /session/{id}/message` 多轮 follow-up = **正是 Hyperion 场景**。关键借鉴:
- **状态机**:idle / running / completed + `sessionExpiresAt` + `POST {endSession:true}` 主动释放(避空转计费/沙箱超时;E2B 空闲超时是踩到的边界)。
- **故障回退(必抄)**:session 失效/aged out → messages 路由返 **conflict** → 降级回"新 session"(宁可起新沙箱也不静默 corrupt repo state)。
- **并发**:Critique 是**串行 follow-up**;多 bug 并行 = **多 session.id**,不要并发写同一 session(并发写安全性官方未明确)。

## 实施形态(对齐 CodingAgentDelegate 抽象)
新后端 `OpenCodeServeDelegate(CodingAgentDelegate)`(delegate.py 核心,窗口展示区):
- 长驻 `opencode serve` 生命周期管理(起/健康检查 `GET /global/health`/停);
- httpx 客户端打 REST;session.id 记账(per-bug workspace → 一个 session);
- `POST /session/{id}/message` 喂 prompt → 解析 `{parts}` 拿 assistant 文本 → 复用现有 `_extract_json` 抠 schema;
- **失效降级**(session aged out → 降级新 session;serve 挂 → 降级回 `OpencodeDelegate` 子进程模式);
- 配置:`delegate.backend: opencode_serve`(或 opencode 配 serve 档)。
- **别新造持久化层**:opencode session 持久化在它的 SQLite(`opencode.db`)+ storage JSON,走 HTTP 表面,**别直接读写它的私有 DB schema**(跨版本会变)。

## 归属:拆出单独一轮(用户 2026-08-06 拍板)
本轮做了 P0 记忆闭环([[similar-bug-recall-roadmap]] 的 ②[a]+②[b]);③ 是纯性能优化 + 新后端 + delegate.py 核心,单独一轮聚焦。本条 memory + backlog #55 升级是那轮的地基。关联:[[delegate-already-localizes]] [[multi-stage-delegate-decision]] [[pitfall-log]]。

---

## ⚠️ 2026-08-07 pivot 后判 obsolete(以此为准)

harness 转向(opencode 主驱动 + Hyperion 当 MCP server)后,本条 ③ **不再需要**:
1. **原痛点前提消失**:delegate 反复 cold-boot opencode(K1+K2≈4×/bug)只存在于已 deprecate 的 legacy `hyperion bug-rca` 命令(cli.py:359-361 stderr deprecate 警告);新主路径 opencode 由用户启动长驻,无 per-stage respawn。
2. **hyperion mcp serve 自身冷启已很低频**:重模块全 lazy(embed.py:245 `from sentence_transformers` 在方法体内);build_server 启动只装 FastMCP + memory,不加载 torch;默认 `openai_compatible` embedder 走 RemoteEmbedder **0 次 torch 加载**。
3. **D0 streamable-http 已覆盖**(cli.py:323-339 warm 长进程);唯一遗留 = opencode 1.18.11 http MCP 不注册原生工具(踩坑#10),那是 opencode 侧 bug,不该用 persistent session 绕。

前沿对照(WebSearch 2026-08):MCP cold-start 业界主流解法 = **lazy-load tools by intent + warm 进程 + transport 选择**(Stacklok/Anthropic/Focused.io),无 persistent-session 编排层(那反是 orchestrator 思路)。性能优化精力转:**按 intent lazy-load MCP 工具**(减 context + cold-start,Anthropic fix)+ 推 stdio→http(待 opencode 解注册)。backlog #55 同步标 obsolete。
