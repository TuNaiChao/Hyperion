# Hyperion

> *Light on every root cause.*

面向系统软件(Linux C 组件,后续 bluez / wpa_supplicant)的智能 agent:Bug 根因定位与分析、自主深度研究(含实测验证)、开源仓库 PR 持续跟踪。三大场景共享平台与共享服务层,并具备持续学习(报告内化为记忆)。

**当前阶段:P0 地基**(平台骨架 + 多 provider 模型工厂 + 配置 + 沙箱占位)。

## 快速开始

```bash
# 1. 装系统工具(Linux/macOS 自动适配)+ Python 依赖 + Claude 记忆软链
bash scripts/setup.sh

# 2. 填密钥
cp .env.example .env   # 然后编辑填 API key

# 3. (可选)拉取参考实现
git clone https://github.com/bytedance/deer-flow

# 4. 验证
uv run hyperion models
```

## 文档

- 架构设计:[docs/architecture.md](docs/architecture.md)
- 工作约定:见仓库根 [CLAUDE.md](CLAUDE.md)

## 技术栈

Python 3.12 · LangGraph + LangChain · uv 管理依赖 · tree-sitter / ctags / LanceDB(代码理解)· mem0 / Graphiti(记忆,后续)。
