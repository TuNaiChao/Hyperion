# 配置参考

> 主配置 [config/config.yaml](../config/config.yaml)。核心思想一句话:**一切可换的组件(模型 / 沙箱 / 记忆后端)都用 `use: <模块>:<类名>` 声明,工厂反射加载 —— 换 provider 通常零代码,只改配置**。
>
> 任何以 `$` 开头的值会解析为环境变量(从 `.env` 读,模板见 [.env.example](../.env.example))。配置加载在 [config.py](../src/rootrecall/platform/config.py)(`load_config` / `get_app_config` 单例)。

## 配置文件怎么被找到

按顺序找第一个存在的:程序包向上逐级找 `config/config.yaml`(从安装位置定位,保证 MCP 子进程在哪起都能找到)→ 当前目录 `config/config.yaml`。`.env` 在配置加载时读入(所以任何入口 —— CLI / MCP / 测试 —— 都有密钥)。

## models — 模型声明

每项声明一个模型,`use` 指向 LangChain 的 chat model 类:

```yaml
models:
  - name: deepseek-v4-pro            # 内部名(model_roles 引用它)
    display_name: DeepSeek Chat
    use: langchain_openai:ChatOpenAI # 反射加载(module:Class)
    model: deepseek-v4-pro           # 传给 provider 的模型名
    api_key: $DEEPSEEK_API_KEY       # $ 开头 = 环境变量
    base_url: https://api.deepseek.com
    max_tokens: 8192
    temperature: 0.2
    supports_thinking: true          # 可选:思考模式
    supports_vision: true            # 可选:视觉
```

**加新 provider = 加一项**:换 `use` 指向对应类即可(`langchain_anthropic:ChatAnthropic`、`langchain_ollama:ChatOllama`……),不用动代码。配置里留有 Anthropic / Ollama 的注释模板可直接打开。

## model_roles — 角色 → 模型路由

把「任务角色」映射到模型,改路由零代码:

```yaml
model_roles:
  default: deepseek-v4-pro
  planner: deepseek-v4-pro      # 规划
  locator: deepseek-v4-pro      # 定位(重活,用强模型)
  summarizer: deepseek-v4-pro   # 摘要(将来可换便宜模型,只改这里)
  verifier: deepseek-v4-pro
  memory_extractor: deepseek-v4-pro
  title: deepseek-v4-pro
```

## sandbox — 沙箱

```yaml
sandbox:
  use: rootrecall.platform.sandbox.provider:LocalSandboxProvider  # 本地;换 Docker 只改 use
  workspace: data/sandbox/workspace
  bash_command_timeout: 600        # 命令超时(秒)
  bash_output_max_chars: 20000     # 各类输出上限,防单次返回撑爆上下文
```

## code_index — 代码检索

`repo` 是默认查的索引名(= 建索引时的名字)。三个子块:

### embedding(向量从哪来)

```yaml
code_index:
  repo: rootrecall
  embedding:
    provider: openai_compatible   # openai_compatible(远端,默认)| sentence_transformers(本地)
    base_url: $DASHSCOPE_BASE_URL # 换 SiliconFlow / OpenAI / 自建 vLLM 只改这行
    api_key: $DASHSCOPE_API_KEY
    model: text-embedding-v4      # = Qwen3-Embedding
    dimensions: 1024              # 存取必须同维度
    batch_limit: 10               # 远端每请求条数上限
    normalize: true               # 客户端归一化,保 cosine 一致
    # 本地模式(provider 改 sentence_transformers 时生效):
    # local_model: Qwen/Qwen3-Embedding-0.6B
    # max_seq_length: 8192        # ⚠️ 本地必须显式设(默认 512 会静默截断长代码)
```

### retrieval(两阶段检索的旋钮)

```yaml
  retrieval:
    rrf_k: 60                    # RRF 融合常数(标准取值,几乎不用调)
    candidate_top_n: 50          # 候选池大小(喂重排器)
    final_top_k: 5               # 重排后返回
    fts_stem: false              # 代码场景必须关(否则 malloc 被 stem 乱变)
    fts_remove_stop_words: false # 代码场景必须关(否则 int/void 被当停用词删)
    query_boost: true            # 查询类型加成(PascalCase→类 / snake→函数)
```

### reranker(精排)

```yaml
  reranker:
    provider: dashscope          # dashscope(默认)| siliconflow(免费)| sentence_transformers(本地)| off
    base_url: $DASHSCOPE_RERANK_URL
    api_key: $DASHSCOPE_API_KEY  # 与 embedding 同一个 key
    model: qwen3-rerank
    rerank_top_n: 5
```

