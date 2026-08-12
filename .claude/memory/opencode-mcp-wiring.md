---
name: opencode-mcp-wiring
description: opencode 接 MCP server 的硬细节(2026-08-03 源码核实):配置语法/工具命名/坑 —— R3.1 把 Hyperion 能力作 MCP 工具给 opencode 调时踩的实证
metadata: 
  node_type: memory
  type: reference
  modified: 2026-08-12T07:33:48.919Z
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
---

R3.1 把 Hyperion 差异化能力做成 MCP 工具(`hyperion mcp serve` → search_codebase/recall/filter_logs)给 opencode 调。
2026-08-03 经 opencode 源码 + GitHub issues 核实的接线硬细节(别凭旧记忆,以这条为准):

**配置(`config/opencode_hyperion.json` 顶层 `mcp` 键,非 `mcpServers`):**
- `type: "local"` = stdio;`command` 是**单数组**(cmd+args 合并,**无独立 `args`**):`["hyperion","mcp","serve"]`。
- env 字段叫 **`environment`**(非 `env`);**local server 的 `environment` 是字面值,不展开 `{env:VAR}`**(`{env:VAR}` 只在 remote 的 headers/oauth 展开)。
  → codebase **不写配置,走进程 env 继承**:delegate 注入 `HYPERION_CODEBASE` → opencode 透传父 env → MCP 子进程 `os.environ` 读。
- `timeout` 单位 **ms**(listTools 用;显式设 ~10000,别吃 5s 默认)。源码 `opencode/packages/core/src/v1/config/mcp.ts:6-24`。

**工具命名:** `sanitize(serverName)+"_"+sanitize(tool)`(`catalog.ts:97`,单下划线,非 `__`)。server `hyperion` + `search_codebase` → 模型见 `hyperion_search_codebase`(callTool 仍发原名)。prompt nudge 用全名。

**坑(都核实过 issue 号):**
1. **MCP 留 primary agent**(hyperion-localize/repair 已是 `mode:primary`):`task` 子 agent 两坑 —— #33397(父 NDJSON 流滤掉子 session 事件,看不见但能跑)+ #16491(子 agent 里 MCP 列得出但执行不了)。
2. **listTools 每 LLM step 调一次**(#17099,延迟税 + 单次抖动永久踢出 server 无重连)+ 默认 **5s**(docs)/30s(code)→ Python 冷启紧,显式 `timeout:10000` + build_server 懒加载重依赖。
3. 单次响应 **< 8KB**(macOS pipe 缓冲,超了 write() 死锁);`PYTHONUNBUFFERED=1` 防 stdout 块缓冲。
4. 别开 `experimentalCodeMode`(塌缩 MCP 成单 execute)。

**Hyperion 侧落地(都对齐了):** `_resolve_codebase` 四段兜底(--codebase > HYPERION_CODEBASE env > config.code_index.repo > cwd);delegate 注入 HYPERION_CODEBASE(从 workspace `<repo>__<bugid>` 推导)+ PYTHONUNBUFFERED;_parse_stream 审计 tool_use 进 `DelegateResult.tool_calls`。详见 [bug-rca-design.md §6](docs/设计/bug-rca-design.md)。关联 [[delegate-already-localizes]] [[pitfall-log]]。

**⚠ 又一个 cwd 坑(2026-08-03 e2e 踩到):** opencode 把 MCP server 的 **cwd 设成 workspace/code**(InstanceState.directory),不是 Hyperion 根 → Hyperion config 里**相对的 `data/` 路径**(`store_path: data/memory`、LanceDB `data/code_index`)全解析到 `workspace/code/data/`,① 污染补丁(`git add -A` 连带 `data/memory/memory.db` + lancedb 进 diff,`validate_patch` apply --check 挂 → verified=False 不写 patch)② 记忆写到临时 workspace 库不持久(recall 查空)。**修法:`cmd_mcp` 在 `build_server` 前 `os.chdir(Hyperion 根)`**(_default_config_path().parent.parent)—— MCP server 是独立进程,工具都用绝对路径/表名(log_path 绝对、codebase 走 env、index 走 repo 名),不依赖 cwd。教训:被外部进程拉起的服务,其相对路径默认按"调用方 cwd"解析,要么 chdir 到自家根,要么 config 路径绝对化。

**⚠ http transport 不注册原生工具(2026-08-06 e2e #3 实测,踩坑 #10 候选):** 想用 streamable-http(warm 长进程,解 ③ cold-boot)给 opencode 接,配 `mcp.hyperion = {type:"http", url:"http://127.0.0.1:8765/mcp"}`,warm server 起活(/mcp 返 406)。**但 opencode 1.18.11 的 http MCP 客户端没把工具注册成原生** —— agent 看不到 `hyperion_*` 原生工具,只能读 config + 写 curl 脚本手工握 initialize→tools/call 绕(warm server 收到的 POST 是 agent 的 curl,非 opencode 原生 MCP 客户端;浪费一整轮 token)。**改回 local stdio 立刻原生注册**(opencode 日志 `evaluated permission=hyperion_memory_recall ... action=allow`,agent 直接 `tool_use` hyperion_*)。**机理**:listTools 只列工具定义(便宜,不加载 embedder)→ stdio 冷启也能在 timeout 内完成注册,工具原生可见;首次 recall/search *调用* 才冷启 embedder(慢),故 `timeout` 要 ≥120000ms(模板默认 10000 不够 → 首次调用超时)。**结论:opencode↔hyperion 走 local stdio(原生工具全触发,e2e #3 证 7 工具全原生);http 留作 cold-boot 优化但需先解 opencode http MCP 注册问题(config 格式?1.18.11 bug?)——未深究,stdio 够用。** ✅ 模板 `config/opencode_hyperion.json` 的 `timeout` 已 10000→120000(commit `6338e85`,防首次 recall/search 冷启超时)。

**⚠ opencode 不读 `.env`,LLM provider key 走 shell env(2026-08-12 compare e2e 踩到):** `opencode run` 报 `Invalid API key 401`(`ai.getdeepin.org`/uniontech-ai/deepseek-v4-flash-0731),但 `.env` 里 `UNIONTECH_AI_API_KEY` 明明有。原因:opencode 全局 provider 配 `"apiKey": "{env:UNIONTECH_AI_API_KEY}"`(~/.config/opencode/opencode.json),它从 **shell 环境变量**读,**不自动 load `.env` 文件**。新开 shell / Claude 起的后台 bash 默认没 source `.env` → key 空 → 401。backport e2e 当时能跑通是因为那个 shell 已 source 过。**修法:跑 e2e 前先 `set -a; . ./.env; set +a`(或 `export $(grep -v '^#' .env | xargs)`)把 .env 导进 shell**,再 `opencode run`。区别于上面的 MCP cwd 坑(那是 Hyperion MCP 子进程的路径),这是 opencode 自身 LLM provider 的凭证,两条独立的 env 线。
