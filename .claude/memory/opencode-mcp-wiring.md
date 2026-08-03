---
name: opencode-mcp-wiring
description: opencode 接 MCP server 的硬细节(2026-08-03 源码核实):配置语法/工具命名/坑 —— R3.1 把 Hyperion 能力作 MCP 工具给 opencode 调时踩的实证
metadata: 
  node_type: memory
  type: reference
  modified: 2026-08-03T02:20:16.400Z
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
