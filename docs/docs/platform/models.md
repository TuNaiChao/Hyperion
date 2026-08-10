# 平台 · 模型工厂

> `platform/models.py` —— 按 config 反射造 `BaseChatModel`,做 thinking / base_url 归一化。**加新 provider 通常零代码,只改配置。**

## 概览

不硬编码任何厂家。`config.yaml` 每个模型声明 `use: <module>:<ClassName>`,本工厂用反射加载任意 LangChain chat model 类,把 api_key / base_url 等从 `$ENV` 解析后注入,并按 `supports_thinking` 归一化思考模式开关。任务角色(planner / locator / summarizer …)经 `model_roles` 路由到具体模型,换路由零代码。

## 源码

| 文件 | 职责 |
|---|---|
| `platform/models.py` | `create_chat_model` —— 反射造模型 + thinking / base_url 归一化 |
| `platform/reflection.py` | `resolve_class` —— `'module:Class'` → 真实类(模型 / 工具加载的基础;缺包给 `uv add` 提示) |
| `platform/config.py` | `ModelConfig` / `AppConfig.get_model(name)` / `model_roles` |

## API

```python
def create_chat_model(
    name: str | None = None,
    *,
    thinking_enabled: bool = False,
    role: str | None = None,
    config: AppConfig | None = None,
    **overrides,
) -> BaseChatModel
```

- `name`:config 里声明的模型名(如 `deepseek-v4-pro`)。与 `role` 二选一。
- `role`:任务角色(如 `locator` / `summarizer`),经 `model_roles` 路由到模型名。
- `thinking_enabled`:打开思考模式(模型须 `supports_thinking: true`,并配 `when_thinking_enabled` / `when_thinking_disabled`)。
- `config`:可注入(测试用),默认 `get_app_config()`。
- `**overrides`:覆盖 ModelConfig 的任意字段。

## 流程

1. 解析 `name` / `role` → 经 `model_roles` 路由到一条 `ModelConfig`。
2. `reflection.resolve_class(model.use)` 拿到 LangChain chat model 类。
3. 组装构造参数:`api_key` / `base_url` 等从 `$ENV` 解析;按 `supports_thinking` 注入思考模式开关。
4. 实例化并返回 `BaseChatModel`。

## 配置

见 [configuration.md](../configuration.md) §models / §model_roles。典型一项:

```yaml
models:
  - name: deepseek-v4-pro
    use: langchain_openai:ChatOpenAI
    model: deepseek-v4-pro
    api_key: $DEEPSEEK_API_KEY
    base_url: https://api.deepseek.com
    supports_thinking: true
    when_thinking_enabled: { thinking: { type: enabled, budget_tokens: 8000 } }
    when_thinking_disabled: { thinking: { type: disabled } }
```

## 加新 provider(零代码)

再写一项,`use` 指向对应 LangChain 类即可,无需改 `models.py`:

```yaml
  - name: claude-sonnet
    use: langchain_anthropic:ChatAnthropic     # 需 uv sync --extra providers
    model: claude-sonnet-4-5
    api_key: $ANTHROPIC_API_KEY
  - name: qwen3-local
    use: langchain_ollama:ChatOllama
    model: qwen3:32b
    base_url: http://localhost:11434
```

## 边界与限制

- **DeepSeek 思考模式不支持结构化产出**:思考模式下不能用 `tool_choice` / `response_format: json_schema`。需要结构化产出时,改用「喂 JSON Schema 给 prompt + 模型直出 JSON + 鲁棒解析」(见 `services/memory/backends/native/extract.py` 的做法)。
- `supports_thinking` 模型必须同时配 `when_thinking_enabled` / `when_thinking_disabled`,否则 thinking 开关不生效。
- embedding / rerank 走 DashScope(不是 DeepSeek);见 [services/code-index.md](../services/code-index.md)。

## 示例

```python
from hyperion.platform.models import create_chat_model

m = create_chat_model("deepseek-v4-pro")
m2 = create_chat_model(role="locator")              # 经 model_roles 路由
m3 = create_chat_model("deepseek-v4-pro", temperature=0.0)  # override
```

## See Also

- [configuration.md](../configuration.md) — 配置全段
- [../services/memory.md](../services/memory.md) — extract.py 的结构化产出规避
- [../../CLAUDE.md](../../../CLAUDE.md) §模型
