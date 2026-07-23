# 软件 Bug 定位 / 深度研究 / PR 跟踪 Agent — 架构设计

> 状态:设计稿(v0.1) · 目标代码库:bluez / wpa_supplicant 等 Linux C 系统组件
> 语言:Python 3.12 · 框架:LangGraph + LangChain
> 参考实现:[deer-flow/](deer-flow/)(ByteDance,作为"零件目录"按需移植,不整体 fork)

---

## 0. 一句话定位

构建一个 **平台 + 三条工作流 + 共享服务层** 的智能 agent:

- **三条工作流**:① Bug 根因定位与分析;② 自主深度研究(含实测验证);③ 开源仓库 PR 持续跟踪与合入建议。
- **共享服务层**:代码理解、记忆与持续学习、日志符号化、沙箱执行、检索、可观测——三条工作流公用。
- **持续学习**:每条工作流末尾有一个一等公民的 **Memorize 内化节点**,把报告变成可检索、带溯源、带置信度、带时序的记忆;入口有 **Recall 注入**,实现经验复用。

**核心取舍**:不整体 fork deer-flow(它是 30+ 中间件的 ReAct 超级 harness,自主性强但控制流不透明);自建精简平台,移植 deer-flow 的优质零件(模型工厂、deep-research 方法论、记忆中间件、沙箱工具、Tavily/DDG 检索)。Bug 定位需要严格的"假设→定位→验证"闭环,用显式 StateGraph 比自主 ReAct 更可调试。

---

## 1. 核心架构决策

| # | 决策 | 理由 |
|---|---|---|
| **D1** | 自建精简平台 + 移植 deer-flow 零件 | 控制流透明、可调试、能真正吃透架构;deep-research 用 supervisor+子代理 |
| **D2** | 平台 + 三工作流 + 共享服务 三层分离 | 代码索引/记忆/沙箱/检索三流共用,放共享层避免三套实现 |
| **D3** | bug 工作流用显式 StateGraph;研究/PR 用 supervisor + Send map-reduce | 不同场景控制需求不同,不强行统一成一个大 ReAct |
| **D4** | 记忆分层:LangGraph Store(基座)→ mem0(抽取/合并)→ Graphiti(时序领域 KG) | Store 零额外服务但无合并;mem0 补"内化";Graphiti 用 `valid_at/expired_at` 解决"同一 bug 认知随版本演变" |
| **D5** | 代码理解 = tree-sitter repo map + ctags 符号 + LanceDB 混合检索 + 静态分析器 | 纯向量对 C 的函数名/宏/错误码召回弱,必须 BM25+向量+RRF+rerank |
| **D6** | "实测验证"走仿真:wifi 用 `mac80211_hwsim`+`hostapd`;蓝牙用 `hci_vhci`/QEMU/btproxy | 把"会写报告"升级为"会做实验"的关键可行性杠杆,无需裸机即可无人值守回归 |
| **D7** | LLM provider 用**反射 + 配置声明**,不硬编码任何厂家 | 直接移植 deer-flow 的 `use: module:ClassName` 机制;加厂家只改配置、零代码(见 §4.1) |

---

## 2. 总体架构

```
                 ┌──────────────────────────────────────────────────┐
  用户 / IM / cron ─▶│              Harness / 平台层                     │
                 │  Gateway(FastAPI)·路由·权限·流式·HITL·checkpoint·cron│
                 └────────────────────┬─────────────────────────────┘
                                      │
            ┌─────────────────────────┼───────────────────────────┐
            ▼                         ▼                           ▼
   ┌─────────────────┐      ┌────────────────────┐      ┌──────────────────┐
   │ ① Bug-RCA 工作流 │      │ ② Deep-Research    │      │ ③ PR-Tracker     │
   │  显式 StateGraph │      │  supervisor+子代理 │      │  cron+map-reduce │
   │ triage→locate→  │      │  plan→fan-out→     │      │  pull→Send并行→  │
   │ verify→report→  │      │  verify→test→      │      │  review→scorecard│
   │ memorize        │      │  report→memorize   │      │  →summarize→记忆 │
   └────────┬────────┘      └─────────┬──────────┘      └────────┬─────────┘
            └────────────────────────┼┴──────────────────────────┘
                                     ▼
        ┌──────────────────── 共享服务层 ────────────────────────┐
        │ 代码理解: tree-sitter·universal-ctags·LanceDB(混合检索) │
        │           sparse/smatch/coccinelle·addr2line·btmon解析  │
        │ 记忆:     LangGraph Store·mem0·Graphiti(FalkorDB)       │
        │           BM25+向量+RRF+bge-reranker·LightRAG(领域KG)   │
        │ 沙箱:     Docker/OpenHands runtime(跑测试/复现/编译)     │
        │ 检索:     Tavily/DDG·web_fetch                           │
        │ 可观测:   Langfuse·PostgresSaver(checkpointer)           │
        └────────────────────────────────────────────────────────┘
```

---

## 3. 工作流概览(详细设计见 §6)

| 工作流 | 触发 | 编排范式 | 核心差异化能力 |
|---|---|---|---|
| ① Bug-RCA | 用户给日志/症状 | 显式 StateGraph(triage→locate→verify→refine 循环) | 三路径日志符号化(内核 oops / btmon / wpa)对齐源码行 |
| ② Deep-Research | 用户给问题/代码库 | supervisor + 子代理(Send 并行)+ 沙箱 | 对抗式验证(红队找反例)+ 实测验证(hwsim/hci_vhci) |
| ③ PR-Tracker | LangGraph cron | map-reduce(Send 并行 review) | GraphQL 增量拉取 + 多维合入决策卡 + 本地冲突评估 |

---

