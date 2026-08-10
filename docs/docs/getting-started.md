# 快速开始

> 从零把 Hyperion 跑起来:装依赖 → 配密钥 → 验证模型 → 建索引 → 跑一次调研。
> 假设工作目录是仓库根 `Hyperion/`。两台机(Linux + macOS)协作见 [../../CLAUDE.md](../../CLAUDE.md)。

## 前置

- Python(版本见 `.python-version`,由 `uv` 自动管理)
- `uv`(Python 包管理器)
- 一个能用的 LLM API key(默认走 DeepSeek;embedding/rerank 走 DashScope)

## 1. 装依赖

```bash
uv sync                 # 按 uv.lock 装 Python 依赖(两台机一致)
```

可选 extra(按需):

```bash
uv sync --extra mcp                    # MCP server(给 coding agent 调)
uv sync --extra embedding-local        # 本地 embedding / rerank(离线 / 数据不出本地)
uv sync --extra code-review-graph      # 结构图 CRG(blast_radius / 调研模块切分)
uv sync --extra providers              # Anthropic 等 provider
```

系统工具(clangd / tree-sitter 等)可跑 `bash scripts/setup.sh`(按 OS 自动适配 apt / brew)。

## 2. 配密钥

复制模板,填上你的 key:

```bash
cp .env.example .env
# 编辑 .env,至少填:
#   DEEPSEEK_API_KEY=...        (chat 模型,默认 deepseek-v4-pro)
#   DASHSCOPE_API_KEY=...       (embedding text-embedding-v4 + rerank qwen3-rerank)
#   DASHSCOPE_BASE_URL=...      (可选,专属 MaaS 端点;不设走 serverless 默认)
#   DASHSCOPE_RERANK_URL=...    (可选,rerank 原生端点)
```

> [!WARNING]
> `.env` 在 `.gitignore` 里,**勿提交**。永远不要把 key 的值贴进代码或文档。

`config.yaml` 里任何以 `$` 开头的值都会被解析成环境变量(见 [configuration.md](configuration.md))。

## 3. 验证模型与配置

```bash
uv run hyperion models
```

应列出 `config.yaml` 中配置的模型 + role 路由(如 `default -> deepseek-v4-pro`)。这一步同时验证了 config 加载 + 模型工厂反射没问题。

## 4. 建代码索引(P1 前提)

给一个代码仓建向量索引(`search_codebase` / 深度调研都要先建):

```bash
uv run hyperion index ~/src/wpa_supplicant wpa_supplicant
# 索引完成 [incremental]:wpa_supplicant  1234 chunk  commit=abc123
```

> [!NOTE]
> 第二个参数 `wpa_supplicant` 是索引名,**必须**与 `config.yaml` 的 `code_index.repo` 一致,否则 `search_codebase` 会查空表。

加 `--force` 强制全量重建。

## 5. 跑一次深度调研(P1)

```bash
uv run hyperion research --repo ~/src/wpa_supplicant --codebase wpa_supplicant
# 报告:data/research/<repo>-research.md
# CodebaseFact 入记忆:N 条
```

六节点流水线(建图 → 模块切分 → 每模块 ReAct 子 agent → cited 报告 → 入记忆),真调模型建子 agent,较慢(数分钟)。详见 [workflows/deep-research.md](workflows/deep-research.md)。

## 6.(可选)起 MCP server 给 coding agent 调

bug-RCA 的主路径是 opencode + skill + Hyperion MCP 工具。把 Hyperion 能力开给 opencode:

```bash
# stdio(默认,opencode 拉起子进程 1:1)
uv run hyperion mcp serve --codebase wpa_supplicant

# 或 http(warm 长进程,多 agent 共用,省冷启动)
uv run hyperion mcp serve --codebase wpa_supplicant --transport http
# → http://127.0.0.1:8765/mcp,把 opencode/codex 指过来
```

opencode 侧的接线(配置文件、agent、permission)见 [guides/bug-rca-opencode.md](guides/bug-rca-opencode.md) 与 `config/opencode_hyperion.json`。

## 排查

| 现象 | 排查 |
|---|---|
| `models` 列不出 / 报反射错 | `.env` 的 key 没填 / `use:` 写错;看报错给的 `uv add` 提示 |
| `search_codebase` 返回空 | 没建索引,或索引名 ≠ `code_index.repo`;跑 `hyperion index` |
| `lsp health` 红 | clangd 没装(`bash scripts/setup.sh`),或仓库根缺 `compile_commands.json` |
| MCP 工具在 opencode 里不出现 | 用 `local` stdio 接(详见 [guides/bug-rca-opencode.md](guides/bug-rca-opencode.md));timeout 调到 ≥ 120000ms 防首次冷启 |

## See Also

- [configuration.md](configuration.md) — 配置全段
- [cli-reference.md](cli-reference.md) — 全部命令
- [guides/bug-rca-opencode.md](guides/bug-rca-opencode.md) — bug-RCA 主路径
- [../../CLAUDE.md](../../CLAUDE.md) — 两台机协作、命令速查
