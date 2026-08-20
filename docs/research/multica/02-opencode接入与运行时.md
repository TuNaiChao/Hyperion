# daemon 如何拉起 opencode(运行时接入全链)

> 源码锚点 `v0.4.31-2-g8c9b7503a`;file:line 相对 `multica/`,Go 侧在 `server/`。
> 本文按九个问题展开,每个都是 RootRecall 接入的实际关切;§10 汇总「对 RootRecall 的影响」。

## 1. 工具发现:opencode 在哪、怎么被找到

- 探测入口 `probeAgentCLIs`(`server/internal/daemon/agents_probe.go:91`,var 可测试桩):每个 provider 调
  `probe(envVar, defaultCmd, modelEnv)` —— **opencode 是 `MULTICA_OPENCODE_PATH` / `"opencode"` /
  `MULTICA_OPENCODE_MODEL`**(agents_probe.go:161),另有 claude/codex 及 ~20 个 provider。
- 解析链:先 `exec.LookPath`(config.go:727-741,跳过 `~/.multica/hooks` 防递归);**裸命令名找不到时,
  登录 shell 兜底** —— fork `$SHELL -ilc <script>`(3s 超时,config.go:956-1010),脚本做 `command -v` +
  拒绝非绝对结果 + `pwd -P` 规范化(nvm/fnm 的 multishell 目录退出即消失,故必须规范化),结果再用 daemon
  自己的 LookPath 复核;按 PATH+SHELL+HOME 缓存 30 分钟。**这正是 GUI 拉起的 daemon 看不到 nvm PATH 的
  三重兜底**(另两重:`MULTICA_OPENCODE_PATH` env、`multica runtime profile set-path`——后者是纯本机
  映射,「never leaves the machine」,cmd_runtime_profile.go:28-32)。
- 探测不是一次性的:`agentDiscoveryLoop` 周期刷新(agents_refresh.go:63)——daemon 运行中装的 CLI 会被
  自动发现(MUL-5439);版本检测独立(10s 上限)。

## 2. 精确命令行(opencode 后端)

后端 `opencodeBackend`(`server/pkg/agent/opencode.go:43-47`),Execute 构建(opencode.go:69-100):

```
opencode run --format json --dangerously-skip-permissions
           [--dir <workdir>] [--model <model>] [--variant <thinking>] [--session <id>]
           [<用户 custom_args,协议旗标已滤除>]
```

- **prompt 走 stdin**,永远不用 `-p`/`--prompt`(Windows argv 长度问题 #6538,且不进进程列表可见的
  ps;opencode.go:101-111),专用 goroutine 写完即关 stdin  signaling EOF。
- `--dir <workdir>` 锚定 opencode 的项目发现(AGENTS.md 向上爬 + `.opencode/skills/`);**PWD 也同时被
  覆盖**(§3)。`--max-turns` 明确不支持(仅告警,opencode.go:94-96)。
- **SystemPrompt 从不内联**:运行时简报(CLI 目录/工作流/skills 索引)以**每任务 AGENTS.md** 交付
  (opencode.go:88-93,MUL-5392)——详见 §6。