## 4. Harness / 平台层

### 4.1 模型工厂:多 provider 自动适配 ⭐

> 直接移植 deer-flow 的 [factory.py](deer-flow/backend/packages/harness/deerflow/models/factory.py) 设计。

**核心思想**:不硬编码任何 provider。每个模型在 `config.yaml` 里声明一个 `use: <module>:<ClassName>` 字段,工厂用**反射**动态加载该 LangChain chat model 类,把它声明的其余字段作为 kwargs 传入。**加一家新 provider,通常零代码——只改配置。**

#### 4.1.1 配置声明(多 provider 示例)

```yaml
# config/config.yaml
models:
  # —— OpenAI 官方 ——
  - name: gpt-4.1
    display_name: GPT-4.1
    use: langchain_openai:ChatOpenAI          # 反射目标:import langchain_openai; ChatOpenAI
    model: gpt-4.1
    api_key: $OPENAI_API_KEY                  # $ 前缀 → 环境变量解析
    base_url: https://api.openai.com/v1
    request_timeout: 600.0
    max_retries: 2
    max_tokens: 8192
    temperature: 0.2
    supports_vision: true
    pricing: { currency: usd, input_per_million: 2.0, output_per_million: 8.0 }

  # —— Anthropic(原生 thinking 参数)——
  - name: claude-sonnet
    display_name: Claude Sonnet
    use: langchain_anthropic:ChatAnthropic
    model: claude-sonnet-4-5
    api_key: $ANTHROPIC_API_KEY
    max_tokens: 8192
    supports_thinking: true
    supports_vision: true
    when_thinking_enabled: { thinking: { type: enabled, budget_tokens: 8000 } }
    when_thinking_disabled: { thinking: { type: disabled } }

  # —— DeepSeek(OpenAI 兼容;用 patched 子类保留 reasoning_content)——
  - name: deepseek-reasoner
    display_name: DeepSeek Reasoner
    use: my_agent.models.patched_deepseek:PatchedChatDeepSeek
    model: deepseek-reasoner
    api_key: $DEEPSEEK_API_KEY
    base_url: https://api.deepseek.com/v1
    supports_thinking: true
    when_thinking_enabled: { extra_body: { thinking: { type: enabled } } }
    when_thinking_disabled: { extra_body: { thinking: { type: disabled } } }

  # —— 本地 Ollama(用原生 provider,保留 think 内容)——
  - name: qwen3-local
    display_name: Qwen3 32B (Ollama)
    use: langchain_ollama:ChatOllama           # 注意:别走 OpenAI 兼容端点,会丢 reasoning
    model: qwen3:32b
    base_url: http://localhost:11434
    num_predict: 8192
    supports_thinking: true

  # —— 自托管 vLLM ——
  - name: vllm-qwen
    use: my_agent.models.vllm_provider:VllmChatModel
    model: Qwen/Qwen3-32B
    base_url: http://localhost:8000/v1
    api_key: EMPTY
    supports_thinking: true
    when_thinking_enabled:
      extra_body: { chat_template_kwargs: { enable_thinking: true } }

  # —— 任意 OpenAI 兼容网关(火山方舟/Kimi/GLM 等,同一切换)——
  - name: glm-coding
    use: my_agent.models.patched_deepseek:PatchedChatDeepSeek
    model: glm-4.6
    api_key: $ZHIPU_API_KEY
    base_url: https://open.bigmodel.cn/api/paas/v4

# 角色 → 模型名 的路由(分层:便宜模型做摘要,强模型做定位/写作)
model_roles:
  default: gpt-4.1
  planner: claude-sonnet          # 规划用强模型
  locator: claude-sonnet          # 代码定位用强模型
  summarizer: gpt-4.1-mini        # 摘要/压缩用便宜模型
  verifier: gpt-4.1               # 验证用可靠模型
  memory_extractor: gpt-4.1-mini  # 记忆抽取用便宜模型
  title: qwen3-local               # 标题生成用本地免费模型
```

#### 4.1.2 反射加载器

```python
# src/my_agent/platform/reflection.py
import importlib

def resolve_class(dotted_path: str, base_class: type):
    """'langchain_openai:ChatOpenAI' → 真实的类;校验它是 base_class 的子类。

    缺包时给出可执行的安装提示(deer-flow 同款做法),而不是 ImportError 栈。
    """
    module_path, _, attr = dotted_path.partition(":")
    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        # 把常见 provider 映射到安装命令
        hints = {
            "langchain_openai": "uv add langchain-openai",
            "langchain_anthropic": "uv add langchain-anthropic",
            "langchain_ollama": "uv add langchain-ollama",
        }
        hint = hints.get(module_path.split(".")[0], f"uv add {module_path}")
        raise ImportError(f"无法加载 {module_path}({e});请先安装:`{hint}`") from None
    cls = getattr(module, attr, None)
    if cls is None:
        raise AttributeError(f"{module_path} 没有 {attr}")
    if not (isinstance(cls, type) and issubclass(cls, base_class)):
        raise TypeError(f"{dotted_path} 不是 {base_class.__name__} 的子类")
    return cls
```

#### 4.1.3 工厂函数(简化版,对应 deer-flow `create_chat_model`)

