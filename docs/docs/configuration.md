# 配置参考

> 主配置在 `config/config.yaml`。模型 / 工具用 `use: <module>:<ClassName>` 声明,工厂反射加载。
> 任何以 `$` 开头的值会被解析为环境变量(从 `.env` 读)。配置加载见 `platform/config.py`(`load_config` / `get_app_config` 单例)。

## 文件位置

`config.yaml` 的查找顺序:`$HYPERION_CONFIG` 环境变量 → `./config/config.yaml` → 包内默认。
`.env` 由 `cli.py` 启动时 `load_dotenv()` 读入(必须在任何 `$VAR` 解析之前)。

## models — 模型声明

每项是一个 LangChain chat model,`use` 指向其类:

```yaml
models:
  - name: deepseek-v4-pro              # 内部名(model_roles 引用它)
    display_name: DeepSeek Chat
    use: langchain_openai:ChatOpenAI   # 反射加载的类(module:Class)
    model: deepseek-v4-pro             # 传给 provider 的模型名
    api_key: $DEEPSEEK_API_KEY         # $ 开头 = 环境变量
    base_url: https://api.deepseek.com
    max_tokens: 8192
    temperature: 0.2
    supports_thinking: true            # 可选:思考模式(配 when_thinking_enabled/disabled)
    supports_vision: true              # 可选
```

加 provider 通常零代码:再写一项、`use` 指向对应 LangChain 类(`langchain_anthropic:ChatAnthropic` / `langchain_ollama:ChatOllama` …)。详见 [platform/models.md](platform/models.md)。

## model_roles — 角色 → 模型路由

把"任务角色"映射到具体模型,改路由零代码:

```yaml
model_roles:
  default: deepseek-v4-pro
  planner: deepseek-v4-pro
  locator: deepseek-v4-pro      # 定位用强模型
  summarizer: deepseek-v4-pro
  verifier: deepseek-v4-pro
  memory_extractor: deepseek-v4-pro
  title: deepseek-v4-pro
```

## sandbox — 沙箱 provider + 参数

```yaml
sandbox:
  use: hyperion.platform.sandbox.provider:LocalSandboxProvider   # P0 本地;换 Docker 只改 use
  workspace: data/sandbox/workspace
  bash_command_timeout: 600           # 命令超时(秒)
  bash_output_max_chars: 20000        # 命令输出上限
  read_file_output_max_chars: 50000
  ls_output_max_chars: 20000
```

详见 [platform/sandbox.md](platform/sandbox.md)。

## code_index — 代码理解(P1)

`repo` 是 `search_codebase` 查的表名,**必须**与 `hyperion index <path> <name>` 的 `name` 一致。

### embedding

```yaml
code_index:
  repo: wpa_supplicant
  embedding:
    provider: openai_compatible       # openai_compatible(远端,默认)| sentence_transformers(本地)
    base_url: $DASHSCOPE_BASE_URL     # 换 SiliconFlow/OpenAI/自建 vLLM 只改这里
    api_key: $DASHSCOPE_API_KEY
    model: text-embedding-v4          # = Qwen3-Embedding 全血版
    dimensions: 1024                  # 存取必须同 dim
    batch_limit: 10                   # 远端每请求条数上限
    normalize: true                   # 客户端 L2 归一化(保 cosine 一致)
    # —— sentence_transformers 模式(provider 改它时生效)——
    # local_model: Qwen/Qwen3-Embedding-0.6B
    # max_seq_length: 8192            # ⚠️ 本地必须显式设(ST 默认 512 会静默截断)
```

### retrieval(两阶段)

```yaml
  retrieval:
    rrf_k: 60                   # RRF 融合常数(Cormack 2009,几乎不用调)
    candidate_top_n: 50         # hybrid 取 top-50 喂 reranker
    final_top_k: 5              # reranker 后返回
    fts_stem: false             # 代码场景必须关(否则 malloc 等被 stem)
    fts_remove_stop_words: false# 代码场景必须关(否则 int/void 被当停用词删)
    query_boost: true           # PascalCase→Class / snake→Function 等 boosting
```

### reranker

```yaml
  reranker:
    provider: dashscope         # dashscope(默认)| siliconflow | sentence_transformers | off
    base_url: $DASHSCOPE_RERANK_URL
    api_key: $DASHSCOPE_API_KEY # 与 embedding 同一个 key
    model: qwen3-rerank
    rerank_top_n: 5
```

### lsp(L2 精确导航,clangd via multilspy)