### lsp(L2 精确导航,可选)

clangd 经 multilspy 驱动(查引用 / 跳定义)。**硬前提**:目标仓库根有 `compile_commands.json`(autotools 用 `bear -- make V=1` 生成;cmake 用 `-DCMAKE_EXPORT_COMPILE_COMMANDS=ON`),没有它引用质量骤降。当前主线流程不依赖 LSP,属可选增强。

```yaml
  lsp:
    clangd_path: null       # null = 自动找;或写绝对路径
    extra_args: []          # 如交叉编译 --query-driver
    compile_commands_dir: null
```

## memory — 记忆后端

```yaml
memory:
  backend: native            # native(v1)| mem0 | cognee | 'pkg.mod:Cls'(可换,零锁死)
  store_path: data/memory    # native SQLite 落点
  native:
    structural: none         # none | crg(接结构图;需 uv sync --extra code-review-graph)
    embed: code_index        # 复用 code_index 的 embedder 给记忆算向量 | off
    rerank: code_index       # 复用 code_index 的 reranker 精排召回 | off
    recall_top_k: 5
    decay_halflife_days: 180.0  # 时间衰减半衰期(天)
    promote_access_count: 3    # 被召回 ≥3 次 → 升级 mental_model
    merge_step: 0.3            # 同条重提时置信度累加步长
```

切后端 = 放一个 `services/memory/backends/<名>/` 实现(暴露 `BACKEND_CLASS`)+ 改 `backend` 名;拒绝静默回退(配错会明报,不偷偷降级)。

## delegate — 委托 coding agent(降级参考线)

老 workflow 编排器(bug-rca / research CLI)委托 opencode 时的配置。**主线(skill + MCP 工具)不经过这层** —— opencode 直接被使用者驱动,不经 RootRecall 调度;此段仅老路径使用。

```yaml
delegate:
  backend: opencode            # opencode(有实现)| omp / claude(占位,未实现)
  opencode:
    bin: opencode
    model: uniontech-ai/glm-5.2
    auto_approve: true         # 无头模式必须
    format: json               # NDJSON 事件流(绕开思考模式不支持结构化产出的坑)
    timeout: 1200
    config: config/opencode_rootrecall.json
    retry_max: 2               # 瞬时网络错自动续 session 重试
    fallback_model: uniontech-ai/deepseek-v4-flash
```

## runtime — 长流程护栏(降级参考线)

RootRecall 自跑 workflow 时的护栏(断点续跑 + token 预算 + 工具输出外化)。主线同样不经过这层。

```yaml
runtime:
  enabled: true
  checkpoint_backend: sqlite   # memory(仅测)| sqlite(持久,断点续跑)
  token_budget:
    max_tokens: 1000000        # 每 run 总额;软警告 → 硬停(剥工具调用自然收尾)
  tool_output:
    externalize_min_chars: 30000  # 超长工具输出写盘 + 摘要代替
```

## mcp — 对外开门方式

```yaml
mcp:
  transport: stdio    # stdio(默认,推荐)| http(streamable-http)
  host: 127.0.0.1     # http 绑定(默认仅本机)
  port: 8765
```

CLI `rootrecall mcp serve --transport http` 覆盖这里的值。stdio 与 http 的取舍见 [MCP 工具参考](mcp-tools.md)。

## patch — 补丁 / PR 分析后勤

```yaml
patch:
  git:
    clone_dir: data/repos   # auto-clone 落点(幂等复用)
    shallow: true           # 浅克隆;要完整历史(when_introduced 挖老 commit)改 false
    remotes: {}             # {仓库名: git url} —— 内网镜像 / SSH 等自定义地址
```

## 常用密钥速查

| 环境变量 | 用途 | 哪些功能要 |
|---|---|---|
| `DEEPSEEK_API_KEY` | LLM(默认模型) | 全部(必填) |
| `DASHSCOPE_API_KEY` | embedding + reranker | 检索类工具(必填) |
| `GITHUB_TOKEN` | GitHub PR 抓取 | fetch_patch / patch-report(建议) |
| `GERRIT_USERNAME` / `GERRIT_HTTP_PASSWORD` | Gerrit 私仓 | fetch_patch 抓 Gerrit 时 |

一键配置脚本 `scripts/quickstart.sh` 会交互式引导前两个必填 key 的填写(输入不回显、不打印值)。

## 相关文档

- [MCP 工具参考](mcp-tools.md) — 这套配置撑起来的 17 个工具
- [CLI 参考](cli.md) — 验证配置(`rootrecall models`)与建索引
