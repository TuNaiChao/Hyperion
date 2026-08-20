# RootRecall 接入 Multica:结论与蓝图

> 综合网页调研(2026-08-19)与源码调研(`v0.4.31-2-g8c9b7503a`,2026-08-20)的最终结论。
> 前置阅读:[README.md](README.md) 的裁决对照表、[02-opencode接入与运行时.md](02-opencode接入与运行时.md)。

## 一、结论

**可以接,零改动,推荐接。** 设备侧已有 `opencode + RootRecall install --global` 三件套时,Multica
daemon 拉起 opencode 的方式(`opencode run --dir <bug目录> --session <id>`,prompt 走 stdin,HOME/XDG
不动、无沙箱)与我们真机验证过的 headless 路径完全同族 —— RootRecall 的 MCP 工具、路由表、skills、
`data/`、ephemeral 生命周期全部原样生效。需要做的只有 Multica 侧配置,加上一次 20 分钟的探针。

## 二、接入蓝图(源码修正版)

```
设备(已有,零改动):
  opencode 1.18.18(≥ MCP env 注入要求的 v1.4.10 ✓)
  RootRecall install --global → ~/.config/opencode/{opencode.json 的 mcp 块, AGENTS.md, skills/ 软链}
  ~/.local/bin/opencode 软链(PATH 兜底;daemon 另有登录 shell 解析兜底,02 §1)

Multica 侧(设备上执行一次):
  1. curl …/install.sh | bash && multica setup      # daemon 外连云端,设备成 runtime
  2. 确认 runtime online 且检测到 opencode(探测周期刷新,装完不用重启 daemon)
  3. 建 agent:provider = opencode(需要自定义路径时 MULTICA_OPENCODE_PATH 或 profile set-path)
  4. 建项目 → 资源选 local_directory → 指向 bug 工作目录(如 ~/bugcases/sdp-overflow/)
     模式 = in_place(bug 目录非 git 仓,worktree 模式本就不适用)

日常用法:
  issue:「结合问题描述 txt,分析 bluez 5.50.2 SDP 越界根因」
    → daemon: opencode run --format json --dangerously-skip-permissions --dir <bug目录>
    → 全局路由表载入 bug-rca → find_repo/repo checkout 开 ephemeral → 证伪循环 → 根因
  评论:「生成补丁」/「生成报告」
    → 同 issue 同 runtime,cwd 恒定 → --session 续接,上下文还在
    → export_patch/export_report(用户触发纪律,issue 评论即指令)→ data/bug_rca/ + bug_id 归档
  交付物取回:agent 在 issue 回复里贴补丁全文;或设备是自己的,直接拿 data/bug_rca/<repo>.patch;
    也可让它推分支(但建议代码生命周期全走 RootRecall 注册表,别混用 Multica 的 worktree 体系)
```

## 三、与 RootRecall 各机制的互操作(源码依据)

| 机制 | 结论 | 细节 |
|---|---|---|
| `install --global` 三件套 | ✅ 全部生效 | HOME/XDG 刻意不动(daemon.go:7046-7053);MCP 块被 daemon **主动读出合并**进注入配置且优先级不降(runtime_mcp.go:270-275) |
| 路由表(全局 AGENTS.md) | ✅ 生效,与任务级简报叠加 | Multica 每任务往 workdir 的 AGENTS.md **追加** marker 块(runtime_config.go:228-250;误清空事故 MUL-2753 已修),opencode 全局层+项目层同时注入 |
| 8 个 skill 软链(全局) | ✅ 共存 | Multica 的 skill 写 workdir 的 `.opencode/skills/`,冲突换目录、已存在拒写、任务后只清自己的(sidecar manifest,02 §6) |
| `data/` / ROOTRECALL_HOME 可写 | ✅ 源码级确认 | 无任何沙箱;上轮「待真机验证」降级为冒烟确认 |
| ephemeral 生命周期 | ✅ RootRecall 全权自管 | in_place 模式 workdir=bug 目录,Multica 的两套 worktree 体系均不介入(03 §七) |
| 用户触发交付物 | ✅ 更顺 | issue 评论天然就是「用户开口」;session 续接机制见 02 §4(opencode 续接失败不可检测,有 fresh-session 兜底) |
| repo sync / systemd timers | ✅ 互不干扰 | daemon 是独立进程;sync 动 baseline 的 fetch/ff,agent 走 ephemeral worktree,不同工作树 |
| 长任务 | ✅ | 无 turn 上限;墙钟兜底 AND 门控 daemon 存活,数小时健康长跑不误杀 |

**一个要避开的组合**:不要把「已用姿势③(`wire_opencode.sh`)接线的 bug 仓」交给 Multica 的
local_directory —— 姿势③在仓里放的 AGENTS.md **软链**指向本仓根同名文件,Multica 追加 marker 块时
`os.WriteFile` 会**穿透软链写进本仓根 AGENTS.md**(任务后清理会剜掉,但任务中途崩溃就残留在源文件里)。
给 Multica 的 bug 目录用全局接线(姿势①)即可,本来就够。