```python
# src/my_agent/platform/models.py
from langchain.chat_models import BaseChatModel
from langchain_openai.chat_models.base import BaseChatOpenAI
from my_agent.platform.reflection import resolve_class
from my_agent.platform.config import get_app_config

# 排除这些"元字段"——它们是给我们自己用的,绝不能透传给 provider 客户端
_META_FIELDS = {
    "use", "name", "display_name", "description",
    "supports_thinking", "supports_reasoning_effort",
    "when_thinking_enabled", "when_thinking_disabled", "thinking",
    "supports_vision", "pricing",
}

def create_chat_model(name: str | None = None, *, thinking_enabled: bool = False,
                      role: str | None = None, **overrides) -> BaseChatModel:
    """根据 config.yaml 声明构造一个 chat model。

    - name: 模型逻辑名;None 时按 role 路由,再退回 models[0]。
    - thinking_enabled: 若该模型声明 supports_thinking,注入 when_thinking_enabled 的 kwargs。
    - role: 'locator' / 'summarizer' / ... 走 model_roles 路由(分层控成本)。
    - overrides: 调用方临时覆盖 temperature/max_tokens 等。
    """
    config = get_app_config()
    if name is None:
        name = config.model_roles.get(role) if role else None
        name = name or config.model_roles["default"]

    mc = config.get_model(name)                      # ModelConfig(pydc, extra="allow")
    model_class = resolve_class(mc.use, BaseChatModel)

    # 1) 其余字段 → kwargs;剥离元字段
    kw = mc.model_dump(exclude_none=True, exclude=_META_FIELDS)
    kw.update({k: v for k, v in overrides.items() if v is not None})

    # 2) thinking 开关:不同 provider 的归一化(OpenAI-compat 走 extra_body,Anthropic 走 thinking,vLLM 走 chat_template_kwargs)
    wte = _merge_thinking(mc.when_thinking_enabled, mc.thinking)
    if thinking_enabled and mc.supports_thinking and wte:
        kw.update(wte)
    elif not thinking_enabled:
        if mc.when_thinking_disabled:
            kw.update(mc.when_thinking_disabled)
        elif wte:  # 默认显式关闭,避免 provider 意外开启
            kw.setdefault("extra_body", {})["thinking"] = {"type": "disabled"}

    # 3) OpenAI 兼容客户端的 base_url 归一化(用户常误写 api_base)
    _normalize_base_url(model_class, kw)

    # 4) 流式 usage 默认开(否则第三方端点丢 token 统计)
    if issubclass(model_class, BaseChatOpenAI):
        kw.setdefault("stream_usage", True)
        kw.setdefault("stream_chunk_timeout", 240.0)  # 推理模型首 chunk 可达 90~150s

    # 5) 未知字段告警(把 typo 从"请求时崩溃"提前到"构建时告警")
    _warn_unknown(model_class, name, kw)

    instance = model_class(**kw)
    _attach_tracing(instance)                         # Langfuse callback
    return instance
```

> 完整版还应包含 deer-flow 的 `supports_reasoning_effort` 处理、Codex Responses API 的 `max_tokens` 剥离、MindIE 的 retry 约束等——按需逐步补齐。见 [factory.py](deer-flow/backend/packages/harness/deerflow/models/factory.py) 第 174–319 行。

#### 4.1.4 加一家新 provider 的两种路径

| 场景 | 做法 | 工作量 |
|---|---|---|
| **标准 provider**(OpenAI/Anthropic/Ollama/Gemini/任意 OpenAI 兼容网关) | 只在 `config.yaml` 加一条 `models:` 项,填对 `use:` 和 `base_url` | **零代码** |
| **有非标准行为的 provider**(如 DeepSeek 的 `reasoning_content` 字段、vLLM 的 `reasoning` 字段需要保留) | 写一个 `PatchedXxx(BaseChatOpenAI)` 子类,override `_convert_*` 把非标字段保存在 `additional_kwargs` | 一个小文件 |

patched provider 模板(保留 reasoning_content,对应 deer-flow [patched_deepseek.py](deer-flow/backend/packages/harness/deerflow/models/patched_deepseek.py)):

```python
# src/my_agent/models/patched_deepseek.py
from langchain_openai import ChatOpenAI

class PatchedChatDeepSeek(ChatOpenAI):
    """DeepSeek / GLM / Kimi / 火山方舟等 OpenAI 兼容但额外吐 reasoning_content 的网关。

    保留 reasoning_content 到 additional_kwargs,避免被 BaseChatOpenAI 丢弃。
    """
    # override 相关的 _convert_dict_to_message / _convert_chunk_to_message 即可
```

#### 4.1.5 角色 → 模型 路由(成本控制)

深度研究/批量 PR 评审会爆 token,必须分层:`summarizer` / `memory_extractor` / `title` 用便宜模型(gpt-4.1-mini / 本地 Ollama),`planner` / `locator` / `final_report` 用强模型。工厂函数接受 `role=` 参数,从 `config.yaml` 的 `model_roles` 查表。open_deep_research 的四角色范式是参考基线。

### 4.2 配置系统

移植 deer-flow 的声明式风格([config.example.yaml](deer-flow/config.example.yaml)):

- **主配置** `config/config.yaml`:models / model_roles / tools / sandbox / memory / retrieval / workflows / observability。
- **扩展配置** `extensions_config.json`:MCP servers + skills 启用状态。
- **`$ENV` 解析**:任何值以 `$` 开头 → 解析为环境变量(放 API key)。
- **热重载边界**:per-run 字段(`models[*].max_tokens`、`memory.*` 等)改完下次请求即生效;基础设施字段(`database` / `sandbox` / `scheduler`)需重启。deer-flow 用 `STARTUP_ONLY_FIELDS` 注册表标记,值得照搬。
- **配置缓存 + 签名校验**:`get_app_config()` 缓存解析结果,但当文件内容签名(含 sha256)变化时自动重载——避免 mtime 在网络挂载上失效。

### 4.3 工具注册与 MCP

