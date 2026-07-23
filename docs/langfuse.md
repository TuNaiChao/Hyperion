# Langfuse 可观测性(P0 可选启用指南)

> 状态:**可选**。不启用时 agent 照常运行、零开销。想看"agent 内部发生了什么"时再开。

## 1. 它是什么 / 解决什么

**Langfuse 是 LLM 应用的"监控仪表盘"**——可以理解成"给 agent 装了行车记录仪 + 油耗表"。

普通程序出问题看日志就行;但 agent 是"模型自己想几步、调几个工具、再回答",一旦答错,**你不知道它是哪一步想歪的**。Langfuse 把这个黑盒打开:每次 run 的 prompt、回复、工具调用、token、耗时、成本,全部录下来,在网页面板里像看录像一样回放。

对 Hyperion 尤其重要:Bug-RCA / 深度研究是长链路、高 token、多步骤,没这层可观测就无从做**成本控制**和**"为什么定位错"的复盘**。奔着生产级去的项目,observability 是标配。

## 2. 三个核心概念

一次 `hyperion run` 在 Langfuse 里是一棵树:

```
TRACE  "为什么蓝牙断连"                       ← 一个完整请求 = 一条 trace(树根)
 ├─ SPAN  LLM #1   [320 tok, 1.2s, $0.01]     ← 模型决定先调 ls
 ├─ SPAN  工具 ls   [0.1s]  返回: a2dp.c / hci.c ...
 ├─ SPAN  LLM #2   [880 tok, 2.1s, $0.03]     ← 模型决定读 hci.c
 ├─ SPAN  工具 read_file [0.05s]  返回: ...
 ├─ SPAN  LLM #3   [1200 tok, 3.0s, $0.05]    ← 最终回答
 └─ (最终输出)
```

| 概念 | 含义 | 我们的对应 |
|---|---|---|
| **Trace** | 一个完整请求(提问→回答),树根 | 一次 `agent.invoke` |
| **Span / Observation** | trace 里的一步(LLM 调用 / 工具调用 / 图节点) | LangGraph 自动嵌套 |
| **Session** | 同一对话的多条 trace 归到一张卡 | `langfuse_session_id = thread_id` |
| **User / Tags** | 归属用户 / 标签(按模型、环境筛) | `langfuse_user_id` / `langfuse_tags=[model:xxx]` |

## 3. 面板上能看到什么

- 每条 trace 的完整树 + 每步的 **prompt 原文 / 回复原文 / token 数 / 耗时 / 成本 / 是否报错**;
- 总览:今天跑多少次、总 token、总成本、平均延迟、错误率;
- 按 session / user / tag / model 筛选对比(如 "gpt-4.1 vs deepseek-chat 谁更准更便宜")。

## 4. 怎么启用(4 步)

### 步骤 1:起一个 Langfuse 服务(本地,docker compose)

前置:`git` + `docker` + `docker compose`(macOS/Windows 用 Docker Desktop)。

```bash
git clone https://github.com/langfuse/langfuse.git
cd langfuse
# 本地试玩可先不改;生产部署务必改 docker-compose.yml 里标了 # CHANGEME 的密钥
docker compose up            # 2~3 分钟,看到 langfuse-web-1 日志 "Ready" 即可
```

打开 `http://localhost:3000` → 注册账号 → 新建一个 Project。

> 官方文档:<https://langfuse.com/self-hosting/deployment/docker-compose>

### 步骤 2:拿密钥

在 Project 设置里 **Create new API credentials**,拿到三个值:

- `Public Key`(`pk-lf-...`)
- `Secret Key`(`sk-lf-...`)
- `Host URL`(本地自托管 = `http://localhost:3000`)

### 步骤 3:装 SDK + 填 `.env`

```bash
uv add langfuse
```

把三个值填进项目根的 `.env`(参照 `.env.example`):

```bash
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=http://localhost:3000
```

### 步骤 4:跑 agent + 看面板

```bash
uv run hyperion run "在工作区里 ls 并告诉我看到了什么"
```

回到 Langfuse 面板 → Traces,就能看到刚才这次 run 的完整 trace 树。

## 5. 我们项目里的接线(已就绪)

埋点代码已在 [src/hyperion/platform/tracing.py](../src/hyperion/platform/tracing.py),设计为**完全 opt-in**:

- `build_tracing_callbacks()`:三个 `LANGFUSE_*` 没配齐 → 返回 `[]`;配了但没装 `langfuse` 包 → `try/except` 兜底也返回 `[]`。**不启用时零开销**。
- `tracing_metadata()`:同样未配置时返回 `{}`,调用方无条件 merge 即可。
- 二者都在 [agent.py](../src/hyperion/platform/agent.py) 的 `run_demo` 里挂到**图调用根**(`config["callbacks"]` / `config["metadata"]`)——这是 Langfuse v4 把 trace 属性提到根 span 的前提(单 run 单 trace,所有 node/LLM/tool 是子 span)。

**约定**(对齐架构 §4.5):

```
langfuse_session_id = thread_id      # 一个对话 = 一个 session 卡片
langfuse_user_id    = user_id        # 归属用户(默认 "default")
langfuse_tags       = [model:xxx]    # 按模型筛
```

## 6. 不想自托管?

也可以用官方云([langfuse.com](https://langfuse.com),有免费额度):跳过步骤 1,`LANGFUSE_HOST` 填云端的 `https://cloud.langfuse.com`(或对应 region)。其余步骤一样。

## 参考

- 自托管 docker compose:<https://langfuse.com/self-hosting/deployment/docker-compose>
- LangChain 集成:<https://langfuse.com/integrations/frameworks/langchain>
- LangGraph 可观测 cookbook:<https://langfuse.com/guides/cookbook/integration_langgraph>
- 架构定位:见 [architecture.md §4.5](architecture.md#45-可观测性)
