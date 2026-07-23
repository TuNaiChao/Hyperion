---
name: agent-project-overview
description: Hyperion 项目的目标、架构决策与设计文档位置
metadata:
  type: project
---

**项目名:Hyperion**(GitHub `TuNaiChao/Hyperion`,本地目录 `/home/tnc/Desktop/Agent/Hyperion`,Python 包名 `hyperion`;如日后发 PyPI 用 `hyperion-agent`)。tagline:*Light on every root cause.* 两台机开发:一台 Linux、一台 macOS,用 uv 管 Python、`scripts/setup.sh` 装系统工具、`.claude/memory/` 随 git 同步记忆。

正在构建一个智能 agent(长期项目),面向 bluez / wpa_supplicant 等 Linux C 系统组件,三大场景:① bug 根因定位与报告;② 自主深度研究(含实测验证);③ 开源仓库 PR 持续跟踪与合入建议。要求"持续学习"(报告内化为记忆)。技术栈 Python 3.12 + LangGraph + LangChain,uv 管理依赖。

完整设计文档:**[docs/architecture.md](../../docs/architecture.md)**。

核心约束(易忘、非代码可见):
- `deer-flow/` 子目录是 ByteDance 的**参考实现,只读,当零件目录**(`.gitignore` 掉,各自 clone),不整体 fork。可移植:模型工厂、deep-research 方法论、记忆中间件、沙箱、社区检索工具。
- 模型工厂用**反射 + 配置声明**(`use: module:ClassName`)实现多 provider 自适应,加新厂家通常零代码只改 config(`src/hyperion/platform/models.py`)——用户明确强调过这点。
- 当前范围:MVP 先做 **coding-agent 式代码+日志分析工具**(领域无关),bluez/wpa 等做成 `tools/plugins/` 后挂。工具是声明式+反射+插件,可扩展。
- 分阶段路线 P0–P6(见文档 §11):P0 地基(含模型工厂,已完成脚手架)→ P1 代码理解(tree-sitter/ctags/LanceDB)→ P2 Bug-RCA → P3 记忆闭环 → P4 PR-Tracker → P5 Deep-Research → P6 生产化。

本机环境:Python 3.12;依赖将由 `uv sync` 安装;ctags/clangd 由 `scripts/setup.sh` 装。

下一步未定,用户可能在后续会话要求起 P1(代码理解服务)或先 `uv sync` 验证 P0。