- 会话续接 = `--session <id>`(无 --continue)。进程组管理:自成进程组,SIGTERM→5s 宽限→SIGKILL
  (#4533,opencode.go:225-244);`cmd.Dir = opts.Cwd`(:129-131)。
- **daemon 拥有并从用户 custom_args 里滤除的旗标**(opencodeBlockedArgs,opencode.go:36-41):
  `--format`、`--dir`、`--variant`、`--dangerously-skip-permissions`。各家后端各自定义,统一注册在
  `launchPrefixBlockedArgs`(launch.go:303-325);滤除逻辑支持「带值/独立/可选值」三模式并先去 shell 引号
  (claude.go:972-1025)。参数顺序固定:`<Path> <fixed_args…> <协议参数…> <ExtraArgs…> <custom_args…>`
  (launch.go:56-64)。
- 输出协议:stdout JSON lines,事件 `step_start|text|tool_use|error|step_finish`(opencode.go:656-754);
  **fail-closed 守卫**:流结束若无终止信号(悬挂 step 等)按失败处理(:463-495)。`sessionID` 从每个事件
  里刮取(:405-407)。

## 3. 子进程环境变量

组装顺序(daemon.go runTask,7033-7136):

1. 任务级变量:`MULTICA_TOKEN`(mat_ 任务 token)、`MULTICA_TASK_CONFIG_ROOT`(每任务私有 ~/.multica
   替代)、`MULTICA_TASK_WORKSPACES_ROOT`、`MULTICA_SERVER_URL/DAEMON_PORT/WORKSPACE_ID/AGENT_NAME/
   AGENT_ID/TASK_ID/TASK_SLOT`、每任务 TMPDIR(daemon.go:151-167)。
2. **PATH 前插 multica 自身 bin 目录**(daemon.go:7033-7040)——agent 会话里 `multica` 命令永远可用。
3. provider 专属:codex 每任务 `CODEX_HOME`、cursor `CURSOR_DATA_DIR`、openclaw/reasonix 各自的;
   **opencode 无任何专属 HOME/XDG 覆盖**。
4. **HOME / XDG 刻意不动**(daemon.go:7046-7053 原注释:「provider tools such as gh, aws, kubectl… continue
   resolving the daemon user's existing state」,MUL-5578)——继承 `os.Environ()` 全量。
5. 后端层再清洗:剥掉继承来的所有 `MULTICA_*`(claude.go:927-931)后重新拼任务集;opencode 额外设
   `PWD=<workdir>`(「OpenCode 无 --dir 时优先看 PWD」,opencode.go:133-140)与 §5 的
   `OPENCODE_CONFIG_CONTENT`。

**对 RootRecall**:`ROOTRECALL_HOME`、`.env`(由 rootrecall 进程自加载)等不受任何影响;全局
`~/.config/opencode/*` 照常读取。

## 4. 会话续接(「同 issue 评论接着聊」的机制)

- session id 从事件流刮取 → `Result.SessionID` → **存服务端 DB**(`agent_task_queue.session_id` 列 +
  `work_dir`/`retired_session_id`/`session_rollout_missing`,agent.sql:930-937),运行中也实时上报
  (UpdateAgentTaskSession)。设备本地无 opencode session 存储。
- 下次领取同 issue:claim 响应带 `PriorSessionID`,**仅当上次 runtime == 本次 runtime**(handler/daemon.go:
  2452-2468);`GetLastTaskSession` 查询显式排除中毒 session(iteration_limit、context overflow、
  api_invalid_request 等,agent.sql:950-1030 长注释)。
- 发射前两道门(gateResumeToReusedWorkdir,daemon.go:5664+):workdir 不复用则丢 session(CLI 后端的
  session 键在 cwd 上);session 存储不可达也丢。in_place local_directory 的 cwd 恒为用户目录,天然稳定。
- **opencode 属于「无法检测续接被拒」的五个后端之一**(resumeRejectionUndetectable,agent.go:349-355:
  antigravity/copilot/cursor/deveco/opencode)。兜底 fresh-session 重试条件苛刻(daemon.go:7655-7733):
  `failed + 有 priorSession + 零工具调用`(避免副作用重复),旧 id 标记 retired 永不再选。
- **推论**:「评论『生成补丁』」场景成立——同 issue、同 runtime、cwd 恒定 → `--session` 续接;若续接
  静默失败,agent 以新 session 冷启动但 workdir 还在(改动文件未丢),只是对话上下文降级。

## 5. MCP 注入:OPENCODE_CONFIG_CONTENT(不是写文件)

这是本次调研对上轮网页结论的**最重要修正**。daemon 侧组装(daemon.go:6502-6569 + runtime_mcp.go):

1. 起点 = agent 级 McpConfig(服务端下发);
2. **与设备本地用户的全局 opencode 配置合并**:`$XDG_CONFIG_HOME/opencode/opencode.json` 的 `mcp` 段
   会被读出参与合并(runtime_mcp.go:270-275;claude 对应 ~/.claude.json、codex 对应 config.toml)——
   **合并发生在设备上,「runtime URLs, headers, commands, and env values never need to leave the
   machine」**(runtime_mcp.go:18-24);同名时 agent 级赢。
3. 远程 MCP broker:每个已审批的远程连接在 `127.0.0.1` 起本地反代(随机端口+路径 token),SSRF 防线
   (https-only/无 userinfo/公网 IP/allowlist,pkg/remotemcp/client.go:30-75),每任务上限 1 MiB 请求 /
   256 次调用 / 8 并发(remote_mcp_broker.go:26-29)。
4. **投递给 opencode 的方式 = 环境变量**:`OPENCODE_CONFIG_CONTENT={"mcp":…}`(opencode.go:160-170 +
   opencode_mcp.go:90-110)。设计理由(opencode_mcp.go:73-89):workdir 跨轮复用,agent/用户自己写的
   `<workdir>/opencode.json` 必须存活;env 注入在「local」层、项目配置**之后**合并 → daemon 条目优先。
   要求 opencode ≥ **v1.4.10**(本机 1.18.18 ✓)。配置按 opencode schema 严格校验
   (DisallowUnknownFields,opencode_mcp.go:122-308),兼容 `mcpServers`/原生 `mcp` 两种输入形状。

**对 RootRecall**:我们的 `mcp.rootrecall` 块在全局 opencode.json —— daemon 会读它、合并进注入配置、
且优先级不降。**零改动生效**,连「daemon 注入覆盖用户配置」的担心都反了:它主动合并用户全局条目。

## 6. skills 注入与运行时简报(AGENTS.md)

- **skill bundles**:服务端下发 `Agent.SkillRefs`,daemon 下载进磁盘缓存
  `{WorkspacesRoot}/.skill-cache/v1`(daemon.go:615;内容寻址 = 对源/名称/描述/内容/全部打包文件的
  sha256 清单,pkg/skillbundle/hash.go:43-79)。
- **写入位置(按 provider)**(execenv/context.go:122-142 的表):opencode → **`{workdir}/.opencode/skills/
  {name}/SKILL.md`**(:361-370;理由就是 `opencode run --dir` + PWD 覆盖,MUL-2416);claude →
  `.claude/skills/`;codex/hermes → 各自每任务 HOME。
- **不覆盖用户内容**:slug 去重 + 与已存在目录冲突时分配新目录(allocateCollisionFreeSkillDir,
  context.go:944-1004);所有写入走 recordWriteFile,**路径已存在即拒绝**(errPathPreExists)。
- **运行时简报**:每任务往 workdir 写 `AGENTS.md`(opencode 用 AGENTS.md,claude 才用 CLAUDE.md;
  runtime_config.go:161-217)—— CLI 目录/工作流/skills 索引全在这,所以 `--prompt` 系统提示保持空。
- **任务后清理**:所有写入登记进 sidecarManifest(execenv.go:428-465);local_directory 任务收尾执行
  `CleanupRuntimeConfig`(剜掉 AGENTS.md 里的 marker 块)+ `CleanupSidecars`(删 `.opencode/skills/`
  等它写的目录)——**用户目录 round-trip 回任务前字节**(daemon.go:6895-6947)。只清自己写的清单内文件。

**对 RootRecall**:任务级 `.opencode/skills/` 与我们全局 `~/.config/opencode/skills/` 8 个软链共存
(opencode 两层都发现);Multica 写的 AGENTS.md 是项目层,与我们全局层的路由表叠加,清理时只剜 marker
块。**互不踩**。唯一交互点:bug 目录里任务期间会短暂出现 AGENTS.md/.opencode/ —— 不影响 RootRecall。

## 7. 沙箱与权限:没有沙箱,没有审批

- **Go daemon 自身零文件系统沙箱**:全树 grep landlock/seccomp 只命中一处**解释为什么不做的注释**
  (execenv/codex_sandbox.go:64-79:「Landlock could enforce workspace-write here, but enforcing it
  meant redirecting HOME/XDG per task…」);隔离策略 = 交给部署边界(VM/容器/专用 Unix 用户,MUL-5578)。
- **Codex 的沙箱是 Codex 自己的**,且 Linux/Windows 默认 `danger-full-access`(codex_sandbox.go:98-132);
  macOS 修过 seatbelt DNS bug 的版本才 workspace-write。#2925 事故的根因是 Codex workspace-write 把
  worktree 的外部 gitdir 判只读 —— Multica 的修复是**结构性的**:`WorktreeParams.IsolatedGitMetadata`
  改建「.git 在 workdir 里的本地 clone」(repocache/cache.go:722-733,`git clone --local --no-checkout`),
  并按平台给 codex 导出 `MULTICA_REPO_CHECKOUT_MODE=isolated`(daemon.go:95-119)。opencode 无此负担。
- **权限旗标全部硬编码「放开」**:opencode `--dangerously-skip-permissions`(opencode.go:69);claude
  `--permission-mode bypassPermissions` + 禁 AskUserQuestion(claude.go:717-731);codex 审批 RPC 一律
  自动 `{"decision":"accept"}`(codex.go:2560-2617);ACP 系后端 `session/request_permission` 本地自动
  `allow_once`(hermes.go:1033-1105);cursor/grok/qoder 全是 `--yolo` 系。
- **daemonws 协议里没有任何审批方法**(hub.go/wsrpc.go grep approval/permission 零命中)——「远程人工
  审批」在产品里不存在,文档没骗人但也没强调。

**对 RootRecall**:上轮「data/ 可写性待真机验证」的疑虑**源码级解除**——无沙箱 + HOME 不动,设备上
`data/`(ROOTRECALL_HOME)与安装根全部可写。同时安全面定论:跑在 daemon 用户下的 agent 对该用户全部
资产(含 `.env`、记忆库、SSH key)有完整读写权,唯一边界是部署边界。

## 8. workspace 目录与生命周期

- 根:显式覆盖 > `MULTICA_WORKSPACES_ROOT` > 默认 `~/multica_workspaces`(config.go:649-670)。
- 每任务:`{root}/{workspaceID}/{shortTaskID}/`(execenv.go:327-426)含 `workdir/ output/ logs/
  multica-config/`(0700,任务 token 私有目录)+ provider 附加(codex-home 等)。**Prepare 先删旧 envRoot
  再建**(task id 唯一,:350-355);同 (agent, issue) 的 workdir 跨轮复用(Reuse)是常态 —— agent 在
  workdir 写的任何东西(含 opencode.json)跨任务存活。
- 根级 fail-closed 标记 `{workspacesRoot}/.multica/daemon_task_context.json`:嵌套 `multica` CLI 调用
  自动改用任务 token 而非用户 PAT(context.go:17-54)。
- GC 循环(gc.go,默认 2h 一轮):TTL 24h/孤儿 72h/工件 12h/repo 缓存 30d;活动 root 有保护标记。

## 9. 任务工作目录怎么定(按资源类型)

- **默认(github_repo 或无资源)**:workdir = `{envRoot}/workdir`,**建出来是空的**——「The workdir
  starts empty (no repo checkouts). The agent checks out repos on demand via `multica repo checkout
  <url>`」(execenv.go:324-326)。本地 checkout 端点以任务 token 鉴权、仅执行窗口开放
  (daemon.go:7279-7291)。
- **local_directory 资源**(internal/daemon/local_directory.go):`{local_path, daemon_id,
  execution_mode}`;daemon_id 把路径钉死到一台 daemon 注册(同路径不同机器 = 不同资源,
  project_resource.go:130-134)。
  - *in_place*(默认):**workdir 就是用户目录本身**;按 symlink 规范化后的真实路径上进程内互斥锁串行
    (local_directory.go:398-537),争用时任务进 `waiting_local_directory` 状态;GC 永不删用户目录。
  - *worktree*:在 envRoot 里从用户仓快照建一次性 worktree(细节见 03 文档)。
  - 未知 execution_mode **直接失败**而不是静默 in_place(:71-85)。

## 10. 对 RootRecall 的影响一览

| 关切 | 裁决 | 依据 |
|---|---|---|
| 全局三件套(mcp/AGENTS.md/skills)生效? | ✅ HOME/XDG 不动;MCP 还被主动合并进注入配置且优先级不降 | §3/§5 |
| data/ 可写? | ✅ 无沙箱无隔离,daemon 用户全权 | §7 |
| 会话续接(评论接着聊) | ✅ 机制成立(同 issue+runtime+cwd);opencode 续接失败不可检测,有 fresh-session 兜底 | §4 |
| 权限 | ⚠️ 全自动放行,无审批 —— 人在环只存在于 issue 文字层 | §7 |
| opencode 版本 | ✅ ≥1.4.10 即可(MCP env 注入);本机 1.18.18 | §5 |
| 任务目录污染 | ✅ sidecar 清理保证 round-trip;只清自己写的 | §6 |
| 长任务 | ✅ 无 turn 上限;墙钟兜底 AND 门控 daemon 存活 | §2/01§四 |
| PATH/nvm | ✅ 三重兜底(登录 shell 解析/env/profile set-path) | §1 |
