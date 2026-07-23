# CLAUDE.md

> 本文件是 Claude Code(及其他 coding agent)在本项目工作的始终生效上下文。随 git 跨机同步。

## Hyperion 是什么

面向系统软件(后续 bluez / wpa_supplicant 等 Linux C 组件)的智能 agent,三大场景:

1. **Bug 根因定位与分析** — 结合源码 + 日志精准定位根因,生成分析报告。
2. **自主深度研究** — 给定代码库/问题,自主调研、实测验证、生成带引用的报告。
3. **开源仓库 PR 持续跟踪** — 定期分析上游 PR,给出"是否合入本地"的建议。

三大场景共享一个**平台 + 共享服务层**(代码理解、记忆与持续学习、沙箱、检索、可观测),并具备**持续学习**:每份报告经 Memorize 内化为可检索、带溯源、带置信度、带时序的记忆。Tagline:*Light on every root cause.*

完整架构设计见 [docs/architecture.md](docs/architecture.md)。

## ⭐ 工作准则(必读)

**设计任何模块前,先做这两件事,再动手写代码:**

1. **前沿调研** — 用 WebSearch 调研该方向的 2025-2026 最新进展,不要只看旧资料。
2. **参考 deer-flow** — `deer-flow/` 子目录是 ByteDance 的成熟实现(只读参考,`.gitignore` 掉,各自 clone)。精读对应模块,引用具体文件路径与关键代码片段。

综合两者再给方案、再写代码。不要凭空造轮子。

**实现时对齐 deer-flow,目标生产级(不是 demo):** 各功能优先照齐 deer-flow 对应代码的质量与边界处理;起步可做最小实现,但**必须排期迭代到生产级**。**本项目是生产级项目,不是 demo**——"最小实现"是阶段性手段,不是终点。每处简化都记入 `.claude/memory/backlog-production-grade.md`,后续补齐。

## 仓库地图

```
Hyperion/
├── src/hyperion/
│   ├── platform/     # Harness:模型工厂 / 配置 / 反射 / 网关 / 沙箱 / 可观测
│   ├── workflows/    # 三条工作流:bug_rca / deep_research / pr_tracker
│   ├── services/     # 共享服务:code_index / memory / log_symbolizer / static_analysis
│   ├── tools/        # agent 可调用工具(导航/检索/执行)+ 插件槽
│   └── cli.py        # 入口(uv run hyperion ...)
├── config/           # config.yaml(模型/工具/记忆 声明式)+ extensions_config.json(MCP/skills)
├── docs/architecture.md
├── scripts/          # setup.sh(系统工具) / setup_claude.sh(记忆软链)
├── .claude/memory/   # Claude Code 项目记忆(随 git 跨机)
└── deer-flow/        # 只读参考实现(.gitignore)
```

## 模型:多 provider 自适应

不硬编码任何厂家。在 `config/config.yaml` 的 `models:` 每项声明 `use: <module>:<ClassName>`,工厂 `hyperion.platform.models.create_chat_model` 用反射加载任意 LangChain chat model 类。**加新 provider 通常零代码,只改配置。** 详见 architecture.md §4.1。

## 命令

```bash
uv sync                  # 装/同步 Python 依赖(两台机一致,靠 uv.lock)
uv run hyperion models   # 列出配置的模型(验证 config + 工厂加载)
uv run pytest            # 测试
uv run ruff check .      # lint
bash scripts/setup.sh    # 装系统工具(Linux/macOS 自动适配)+ 记忆软链
```

## 两台机协作(Linux + macOS)

- **Python**:uv + `uv.lock` 保证两台一致;`.python-version` 锁定解释器版本。
- **系统工具**:`scripts/setup.sh` 按 OS 分发(macOS 用 brew / Linux 用 apt)。
- **记忆**:每次 fresh clone 后跑 `bash scripts/setup_claude.sh`,把 Claude Code 记忆软链到仓库内 `.claude/memory/`,随 git 同步。建议两台机仓库路径一致(如都放 `~/Desktop/Agent/Hyperion`),记忆 slug 自然对齐。
- **密钥**:`.env`(gitignore)按 `.env.example` 填,勿提交。

## 扩展性

工具是**声明式 + 反射 + 插件**:`config.yaml` 的 `tools:` 声明工具的 `use: <module>:func`,按需加载。domain 工具(bluez/wpa 解析等)放 `src/hyperion/tools/plugins/<name>/`,在配置里开关,不改核心。

## 路线

P0 地基 → P1 代码理解 → P2 Bug-RCA → P3 记忆闭环 → P4 PR-Tracker → P5 Deep-Research → P6 生产化(见 architecture.md §11)。