- **声明式 + 反射**:`config.yaml` 的 `tools:` 列表每项有 `use: <module>:<func>`,`get_available_tools()` 用 `resolve_variable()` 动态加载(同 deer-flow [tools.py](deer-flow/backend/packages/harness/deerflow/tools/tools.py))。
- **工具组**:`tool_groups` 把工具按 `web` / `code` / `memory` / `sandbox` 分组,工作流按需挂载。
- **MCP**:用 `langchain-mcp-adapters` 的 `MultiServerMCPClient`,支持 stdio/SSE/HTTP + OAuth;延迟发现(`tool_search`)避免 schema 撑爆上下文。

### 4.4 沙箱执行

移植 deer-flow 的 `SandboxProvider` 抽象([sandbox/](deer-flow/backend/packages/harness/deerflow/sandbox/)):

| 实现 | 用途 |
|---|---|
| `LocalSandboxProvider` | 开发期,宿主文件系统;虚拟路径 `/mnt/user-data/{workspace,outputs}` 映射到每线程目录 |
| `DockerSandboxProvider` | 生产期,容器隔离(对应 deer-flow `AioSandbox`);host bash 默认关 |
| (可选)`E2B / Boxlite` | 远程沙箱 / 微 VM |

工具:`bash`(带命令超时 + 后台进程处理)、`read_file`、`write_file`、`str_replace`、`ls`。
**安全**:`env_policy` 刮掉 `*KEY*/*SECRET*/*TOKEN*` 类环境变量,平台凭据不泄进 skill 子进程;`read_before_write` 哈希门防盲写。

### 4.5 可观测性

- **Langfuse**(自托管友好):trace / session / user / token / cost 全链路。callback 挂在图调用根(单 run 单 trace,所有 node/LLM/tool 是子 span)。
- 可同时开 LangSmith / Monocle(OTel)。
- 关键约定:`langfuse_session_id = thread_id`,`langfuse_user_id = user_id`,`langfuse_tags = [env, model]`。

### 4.6 持久化与调度

- **Checkpointer**:`PostgresSaver`(生产)/ `SqliteSaver`(单机)/ `MemorySaver`(开发)。跨对话状态恢复、HITL rewind、断点续跑的基础。
- **Store**:跨线程 KV + 向量索引(记忆基座,见 §5.2)。
- **Cron**:`LangGraph Platform` 原生 `CronClient` + cron 表达式(UTC)。上游仓无权挂 webhook,**cron 轮询 GraphQL 是 PR 跟踪主路径**。
- **Background run**:长任务(深度研究、批量 PR 评审)从请求线程解耦。

---

## 5. 共享服务层

### 5.1 代码理解服务 `services/code_index/`

**目标**:让 agent 能像 IDE 一样在大型 C 代码库里"导航"——这是 bug 定位和 PR 影响面分析的共同地基。

