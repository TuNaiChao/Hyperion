# Multica 源码调研(接入 RootRecall 前的全面分析)

> 调研对象:multica-ai/multica,源码 clone 在本仓 `multica/`(gitignore,不入库)。
> 版本锚点:`v0.4.31-2-g8c9b7503a`(2026-08-20)——文中所有 file:line 均相对 `multica/`,以该版本为准。
> 调研动因:把「opencode + RootRecall」设备注册为 Multica runtime 节点,以后直接在 Multica(App/网页)上交互派活。
> 先前的网页级调研结论见本文 §二 的对照表——本次源码调研**修正了其中两处**,其余全部坐实。

## 文档索引

| 文档 | 内容 |
|---|---|
| [01-架构总览.md](01-架构总览.md) | 系统拓扑(云 server / daemon / 桌面·网页·移动端)、daemon↔云协议、任务管线与状态机、数据层、鉴权、realtime |
| [02-opencode接入与运行时.md](02-opencode接入与运行时.md) | daemon 怎么拉起 opencode:工具发现、精确命令行、环境变量、会话续接、MCP/skills 注入、沙箱与权限、workspace 目录 |
| [03-资源模型与worktree.md](03-资源模型与worktree.md) | 两种项目资源(github_repo / local_directory)、in_place 与 worktree 模式、`multica repo checkout`、#2925 只读 gitdir 事故、GC |
| [04-Go工程要点.md](04-Go工程要点.md) | Go 模块结构与依赖选型、分层纪律、注释文化、优雅停机、并发原语、观测性、构建与 self-host —— 以及对 RootRecall 可借鉴的点 |
| [05-RootRecall接入结论.md](05-RootRecall接入结论.md) | 综合裁决:接入蓝图(修正版)、风险面板、真机探针清单、与 RootRecall 各机制的互操作注意 |

## 一、30 秒结论

Multica = **Go 单模块后端(云 server + 设备 daemon 同仓两个 cmd)+ TS monorepo 前端**。daemon 以
`opencode run --format json --dangerously-skip-permissions --dir <workdir> --session <id>` 拉起 opencode,
prompt 走 stdin,进度以 JSON lines 回流、落 issue timeline;**HOME/XDG 刻意不动、无任何文件系统沙箱、
无任何审批机制(全部自动放行)**;MCP 配置通过 `OPENCODE_CONFIG_CONTENT` 环境变量注入并**预合并用户全局
opencode.json 的 mcp 段**。对 RootRecall:全局接线(`install --global` 三件套)透明生效,零改动可接入;
推荐 local_directory(in_place)+ bug 目录形态,续接/清理机制均不踩我们的生命周期。

## 二、上轮网页调研 → 源码裁决对照

| 上轮(官方文档)说法 | 源码裁决 | 证据 |
|---|---|---|
| daemon 以同用户、真实 HOME/XDG 拉起工具 | ✅ 坐实:HOME/XDG「deliberately not touched」 | daemon.go:7046-7053 |
| 审批自动应答 | ✅ 且比文档更绝对:**无任何审批 RPC**;opencode 硬编码 `--dangerously-skip-permissions` | opencode.go:69;daemonws 无 approval 方法 |
| #2925 只读 gitdir 是平台级风险 | 🔧 **修正**:根因是 Codex CLI 自带 Landlock 沙箱;daemon 本身零沙箱,opencode 不受影响 | codex_sandbox.go:64-79;repocache/cache.go:722-733 |
| MCP「可在 agent 配置里定义、传给工具」 | 🔧 **修正**:opencode 的注入载体是 env `OPENCODE_CONFIG_CONTENT`(非文件),且会**预读用户全局 `~/.config/opencode/opencode.json` 的 mcp 段一起合并** | runtime_mcp.go:270-275;opencode_mcp.go:90-110 |
| skills 写入 `.opencode/skills/`、不覆盖已有 | ✅ 坐实:写**任务 workdir** 下;冲突时换目录;只清理自己写的(sidecar manifest 跟踪) | execenv/context.go:150-205, 944-1004 |
| runtime 存 session id、同 issue 续接 | ✅ 坐实 + 细化:存服务端 DB;仅**同 runtime** 续接;opencode 属「无法检测续接被拒」的五个后端之一,兜底是 fresh-session 重试(条件苛刻) | handler/daemon.go:2452-2468;agent.go:349-355;daemon.go:7655-7733 |
| data/ 可写性待真机验证 | ✅ 源码级解除:无沙箱 + HOME 不动 → 设备上 `data/`(ROOTRECALL_HOME)可写 | 见 02 §7 |
| PATH 老坑(daemon 看不到 nvm) | ✅ 有三重兜底:启动探测 + 登录 shell 解析(`$SHELL -ilc`, 3s)+ `MULTICA_OPENCODE_PATH`/profile set-path | agents_probe.go:91;config.go:956-1010 |

## 三、与 RootRecall 相关的一页速览

- **接入形态**:设备装 `multica` CLI → `multica setup` → 建项目挂 `local_directory` 资源指向 bug 工作目录(in_place 模式)→ issue 一句话派活。详见 [05-RootRecall接入结论.md](05-RootRecall接入结论.md)。
- **会话续接**:同 issue 后续评论恢复同一 opencode session(`--session <id>`),「评论『生成补丁』→ agent 调 export_patch」链路成立。
- **最大风险**:安全面——bash/edit 全开、无审批、`mat_` 任务 token 可回写 issue;`.env`/记忆库/SSH key 均在可达范围。
- **本机就绪度**:opencode 1.18.18 ≥ 注入机制要求的 v1.4.10;`~/.local/bin/opencode` 软链已在(PATH 兜底)。
