# Multica 接入调研(2026-08-20 定稿)

**结论:可接、零改动、推荐 local_directory(in_place)+ bug 目录形态。** 全文见 `docs/research/multica/`(README 有裁决表,05 是接入蓝图+探针清单);源码 clone 在 `multica/`(gitignore),锚点 v0.4.31-2-g8c9b7503a。

关键源码事实(修正过两处网页推断):
- daemon 拉起 `opencode run --format json --dangerously-skip-permissions --dir <workdir> [--session <id>]`,prompt 走 stdin;HOME/XDG 不动、无沙箱、无审批(daemonws 零审批方法)→ `install --global` 三件套透明生效,`data/` 可写。
- **MCP 注入是 env `OPENCODE_CONFIG_CONTENT` 且主动合并用户全局 opencode.json 的 mcp 段**(runtime_mcp.go:270-275)—— 不是覆盖,是合并,要求 opencode ≥1.4.10(本机 1.18.18)。
- 会话续接存服务端 DB,同 issue+同 runtime+cwd 恒定才续;opencode 属「续接被拒不可检测」,fresh-session 兜底条件苛刻(零工具调用)。
- #2925 只读 gitdir = Codex 自带 Landlock 沙箱,与 opencode 无关;Multica 自己零沙箱。
- **避开**:别把姿势③(wire_opencode)接线的仓交给 Multica —— AGENTS.md 软链会被 marker 块穿透写入本仓根(runtime_config.go 追加语义,MUL-2753)。

**2026-08-20 探针已跑,全绿**(实测在本机 self-host 栈 v0.3.34,非云版):RCA 根因卡与金标逐点吻合、评论「生成补丁」→ 53 行三合一补丁落 `<ROOTRECALL_HOME>/bug_rca/`、用户触发纪律生效、`--session` 续接实测可见。真机新坑:daemon 缺 `{env:UNIONTECH_AI_API_KEY}` → "Invalid API key",修法 `multica agent env set <id> --custom-env-file`(踩坑#34 家族第三入口:终端→systemd→multica daemon)。daemon 在跑(停:`multica daemon stop`);升 0.4.x 可选(只为 local_directory)。

待办:~无阻塞项;若要 bug 目录形态(local_directory)需升 self-host 栈到 0.4.x。用户侧触发词:「接入 multica」。