**三层索引**:
1. **tree-sitter**(主力):`tree-sitter-c` 容错解析,提取函数/struct/宏的定义与调用,构建 repo map(Aider 的 PageRank 式排名 + token 预算裁剪,见 [aider repo map](https://aider.chat/docs/repomap.html))。
2. **universal-ctags**(补充):`ctags -R --output-format=json` 产出符号表(函数/宏/typedef/struct + 位置),补 tree-sitter 不擅长的宏。
3. **clangd / LSP**(按需精确):需 `compile_commands.json`(Make 项目用 `bear -- make` 生成);万文件级索引慢,只在需要精确 caller/callee 时查。

**切块 + 向量 + 混合检索**:
- 按符号边界切(每个 `function_definition` / `struct_specifier` / `#define` 一个 chunk),不按固定行数切。
- 向量库 LanceDB(嵌入式,本地友好)/ Qdrant(生产,原生 dense-sparse)。
- embedding 模型:`voyage-code-3` 或 `bge-large-en-v1.5`(选定后不能换,换需全量重嵌)。
- **混合检索**:BM25(符号/函数名/错误码精确) + 向量(语义) + **RRF 融合**(`score=Σ 1/(60+rank)`) + **`bge-reranker-v2-m3` 重排**(只对 top-50,取 top-5)。对 C 代码尤其重要——函数名/宏名是强信号。

**导航工具集**(给定位 agent 的 ACI,借鉴 SWE-agent/OrcaLoca):
`grep_symbol`、`read_function(sym)`、`get_callers(sym)`、`get_callees(sym)`、`search_code(query)`(混合检索)。

### 5.2 记忆与持续学习服务 `services/memory/` ⭐

**分层栈**(分阶段叠加,不是一开始就上全套):

| 层 | 选型 | 职责 | 引入时机 |
|---|---|---|---|
| 基座 | LangGraph Store + pgvector | 跨线程 KV + 向量检索;所有节点原生可访问 | Phase 1 |
| 抽取/合并 | mem0 OSS(Apache-2.0) | 报告→原子事实;ADD/UPDATE/DELETE 判定 | Phase 3 |
| 时序领域 KG | Graphiti + FalkorDB(Apache-2.0) | `valid_at/expired_at/invalid_at`;同一 bug 认知随版本演变 | Phase 3 进阶 |
| 静态领域 KG | LightRAG | bluez/wpa 官方文档/源码注释建成静态知识库 | Phase 5 |

**两个横切组件**(挂在每条工作流上,详见 §7):

- **Recall**(读,入口):症状/问题 → 多路召回(Store 语义 + Graphiti 图遍历 + BM25 精确)→ RRF 融合 → reranker 重排 → 只取 top-3~5 → 注入 context(每条带 溯源+置信度+时间戳)。
- **Memorize**(写,出口):报告 → 抽取原子事实 → 实体消歧 → 冲突合并(recency-wins + 显式失效)→ 设置信度 → 存 Store/mem0/Graphiti + provenance。

**记忆分类学 × bug 场景**:

| 类型 | 存什么 | 存哪 |
|---|---|---|
| 工作记忆 | 当前 bug 报告、已排除的假设 | LangGraph State(Checkpointer) |
| 情景记忆 | "2026-07 分析了 A2DP 断连案例 X,根因是 avdtp_start 滞后" | Store namespace `(component,"episodes")` |
| 语义记忆 | "BlueZ transport_state 在 SUSPENDING 态下不接受 new start" | mem0 事实 / Graphiti 边 / LightRAG KG |
| 程序性记忆 | "排查连接问题标准流程:btmon→dbus signal→状态机" | Store namespace `("procedures",)` + few-shot 示例 |

**合并四杠杆**(来自 Hindsight 框架,write-time 策略):
Importance(只存根因/模式,不存日志流水)→ Merge(实体消歧 + recency/source/confidence 择优)→ Decay(指数衰减 + 补丁发布即 invalidate)→ Eviction(仅合规用,不为性能删)。

### 5.3 日志符号化服务 `services/log_symbolizer/`

三条路径把日志对齐到源码行(你场景的核心差异化能力):

| 日志类型 | 工具 | 对齐方式 |
|---|---|---|
| **内核 oops/panic** | `addr2line -e vmlinux -f -C <addr>` + `/proc/kallsyms` | 直接出文件:行(需 `CONFIG_DEBUG_INFO=y`) |
| **btmon(HCI)** | 协议层无地址 → 提取 opcode/event 码 → 在 bluez 源码 switch-case 映射处理函数 | tree-sitter 检索 `hci_le_cis_established_evt` 等符号 |
| **wpa_supplicant `-dd`** | 日志行通常自带函数名(如 `wpa_supplicant_assoc_req_ie_cb`) | ctags 直接定位 |

**时间线重建**:把 btmon + dmesg + wpa log + journalctl 按统一时间轴合并,NTP 校准,每事件标对应代码符号,作为 LLM 上下文推因果链。

### 5.4 静态分析服务 `services/static_analysis/`

封装内核/C 静态分析器,作为 verify 节点的"确定性兜底":

| 工具 | 用途 | 调用 |
|---|---|---|
| **Sparse** | 地址空间违规(`__user`/`__iomem`/`__rcu`)、锁注解 | `make C=2 drivers/bluetooth/` |
| **Smatch** | 空指针、未初始化、锁问题(Sparse + 数据流) | `smatch_scripts/kchecker` |
| **Coccinelle** | API 误用模式(Semantic Patch Language) | `make coccicheck MODE=report` |
| **scan-build / `-fanalyzer`** | 通用 C bug(死存储、空指针) | `scan-build make CC=clang` |
| (可选)**CodeQL** | 自定义 bug 模式查询 | `codeql database create` |

### 5.5 检索服务

移植 deer-flow [community/](deer-flow/backend/packages/harness/deerflow/community/) 的搜索/抓取:
- 搜索:Tavily(质量高,推荐)/ DDG(免费)/ Brave / Exa / Serper。
- 抓取:httpx + markdownify;或 Firecrawl / Jina Reader。
- 切换只改 `config.yaml` 一行 `use:` 路径。

---

## 6. 三条工作流详细设计

### 6.1 工作流 ① Bug-RCA(显式 StateGraph)

范式:学术界 MA-RCA / LLM4FL 验证的 **hypothesize → locate → verify → refine** 循环。

```
START → triage ────────────────────────────┐
   (解析日志/症状,符号化,Recall 历史)      │
            ▼                              │
        hypothesize ── Top-K 假设 ──┐       │
                                  │ Send   │
                          ┌───────▼───────┐│
                          │    locate     ││ ← grep_symbol/read_function/
                          │  (并行×K)     ││   get_callers + 日志符号化结果
                          └───────┬───────┘│
                                  ▼        │
                          ┌──────────────┐ │
                          │   verify     │ │ ← 静态分析/调用链交叉/对照症状
                          │ (verify_loop)│ │   失败 → refine 回 hypothesize(最多 N 轮)
                          └──────┬───────┘ │
                                 │ pass    │
                          ┌──────▼──────┐  │
                          │   report    │  │ ← 结构化模板:症状/根因/证据链/修复建议/置信度
                          └──────┬──────┘  │
                          ┌──────▼──────┐  │
                          │  memorize   │  │ ← 抽取事实 → 消歧 → 合并 → 入记忆
                          └─────────────┘  │
```

**关键设计**:
- **triage** 先跑 Recall:同类 bug 是否见过?直接复用首解路径。
- **hypothesize** 用 `Send` API 对 Top-K 假设并行 locate(map-reduce),互不污染上下文。
- **verify** 用**对抗式**:独立 verifier 子代理 + 确定性工具(静态分析/编译/测试),不让同一个 LLM 自评。
- **report** 模板带证据链(每个结论附 源码 file:line + 日志原文 + 引用),置信度低时显式标注。
- **memorize** 只抽根因/模式/规则,不存原始日志流水。

### 6.2 工作流 ② Deep-Research(supervisor + 子代理 + 沙箱)

借鉴 LangChain 官方 `open_deep_research` + `deepagents` 的 `task()` 子代理模式:

```
supervisor(lead)
  ├─ planner        分解为子问题(Top-K)
  ├─ Send→ researcher ×K   并行 search + fetch + 摘要(带引用)
  ├─ adversary      对抗验证:提取原子断言 → 找反例 → 不过打回   ★
  ├─ experimenter   沙箱里实际跑测试/复现验证结论                 ★
  ├─ synthesizer    跨子代理去重引用 + 综合报告
  └─ memorize       内化
```

**两个关键差异点**(把"会写报告"升级为"会做研究"):
1. **对抗式验证节点**:朴素"LLM 自检"有自我认同偏差(ACL 2025 实证)。独立红队子代理专门找反例/跑反例测试。
2. **实测验证闭环**:沙箱里编译/跑测试,配合 D6 仿真(hwsim/hci_vhci)在 CI 里无人值守验证。借鉴 deer-flow `/goal` 目标驱动续跑(便宜评估模型判定"是否达成",自动续跑到测试通过,带熔断:2 次无进展即停)。

**HITL 断点**:planner 审批 + 报告定稿前。

### 6.3 工作流 ③ PR-Tracker(cron + Send map-reduce)

```
cron(每天,UTC) → graphql 增量拉 bluez/wpa 近期 PR(updatedAt 过滤 + cursor 分页)
                   │
                   └─Send→ PR-reviewer ×N(并行,每 PR 独立上下文)
                            ├─ fetch diff
                            ├─ 影响面分析(blame + 调用图 + 依赖,复用 §5.1 代码索引)
                            ├─ 与本地分支冲突评估
                            └─ 输出决策卡(JSON)
                   ▼
              reduce 汇总 → "合入/cherry-pick/观望/跳过" 清单 → memorize
```

**"是否建议合入"决策卡**(借鉴 PR-Agent/CodeRabbit 维度):
```json
{"recommendation":"cherry-pick","confidence":0.82,
 "dimensions":{
   "safety":{"score":4,"notes":"修复 use-after-free,有 CVE"},
   "compatibility":{"score":5,"notes":"API 向后兼容"},
   "dependencies":{"score":3,"notes":"依赖上游另一未合入 commit"},
   "test_coverage":{"score":4,"notes":"含回归测试"},
   "upstream_stability":{"score":4,"notes":"已在上游 main,2 周"}},
 "conflict":"与本地 patch btusb-quirk 冲突,需手动适配",
 "action_items":["本地验证编译","跑 hwsim 回归"]}
```

**调度**:LangGraph cron。**轮询用 GraphQL**——一次拿全 PR + reviews + comments + files,比 REST 省额度;`updatedAt` 过滤 + cursor 分页只拉增量。

---

## 7. 持续学习闭环(一等公民)

### 7.1 Recall(读,工作流入口)

```python
# src/my_agent/services/memory/recall.py(伪代码)
async def recall(query: str, component: str, top_k: int = 5) -> list[Memory]:
    sem  = await store.search(namespace=(component,"semantic"), query=query, limit=20)
    epis = await store.search(namespace=(component,"episodes"), query=query, limit=20)
    graph = await graphiti.search(query)                    # 图遍历(多跳)
    fused = rrf([sem, epis, graph])                         # 倒排融合
    final = await reranker.rerank(query, fused, top_k=top_k)  # bge-reranker-v2-m3
    return final  # 每条带 source/case_id/confidence/valid_at → 注入 prompt
```

注入规则:rerank 后只取 top-3~5,按 `score × confidence × recency_weight` 排序,**每条带溯源 + 时间戳**,让模型知道可信度与时效。

### 7.2 Memorize(写,工作流出口)

```python
# src/my_agent/services/memory/memorize.py(伪代码)
async def memorize(report: BugReport, component: str):
    # ① 抽取:按预定义本体(Component/Interface/State/Function/Symptom/RootCause/Patch/Version)
    facts = await extractor.extract(report, ontology=ONTOLOGY[component])
    for f in facts:
        f.entities = canonicalize(f.entities)               # ② write-time 实体消歧
        f.confidence = llm_score(f) * source_weight(report) # ④ 置信度
    # ③ 合并:recency-wins + 显式失效(旧事实标记 invalid,不删除)
    await mem0.add(facts, user_id=component, merge="recency")
    await graphiti.add_episode(report.id, report.text, reference=report.url)  # ⑤ 时序图
    # 附 provenance
```

**关键原则**:
- write-time 严格过滤(只存根因/模式/规则,不存日志流水)——脏数据进索引后 rerank 也救不回来。
- 冲突显式失效而非并存,避免两条矛盾事实都进 top-k。
- 每条记忆保留到 episode(原始报告)的引用,可追溯。

### 7.3 关于"持续学习"的清醒认知

检索分数好 ≠ 记忆对。真正衡量是**合并策略让"该被召回的"存活下来**。所以必须有评测闭环(§13),否则记忆会悄悄膨胀/污染。

---

## 8. 领域知识建模

把 **状态机作为一等公民**建模(纯向量 RAG 做不到的多跳推理)。用 Graphiti 的 Pydantic 自定义实体/边类型:

```python
# data/knowledge/ontology.py
class Component(EntityNode): name: str; subsystem: str; source_path: str
class StateTransitionEdge(Edge):
    __edge_type__ = "TRANSITIONS_TO"
    trigger: str           # 触发条件——很多 bug 就是非法迁移
    valid_at: datetime     # 该迁移在哪个版本起存在

# BlueZ 协议栈:HCI 状态机 / L2CAP(ERTM) / A2DP(configured→open→streaming→suspend→close)
#   / RFCOMM / LE ISO(CIS, 蓝牙 5.2+ LE Audio)
# wpa_supplicant:EAPOL/EAP/RSN 4-way handshake 状态机
#   (disconnected→scanning→authenticating→associating→4way→groupkey→completed)
```

领域知识字典存 `data/knowledge/{bluez,wpa}/` 为 YAML:状态名 + 转换条件 + 对应源码 file:line + 典型日志特征(btmon opcode / wpa 日志模式)。Agent 先匹配状态机字典定位协议层,再深入源码。

---

## 9. 技术栈

| 层 | 选型 | 包 |
|---|---|---|
| 编排 | LangGraph + langgraph-supervisor + deepagents | `langgraph>=1.2`, `langgraph-supervisor`, `deepagents` |
| 模型 | 任选(OpenAI/Anthropic/DeepSeek/Ollama/vLLM),分层路由 | `langchain_openai` / `langchain_anthropic` / `langchain_ollama` |
| 代码解析 | tree-sitter + tree-sitter-c + universal-ctags | `tree-sitter`, `tree-sitter-language-pack` |
| 向量库 | LanceDB(本地)/ Qdrant(生产) | `lancedb` |
| 静态分析 | sparse / smatch / coccinelle / scan-build | 内核工具链 |
| 符号化 | addr2line / objdump / kallsyms | binutils |
| 记忆-基座 | LangGraph Store + PostgresSaver | `langgraph` + PG + pgvector |
| 记忆-抽取 | mem0 OSS(Apache-2.0) | `mem0ai` |
| 记忆-时序KG | Graphiti + FalkorDB(Apache-2.0) | `graphiti-core` |
| 记忆-领域KG | LightRAG | `lightrag-hku` |
| 检索增强 | BM25 + RRF + `bge-reranker-v2-m3` | — |
| web 检索 | Tavily / DDG + httpx+markdownify | deer-flow `community/` |
| 沙箱 | Docker(自建)/ OpenHands runtime | — |
| 可观测 | Langfuse(自托管) | — |
| 部署 | LangGraph Self-Hosted Lite(免费至 1M 节点)→ 或 FastAPI+PG+Docker | — |

---

## 10. 项目结构

```
my-agent/
├── pyproject.toml
├── config/
│   ├── config.yaml             # 模型/角色/工具/记忆/沙箱 声明式配置
│   ├── extensions_config.json  # MCP servers + skills
│   └── .env                    # API keys
├── src/my_agent/
│   ├── platform/               # Harness
│   │   ├── models.py           # ★ create_chat_model(多 provider 工厂)
│   │   ├── reflection.py       # ★ resolve_class / resolve_variable
│   │   ├── config.py           # AppConfig + $ENV 解析 + 热重载
│   │   ├── gateway.py          # FastAPI 入口
│   │   ├── tools/              # 工具注册 + MCP
│   │   ├── sandbox/            # local / docker
│   │   ├── observability.py    # Langfuse
│   │   └── runtime.py          # checkpointer + cron
│   ├── workflows/
│   │   ├── bug_rca/            # ① StateGraph + nodes + tools
│   │   ├── deep_research/      # ② supervisor + 子代理
│   │   └── pr_tracker/         # ③ cron + Send
│   ├── services/               # ★ 共享服务层
│   │   ├── code_index/         # tree-sitter/ctags/LanceDB + 混合检索
│   │   ├── memory/             # Store/mem0/Graphiti + Recall/Memorize
│   │   ├── log_symbolizer/     # addr2line/btmon/wpa
│   │   └── static_analysis/    # sparse/smatch/coccinelle
│   ├── models/                 # patched providers(DeepSeek/vLLM/...)
│   ├── tools/                  # agent 可调用工具(导航/检索/执行)
│   └── prompts/
├── data/
│   ├── knowledge/{bluez,wpa}/  # 状态机字典(YAML)
│   └── datasets/               # ground truth(git fix commits 提取)
├── eval/                       # Top-N / MFR / LongMemEval
├── tests/
├── docs/architecture.md        # 本文件
└── deer-flow/                  # 参考实现(只读,当零件目录)
```

---

## 11. 分阶段路线图

| 阶段 | 目标 | 关键交付 | 退出标准 |
|---|---|---|---|
| **P0 地基** (1–2w) | 平台骨架能跑 | LangGraph 骨架 + **模型工厂(多 provider)** + config + Langfuse + 本地沙箱 + demo agent(bash/read_file) | 给简单问题,agent 在沙箱里 ls/读文件/回答;切换 provider 只改配置 |
| **P1 代码理解** (2–3w) | 共享地基 | tree-sitter repo map + ctags + LanceDB 函数级 chunk + BM25/向量/RRF/rerank;以 bluez 首建索引;导航工具可用 | 函数名/错误码混合检索召回 top-5 准确 |
| **P2 Bug-RCA MVP** (3–4w) | 场景①跑通 | triage→locate→verify→report 图 + 三路径符号化 + 静态分析 + 报告模板 | 真实 bluez/wpa bug 日志 → 定位到正确文件/函数并出报告 |
| **P3 记忆+学习** (2–3w) | 内化闭环 | Memorize pipeline + Recall 注入 + 评测集;进阶 Graphiti 领域 KG | 同类 bug 第二次出现,Recall 召回首解;LongMemEval 指标达标 |
| **P4 PR-Tracker** (2–3w) | 场景③跑通 | cron + GraphQL 增量 + Send 并行 review + 决策卡 + 冲突评估 | 定期产出 bluez/wpa 合入建议清单 |
| **P5 Deep-Research** (3–4w) | 场景②跑通 | supervisor + 子代理 + 对抗验证 + 沙箱实测(hwsim/hci_vhci) + 移植 deep-research SKILL | 给调研问题 → 搜 web + 结合代码 + 实跑测试 + 出带引用报告 |
| **P6 生产化** (持续) | 上线 | Docker 部署 + 模型分层控成本 + 评测 harness + 记忆膨胀治理 | 稳定、成本可控、可观测、可回放 |

> **路线逻辑**:P1 代码索引是 P2/P4 共同地基,先建;P2 最难且逼出工具链;P3 紧跟 P2(有报告可内化);P4 比 P5 简单且高价值,先于 P5;P5 复用前面一切。

---

## 12. 关键风险与对策

| 风险 | 对策 |
|---|---|
| 真机复现难 | D6 仿真(hwsim/hci_vhci)+ 静态分析做无硬件验证代理;符号/日志级验证为主,真机最终确认 |
| C 解析精度 | tree-sitter 容错做主力;clangd 按需补精确(`bear -- make` 生成 compile_commands.json) |
| 成本/token 爆炸 | 模型分层(summarizer 用 mini/本地)+ 子代理并发上限 + 工具预算(researcher "简单≤3 次搜索,5 次找不到就停") |
| 记忆污染/膨胀 | write-time 严格过滤 + rerank 后只取 top-3~5 + recency/confidence 降权 + 显式失效;仅合规做 eviction |
| LLM 自查不可靠 | 对抗式验证(独立红队)+ 确定性工具兜底(静态分析/测试) |
| provider 切换踩坑 | 工厂函数归一化:base_url 别名、stream_usage 默认开、stream_chunk_timeout 240s、未知字段告警(全抄 deer-flow factory.py) |

---

## 13. 评估方法

**缺陷定位准确率**(场景①/②):
- 指标:**Top-1/3/5 Accuracy**、**MFR**(首正确排名)、**MAR**(平均排名)、**EXAM**(检查比例)。
- Ground truth:从 bluez([github.com/bluez/bluez](https://github.com/bluez/bluez))、wpa([w1.fi/cgit/hostap/](https://w1.fi/cgit/hostap/))git 历史用 `git log --grep="Fixes:"` + CVE 批量提取 fix commit 改的文件/行。给 agent bug 时的日志,收集它定位的 Top-N,算准确率。

**记忆有效性**(持续学习):
- LongMemEval 式多会话推理准确率([ICLR 2025](https://arxiv.org/abs/2410.10813))。
- 矛盾注入测试:注入状态变更,测 agent 一周后能否返回新状态。
- 每轮 token 成本应随索引增长平稳,线性涨说明合并策略失败。

**生产监控**:每轮检索数、token 用量、实体合并率、事实失效事件、缓存命中率。

---

## 14. 参考资料

**深度研究 Agent**
- LangChain open_deep_research: https://github.com/langchain-ai/open_deep_research
- deepagents(子代理 task()): https://docs.langchain.com/oss/python/deepagents/deep-research
- GPT Researcher: https://github.com/assafelovic/gpt-researcher
- LangGraph Send API(map-reduce): https://docs.langchain.com/oss/python/langgraph/use-graph-api

**记忆与持续学习**
- LangGraph Stores: https://docs.langchain.com/oss/python/langgraph/stores
- mem0 LangGraph 集成: https://docs.mem0.ai/integrations/langgraph
- Graphiti(时序 KG): https://github.com/getzep/graphiti
- LightRAG: https://github.com/hkuds/lightrag
- 四杠杆合并框架(Hindsight): https://hindsight.vectorize.io/blog/2026/05/21/agent-memory-consolidation
- LongMemEval(ICLR 2025): https://arxiv.org/abs/2410.10813

**LLM 缺陷定位**
- OrcaLoca(ICML 2025): https://github.com/fishmingyu/OrcaLoca
- FlexFL(TSE 2025): https://dl.acm.org/doi/10.1109/TSE.2025.3553363
- RepoGraph(ICLR 2025): https://github.com/ozyyshr/RepoGraph
- Aider repo map: https://aider.chat/docs/repomap.html
- SWE-bench: https://www.swebench.com/
- 内核静态分析教程: https://gautammenghani.com/linux,/c/2022/05/19/static-analysis-tools-linux-kernel.html

**日志符号化**
- btmon wiki: https://github.com/bluez/bluez/wiki/btmon
- 内核 oops 解析: https://www.kernel.org/doc/html/v4.13/admin-guide/bug-hunting.html
- wpa_supplicant 代码结构: https://w1.fi/wpa_supplicant/devel/code_structure.html

**PR 分析**
- PR-Agent: https://github.com/The-PR-Agent/pr-agent
- CodeRabbit Context Engineering: https://www.coderabbit.ai/blog/context-engineering-ai-code-reviews
- LangGraph cron: https://docs.langchain.com/langsmith/cron-jobs
- GitHub GraphQL PR: https://docs.github.com/en/graphql/reference/pulls

**沙箱与验证**
- OpenHands Runtime: https://docs.openhands.dev/openhands/usage/architecture/runtime
- 对抗式事实性(ACL 2025): https://aclanthology.org/2025.acl-long.81.pdf

**多 Agent**
- langgraph-supervisor: https://reference.langchain.com/python/langgraph-supervisor
- langgraph-swarm: https://github.com/langchain-ai/langgraph-swarm
- 多 Agent 架构基准: https://www.langchain.com/blog/benchmarking-multi-agent-architectures

**部署**
- LangGraph Platform(自托管): https://docs.langchain.com/langsmith/deploy-standalone-server
- FastAPI+LangGraph 生产模板: https://github.com/wassim249/fastapi-langgraph-agent-production-ready-template

**参考实现**
- deer-flow(本仓库子目录): [deer-flow/](deer-flow/)
  - 模型工厂: [factory.py](deer-flow/backend/packages/harness/deerflow/models/factory.py)
  - 配置示例: [config.example.yaml](deer-flow/config.example.yaml)
  - 沙箱: [sandbox/](deer-flow/backend/packages/harness/deerflow/sandbox/)
  - 记忆: [agents/memory/](deer-flow/backend/packages/harness/deerflow/agents/memory/)
  - 子代理: [subagents/](deer-flow/backend/packages/harness/deerflow/subagents/)
  - 社区检索工具: [community/](deer-flow/backend/packages/harness/deerflow/community/)
