---
name: rename-hyperion-to-rootrecall
description: 2026-08-17 项目改名 Hyperion→RootRecall 已全量落地;GitHub 侧阻塞在 token 零 scope
metadata:
  type: project
---

2026-08-17 用户拍板项目改名 **Hyperion → RootRecall**(简洁 + 突出「根因定位 + 记忆召回」特色)。

**已落地(commit cddeb02,143 文件,287 测绿 + ruff clean)**:
- 包名 `src/hyperion/` → `src/rootrecall/`(git mv 历史保留);pyproject name + entry point
- MCP server 名 `hyperion` → `rootrecall` → **opencode 工具前缀全变**:`hyperion_memory_recall` → `rootrecall_memory_recall` 等(skill allowed-tools 已同步)
- opencode agent block × 10(`hyperion-*` → `rootrecall-*`);`config/opencode_hyperion.json` → `opencode_rootrecall.json`(symlink opencode.json 重建);codex toml 同步
- env 变量 `HYPERION_CODEBASE`/`HYPERION_GIT_TIMEOUT` → `ROOTRECALL_*`(delegate 注入 ↔ mcp_memory 读取两侧同改)
- eval 集改名;历史交接卡(.claude/memory/)保留原名不追溯改写

**GitHub 侧(未完)**:`.env` 的 GITHUB_TOKEN 是 classic **零 scope**(x-oauth-scopes 空,GET 读 200 / PATCH 写 404 三路交叉验证),`gh repo rename` 被挡。待用户换带 repo scope 的 token 或网页手动改;改完需 `git remote set-url origin git@github.com:TuNaiChao/RootRecall.git` + push(积压 50078d5/c04df53/cddeb02)。

**装 gh(2.97.0)**:apt 无包且需 root → 官方二进制 sha256 校验(对 API digest)→ `~/.local/bin/gh`;直连 release CDN 断流,ghproxy.net 镜像下载成功。认证走 `GITHUB_TOKEN` env(gh auth login --with-token)。

**本机目录路径未改**(`~/Desktop/Agent/Hyperion`):改路径会动 Claude Code 记忆 slug(-home-tnc-Desktop-Agent-Hyperion)+ 两台机协作约定,待用户拍板;`tests/fixtures/lsp_c/compile_commands.json` 是 gitignored 本机生成物(绝对路径随机器,gen_compile_commands.sh 重跑即得),不受改名影响。
