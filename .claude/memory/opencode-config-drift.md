---
name: opencode-config-drift
description: "opencode 实际加载 cwd 根 opencode.json(现已 symlink → config/opencode_hyperion.json 单源真相)。历史上两文件都在 git 无同步机制会漂移,2026-08-12 backport e2e 踩到;同日用 symlink 根治。"
metadata: 
  node_type: memory
  type: reference
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-08-12T05:17:39.940Z
---

**opencode 从 cwd 根读 `opencode.json`,不从 `config/opencode_hyperion.json` 读。** 两个文件都在 git 里,但**没有任何同步机制** —— 改模板不碰实际加载文件,改实际加载文件不碰模板,必然漂移。

## 2026-08-12 backport e2e 踩到

加 `hyperion-backport` agent 时只改了模板 `config/opencode_hyperion.json`(commit `9311973`),没动 cwd 根的 `opencode.json`。e2e 跑前发现 opencode 根本看不到 `hyperion-backport` agent —— 实际加载的 `opencode.json` 还停留在旧版(缺 backport + 还引用 2026-08-10 已撤的 `hyperion_filter_logs`)。**修法:`cp config/opencode_hyperion.json opencode.json`(模板是正确源头),commit `3a12721`。**

## 这是 [[opencode-mcp-wiring]] 的延伸(踩坑 #10 同源)

opencode 配置有两处真理源、无同步,是"配置漂移"类 bug 的温床。每次:
- 改 agent / MCP / 工具配置 → **两处都得改**(模板 + cwd 根),或改完一处立刻 `cp` 另一处。
- fresh clone 后跑 opencode → 先确认 cwd 根 `opencode.json` 与模板一致,别默认模板生效。

## ✅ 已根治(2026-08-12,symlink 单源真相)

`opencode.json` 从普通文件(100644,git blob `933af08`)改为 **symlink → `config/opencode_hyperion.json`**(git mode 120000)。从此**只有一份真理源**:改模板一处,opencode 透过 symlink 立刻生效。

**验证两关全绿**:
1. `opencode agent list` 透过 symlink 正常列出全部 5 个 hyperion agent(含 backport)。
2. **改模板加临时 agent → `opencode agent list` 立刻看到 → 移除**(这就是 symlink 治漂移的核心证明,JSON 往返格式噪音用 `git checkout` 清掉)。

**跨机注意**:git 在 Linux/macOS 正常还原 symlink;**Windows 上 git 默认不解 symlink**(会写成含路径文本的普通文件)—— Hyperion 两台机都是 Linux/macOS,不受影响。若将来上 Windows,`git config core.symlinks true` + 开发者模式。

关联 [[opencode-mcp-wiring]] [[pitfall-log]](#10 opencode MCP 接线) [[backport-workflow-handoff]]。

关联 [[opencode-mcp-wiring]] [[pitfall-log]](#10 opencode MCP 接线) [[backport-workflow-handoff]]。
