---
name: workflow-show-code-in-window
description: 每个模块的关键 .py 代码在窗口展示由用户手敲,我不直接写源码文件
metadata:
  type: feedback
---

在 Hyperion 项目里,用户要求:**每个模块的关键代码不要直接写入文件,在本对话窗口展示,由用户自己敲入**。

**分工:**
- `.py` 源码(所有模块,含 config.py / cli.py / tests/)→ 我在窗口给完整代码块,用户手敲入文件。我**不**用 Write/Edit 写 `.py`。
- `config.yaml` / `pyproject.toml` 等配置文件、`uv sync` 等依赖操作 → 我直接改/执行。
- 验证命令(`uv sync` / `ruff` / `pytest` / `hyperion ...`)→ 我执行(只读检查),反馈结果。

**Why:** 这是学习型项目,用户要"真正吃透架构"(见 [[agent-project-overview]]、[[research-deerflow-first]]),亲手敲代码是内化的方式。

**How to apply:** 逐模块交付——展示一个完整 `.py`(给清文件路径)→ 用户敲入 → 我跑验证 → 下一个。配置类我直接动手。展示代码时给可直接粘贴的完整内容、注明文件路径,不要只给片段。

**代码呈现标准(用户明确要求):**
- 代码的 **docstring 与注释一律用中文**。
- 每个模块给出代码前,先讲清**它的作用**:在 agent 里负责什么、为什么需要、与 deer-flow 对应零件的关系。
- 对关键代码段附**逐段中文说明**,让用户"理解着敲"(学习型项目,要真正吃透)。
- 注释要"合适"——解释 why 和非显而易见处,不要无意义复述代码。
