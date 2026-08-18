---
name: opencode-only-positioning
description: "2026-08-17 用户定:README 只保留 opencode 接入,codex/claude code 配置描述移除(暂不适配);一键脚本 quickstart.sh"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 7caed0c0-68ca-4a0f-a5e9-f45a5a0edf5a
  modified: 2026-08-18T02:19:23.291Z
---

2026-08-17 用户决定:对外文档(README)**只写 opencode 接入**,删除 codex / claude code 的配置描述 ——「暂不考虑适配这两个」。`config/codex_rootrecall.toml` 模板保留在仓里但 README 不再指路;CLAUDE.md 里「供 opencode(主)/ codex / claude code 调用」的能力表述未动(MCP 是标准协议,能力仍在,只是不再对外教怎么接)。

同日新增 `scripts/quickstart.sh` 一键配置,README 快速开始收成 clone + 一条命令:调 setup.sh(`.venv` 已存在则跳过,`--force` 重跑)→ 交互填 `.env` 必填 2 key(`read -rs` 不回显;`env_set` 删旧行再追加,避开 sed 对值里特殊字符的转义坑)→ `rootrecall models` 验证 → 可选 index → **可选接 bug 仓**(2026-08-17 晚收进脚本:独立 `scripts/wire_opencode.sh <bug仓>...` 给目标仓放两根线 —— `.claude/skills` 软链 + 生成的 `opencode.json` 注入官方 `mcp.rootrecall.cwd` 锚回本仓根,接完可在 bug 仓直接启动 opencode;JSON 注入用 `uv run python` 而非 jq,jq 不在 setup.sh 依赖里;目标已有不含 rootrecall 的配置 → 备份 .bak 拒写;幂等;探针 4 例全绿含 bug 仓内 `opencode mcp list` connected)→ opencode 接线自检(opencode 在装 / 软链 / 8 skill 数)。已实测两条路径:幂等重跑(已配全跳过)+ 全新 .env(假 key 写入正确、输出零泄露)。

2026-08-18 调研「别的 MCP 为什么免接线」(三因:全局配置注册一次 / npx·uvx 自足启动 / 无状态无家当)后提出全局注册备选方案(mcp 合入 `~/.config/opencode/opencode.json` + 8 skill 软链进 `~/.claude/skills/` + 10 agent block 全局合入),用户拍板**先不设置全局** —— 按仓接线保持干净:全局会让每个 opencode 会话(含无关项目)常驻 16 工具 schema + 8 skill 元数据 + 10 agent block,「过路费」太高。方案与代价完整记档在 `docs/mcp-guide.md`(MCP 使用+设置小白版:四层能力 / 免接线三因 / 三种接入姿势 / 最佳实践清单),README 文档段已挂链。

**Why:** 用户当前只在本机用 opencode,codex/claude code 适配描述对外是噪音;一键脚本降低上手门槛。全局注册同理 —— 当前用到哪个仓接哪个仓,YAGNI。

**How to apply:** 改 README/对外文档时不要再加回 codex/claude code 配置段(用户重提适配时再恢复);新增接入步骤时优先扩 quickstart.sh 而不是往 README 加手动命令;不要再主动提议全局注册(用户重提时翻案,方案在 docs/mcp-guide.md 姿势③)。相关 [[opencode-mcp-wiring]]。