```yaml
  lsp:
    clangd_path: null           # null = shutil.which("clangd") 自动找
    extra_args: []              # 如交叉编译 ["--query-driver=...arm-linux-*gcc*"]
    start_timeout: 30
    request_timeout: 15
    index_retry: 1              # 结果空/偏少时重试(防后台索引未就绪)
    compile_commands_dir: null  # null = clangd 自动向上找
```

> [!WARNING]
> LSP 的 references 质量强依赖仓库根的 `compile_commands.json`(autotools 用 `bear -- make V=1`;cmake 用 `cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON`)。没有它质量骤降。详见 [services/code-index.md](services/code-index.md)。

## memory — 记忆核心(P3)

```yaml
memory:
  backend: native               # native(v1)| mem0 | cognee | 'pkg.mod:Cls'
  store_path: data/memory       # native SQLite 落点
  native:
    structural: none            # none | crg(接 CRG;需 uv sync --extra code-review-graph)
    embed: code_index           # 复用 code_index 的 embedder 给 KI summary 算向量 | off
    rerank: code_index          # 复用 code_index 的 reranker 精排 recall | off
    recall_top_k: 5
    decay_halflife_days: 180.0  # 召回衰减半衰期(天)
    promote_access_count: 3     # 被召回≥N 次 → 升级 mental_model
    merge_step: 0.3             # 重提时 Bayes 置信度累加步长
```

切后端 = 丢一个 `services/memory/backends/<name>/`(暴露 `BACKEND_CLASS`)+ 改 `backend` 名;拒绝静默回退。详见 [services/memory.md](services/memory.md)。

## delegate — 委托 coding agent(P2)

```yaml
delegate:
  backend: opencode             # opencode(v1 默认)| omp | claude
  max_localize_loops: 2         # 迭代 verify-refine:localize 自审重定位轮数
  max_repair_loops: 2           # repair 自审重修轮数
  opencode:
    bin: opencode
    model: uniontech-ai/glm-5.2 # "provider/model"
    agent: null                 # null = opencode 默认 agent;或指定子 agent
    variant: null               # null | "high" | "max" | "minimal"(推理档)
    auto_approve: true          # 无头必须 --auto
    format: json                # json(NDJSON 事件流)| default
    timeout: 1200               # 委托总超时(秒)
    config: config/opencode_hyperion.json
    retry_max: 2                # 瞬时网络错重试次数
    fallback_model: uniontech-ai/deepseek-v4-flash  # 主模型重试仍错 → 换它
```

> [!NOTE]
> `backend: omp/claude` 在 `from_config` 的短名映射里有占位,但**当前只有 `OpencodeDelegate` 有真实现**;配 omp/claude 会报 `AttributeError`。详见 [workflows/bug-rca.md](workflows/bug-rca.md)。

## runtime — agent 运行时(R3)

```yaml
runtime:
  enabled: true
  checkpoint_backend: sqlite    # memory(仅测)| sqlite(持久,默认)| postgres
  checkpoint_path: null         # null → data/runtime/checkpoint.sqlite
  token_budget:
    max_tokens: 1000000         # 每 run 总 token 上限
    warn_threshold: 0.7         # 软警告
    hard_stop_threshold: 1.0    # 硬停 → 剥 tool_calls 自然停
  tool_output:
    externalize_min_chars: 30000  # 超长工具输出外化到磁盘 + synopsis
    outputs_dir: data/runtime/tool-outputs
```

4 个中间件(ToolOutputBudget / TokenBudget / LoopDetection / TurnBudget)详见 [platform/runtime.md](platform/runtime.md)。

## mcp — 对外暴露(D0)

```yaml
mcp:
  transport: stdio            # stdio(默认)| http(streamable-http)
  host: 127.0.0.1             # http 绑定(对外暴露改 0.0.0.0 + 鉴权)
  port: 8765
```

CLI `hyperion mcp serve --transport http --host --port` 覆盖默认值。

## patch — 补丁 / PR 分析(P-A)

```yaml
patch:
  git:
    clone_dir: data/repos     # auto-clone 落点(幂等)
    shallow: true             # --depth 1 浅克隆
    remotes: {}               # {仓库名: git url} 自定义镜像 / SSH
```

> [!NOTE]
> `build_check` 试编译门已于 2026-08-10 撤销(构建信号歧义 + opencode 自己能 make)。`validate_patch` 只验 apply(Tier 0);编译 / 测试 / 复现永不做,用户真机自验。

## See Also

- [platform/models.md](platform/models.md) — 模型工厂细节
- [services/code-index.md](services/code-index.md) — 检索栈
- [services/memory.md](services/memory.md) — 记忆后端
- [.env.example](../../.env.example) — 环境变量模板
