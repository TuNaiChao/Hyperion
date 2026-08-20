# Multica 接入调研(2026-08-20 定稿)

**结论:可接、零改动、推荐 local_directory(in_place)+ bug 目录形态。** 全文见 `docs/research/multica/`(README 有裁决表,05 是接入蓝图+探针清单);源码 clone 在 `multica/`(gitignore),锚点 v0.4.31-2-g8c9b7503a。

关键源码事实(修正过两处网页推断):
- daemon 拉起 `opencode run --format json --dangerously-skip-permissions --dir <workdir> [--session <id>]`,prompt 走 stdin;HOME/XDG 不动、无沙箱、无审批(daemonws 零审批方法)→ `install --global` 三件套透明生效,`data/` 可写。
- **MCP 注入是 env `OPENCODE_CONFIG_CONTENT` 且主动合并用户全局 opencode.json 的 mcp 段**(runtime_mcp.go:270-275)—— 不是覆盖,是合并,要求 opencode ≥1.4.10(本机 1.18.18)。
- 会话续接存服务端 DB,同 issue+同 runtime+cwd 恒定才续;opencode 属「续接被拒不可检测」,fresh-session 兜底条件苛刻(零工具调用)。
- #2925 只读 gitdir = Codex 自带 Landlock 沙箱,与 opencode 无关;Multica 自己零沙箱。
- **避开**:别把姿势③(wire_opencode)接线的仓交给 Multica —— AGENTS.md 软链会被 marker 块穿透写入本仓根(runtime_config.go 追加语义,MUL-2753)。

待办:20 分钟真机探针(MCP+data/ 冒烟 → 一句话 e2e + 评论「生成补丁」续接验证),见 05 §五。用户侧触发词:「接入 multica」。