## 四、安全面板(不变的部分再说一遍)

- **无审批、无沙箱**:opencode 硬编码 `--dangerously-skip-permissions`(opencode.go:69);daemonws 协议
  零审批方法。bash/edit 对 daemon 用户全部资产开放 —— 含 RootRecall 的 `.env`(key)、记忆库、SSH key。
- **mat_ 任务 token**:agent 只能以「该任务」身份回写 issue(四元组绑定,task_token 表),不能冒充用户
  —— 这是唯一内建的收窄。
- **云端中转**:issue/prompt/进度都过 Multica 服务端;agent 的 custom env **存在服务端**、执行时下发
  (daemon-runtimes 文档明示「别假设 secrets 都留本地」)。RootRecall 的密钥不走这条路(只在本机 .env),
  但 issue 正文里的敏感信息要自觉。
- 缓解:Multica 账号视为全信任边界;敏感场景用 self-host(compose 三件套);或专用 Unix 用户跑 daemon
  (Multica 自己的注释都建议隔离交给部署边界,MUL-5578)。

## 五、真机探针清单(2026-08-20 已执行,全绿 ✅)

实测环境:本机已有 **self-host 栈**(docker:backend v0.3.34 + pgvector,`~/multica` 的 compose;CLI
v0.3.34 指向 localhost:8080)——非云版,prompt 全程不出本机。注意 v0.3.34 早于 local_directory 特性,
探针 3 用「问题描述内联在 issue」替代 bug 目录形态;升级 0.4.x 后可补测 local_directory。

1. ✅ **连通**:`multica daemon start` → runtime online,opencode 被自动探测(顺带探测到 codex/kimi;
   探测走 `~/.local/bin/opencode` 软链)。
2. ✅ **MCP + data/ 冒烟**:issue 指令调 `rootrecall_memory_recall` + `memorize` → 两工具返回原文贴
   回 issue;本地 CLI 复核条目 `c1f4184c` 第一命中(写入真实落库)。
3. ✅ **一句话开问全链**:RCA 6m13s / 51 工具调用 → 根因卡与 7 月 e2e 金标**逐点吻合**
   (lib/sdp.c:1261 SEQ32 INT_MAX 溢出);评论「根因已确认。生成对应的补丁。」→ 1m07s / 8 工具调用 →
   `<ROOTRECALL_HOME>/bug_rca/bluez.patch`(53 行三合一,与金标同款 —— 记忆变现走通远程链路)。
4. ✅ **用户触发纪律(计划外验证)**:RCA 阶段零落盘(bug_rca/ 目录都不存在);「生成补丁」评论后才
   export —— SKILL 新措辞在 multica 语境同样生效。
5. ✅ **评论触发 + session 续接**:后续评论任务日志见 `resume_session=true reuse_workdir=true`,
   argv 带 `--session ses_…`;opencode 续接失败不可检测的问题未出现。

**真机新发现(已修/已记)**:① daemon 环境缺模型 key —— opencode 全局配置的 apiKey 是
`{env:UNIONTECH_AI_API_KEY}` 引用,daemon 拉起的 opencode 没这个 env → "Invalid API key";修复用
`multica agent env set <agent-id> --custom-env-file`(0600 文件,存本机 self-host DB)。这是踩坑#34
(headless 环境变量)家族的第三个入口(终端手动 → systemd service → multica daemon)。② 交付物路径:
`data/bug_rca` 在 ROOTRECALL_HOME 设置时 reanchor 成 `<ROOTRECALL_HOME>/bug_rca/`(剥 data/ 前缀,
registry.py:55-67)。③ v0.3.34 差异:prompt 在 argv 而非 stdin(0.4.x 改 stdin);agent update 不支持
改 custom-env(要用 `agent env set`)。

遗留:daemon 保持运行(要停:`multica daemon stop`);探针 issue TES-3/TES-4 留档;升 0.4.x 可选
(只为 local_directory 的 bug 目录形态)。

## 六、开放问题(不阻塞接入)

- opencode「续接被拒不可检测」的兜底要求「零工具调用」—— 若续接静默失败且 agent 已调过工具,fresh
  重试不触发,上下文丢失但 workdir 还在;表现为 agent「忘了之前聊过」,靠记忆库 recall 兜底。可接受。
- issue 评论触发新任务时,`per-(issue,agent) 串行`会让连续多条评论排队逐条跑 —— 交互节奏上「一口气
  说三句」不如「一句等回复」。
- Multica 桌面端是 Electron 且自带 CLI 打包;若设备用桌面 App 注册 runtime,与 CLI daemon 二选一,
  别同时跑两个 daemon 连同一账号(未见双跑保护的说明)。
