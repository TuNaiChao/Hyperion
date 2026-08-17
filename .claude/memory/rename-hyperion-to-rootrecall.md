---
name: rename-hyperion-to-rootrecall
description: 2026-08-17 项目改名 Hyperion→RootRecall 全量收口(代码+GitHub+本机目录+记忆 slug)
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

**GitHub 侧(已收口)**:用户网页手动改名 TuNaiChao/Hyperion → **TuNaiChao/RootRecall**;`remote set-url` + push 完成(196f7e2..cddeb02,含路由矩阵/docs 对齐/改名三 commit)。之前 API 改名被挡的根因:`.env` 的 GITHUB_TOKEN 是 classic **零 scope**(x-oauth-scopes 空,GET 200 / PATCH 404 三路交叉验证)—— 读公开仓不需要 scope,写才需要;后续要用 gh 写操作记得先换 token。

**装 gh(2.97.0)**:apt 无包且需 root → 官方二进制 sha256 校验(对 API digest)→ `~/.local/bin/gh`;直连 release CDN 断流,ghproxy.net 镜像下载成功。认证走 `GITHUB_TOKEN` env(gh auth login --with-token)。

**本机目录已改**(2026-08-17 同日):`~/Desktop/Agent/Hyperion` → **`~/Desktop/Agent/RootRecall`**,与 CLAUDE.md 两台机路径约定一致。mv 后修的三件事(下次 macOS 那台同步改名时照抄):① `.venv` 原地重建(entry-point shebang 写死绝对路径,`rm -rf .venv && uv sync --extra mcp --extra code-review-graph --extra providers`);② `tests/fixtures/lsp_c/gen_compile_commands.sh` 重跑(gitignored 本机生成物,绝对路径随机器);③ `~/.claude/projects/` 下建新 slug `-home-tnc-Desktop-Agent-RootRecall` 目录 + 软链 `memory -> 仓内 .claude/memory`(旧 slug 目录的断链删除,历史 jsonl 留在旧 slug 不迁)。**注意**:uv sync 裸跑会剥 extras(mcp/code-review-graph/providers),必须带 `--extra` 列表(踩坑:extras 名是 `code-review-graph` 不是 `graph`)。
