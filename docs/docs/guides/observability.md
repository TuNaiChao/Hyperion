# 指南 · Langfuse 可观测(可选)

> **可选**。不启用时零开销。想看"agent 内部发生了什么"(哪步想歪、token 烧在哪)时再开。

## 它是什么

Langfuse 是 LLM 应用的监控仪表盘 —— 给 agent 装"行车记录仪 + 油耗表"。普通程序出错看日志;agent 是"模型自己想几步、调几个工具再答",答错时你不知道它哪步歪。Langfuse 把每次 run 的 prompt、回复、工具调用、token、耗时、成本全录下来,在面板里回放。

Hyperion 的 bug-RCA / 深度调研是长链路、高 token、多步骤,没可观测就无从做成本控制和"为什么定位错"的复盘。

## 三个核心概念

一次 run 在 Langfuse 里是一棵树:

```
TRACE  "为什么蓝牙断连"                       ← 一个完整请求 = 一条 trace(树根)
 ├─ SPAN  LLM #1   [320 tok, 1.2s]            ← 模型决定先调 ls
 ├─ SPAN  工具 ls   [0.1s]  返回: a2dp.c ...
 ├─ SPAN  LLM #2   [880 tok, 2.1s]
 └─ ...
```

| 概念 | 含义 | Hyperion 对应 |
|---|---|---|
| **Trace** | 一个完整请求,树根 | 一次 agent invoke |
| **Span / Observation** | trace 里的一步(LLM / 工具 / 图节点) | LangGraph 自动嵌套 |
| **Session** | 同一对话的多条 trace 归一张卡 | `langfuse_session_id = thread_id` |

## 怎么启用(4 步)

### 1. 起 Langfuse 服务(本地 docker compose)

```bash
git clone https://github.com/langfuse/langfuse.git
cd langfuse
docker compose up            # 看到 langfuse-web-1 "Ready" 即可
```

打开 `http://localhost:3000` → 注册 → 新建 Project。(也可用官方云 [langfuse.com](https://langfuse.com),跳过这步,`LANGFUSE_HOST` 填云端地址。)

### 2. 拿密钥

Project 设置 → Create new API credentials,拿到:`Public Key`(`pk-lf-...`)、`Secret Key`(`sk-lf-...`)、`Host URL`(本地自托管 = `http://localhost:3000`)。

### 3. 装 SDK + 填 `.env`

```bash
uv add langfuse
```

```bash
# .env
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=http://localhost:3000
```

### 4. 接线 + 跑

埋点工具在 `platform/tracing.py`,**完全 opt-in**:

```python
def build_tracing_callbacks() -> list[Any]    # 三个 LANGFUSE_* 没配齐 → 返回 [];没装 langfuse → 也返回 []
def tracing_metadata(*, thread_id=None, user_id=None, model_name=None) -> dict[str, Any]
    # 未配置 → 返回 {}
```

约定(对齐架构):

```
langfuse_session_id = thread_id      # 一个对话 = 一个 session 卡片
langfuse_user_id    = user_id
langfuse_tags       = [model:xxx]    # 按模型筛
```

> [!NOTE]
> **当前接线状态**:`tracing.py` 的 hooks 在且 opt-in(不启用零开销),但 demo agent 撤销后,Hyperion 自带的 workflow **目前没有内置接线点**。要用 Langfuse 观察某个 workflow,在其 graph invoke 的根上自己挂:
>
> ```python
> from hyperion.platform.tracing import build_tracing_callbacks, tracing_metadata
> callbacks = build_tracing_callbacks()           # 配齐了才有,否则 []
> agent.invoke(inputs, config={"callbacks": callbacks,
>                              "metadata": tracing_metadata(thread_id="..."),
>                              "thread_id": "..."})
> ```
>
> 这是 Langfuse v4 把 trace 属性提到根 span 的前提(单 run 单 trace,所有 node/LLM/tool 是子 span)。后续会由 workflow 按需内置接线。

## 面板上能看到什么

- 每条 trace 的完整树 + 每步 prompt 原文 / 回复原文 / token / 耗时 / 成本 / 是否报错;
- 总览:今天跑多少次、总 token、总成本、平均延迟、错误率;
- 按 session / user / tag / model 筛选对比(如 "模型 A vs 模型 B 谁更准更便宜")。

## 边界与限制

- **opt-in,零开销**:三个 `LANGFUSE_*` 没配齐或没装包 → `build_tracing_callbacks()` 返回 `[]`,agent 照常跑。
- 自托管务必改 `docker-compose.yml` 里标 `# CHANGEME` 的密钥。
- 当前 workflow 未内置接线(见上),需手动挂 callbacks。

## 参考

- 自托管 docker compose:<https://langfuse.com/self-hosting/deployment/docker-compose>
- LangChain 集成:<https://langfuse.com/integrations/frameworks/langchain>
- LangGraph cookbook:<https://langfuse.com/guides/cookbook/integration_langgraph>

## See Also

- [../platform/runtime.md](../platform/runtime.md) — runtime 中间件(token 预算等,另一层可观测)
- [../configuration.md](../configuration.md)
