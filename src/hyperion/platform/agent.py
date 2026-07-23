"""demo agent:把模型工厂 + 工具用 create_agent 拼成一个可运行 agent。

P0 的"能跑通"证明:模型经 create_chat_model(role=...) 构造,工具经 get_available_tools()
加载,二者用 langchain.agents.create_agent 组合成一个 ReAct agent;invoke 一个问题就能在
沙箱里 ls/读文件/跑命令,再基于观察回答。

用 langchain.agents.create_agent(而非已弃用的 langgraph.prebuilt.create_react_agent),
与 deer-flow 同款 API。中间件链(build_middlewares)目前为空(P0 最小实现),生产级护栏
(工具错误兜底/输出预算/写前哈希门/循环检测/token 预算…)按 deer-flow 对齐,P2+ 逐步挂
(见 .claude/memory/backlog-production-grade.md 第 3 条)。
"""

from __future__ import annotations

from langchain.agents import create_agent
from langchain.chat_models import BaseChatModel
from langchain_core.messages import AIMessage

from hyperion.platform.config import AppConfig, get_app_config
from hyperion.platform.models import create_chat_model
from hyperion.platform.tracing import build_tracing_callbacks, tracing_metadata
from hyperion.tools.registry import get_available_tools

DEMO_SYSTEM_PROMPT = """你是 Hyperion —— 面向系统软件(C/内核)的代码与日志分析助手。

你可以调用工具在沙箱里探索文件系统、读代码、跑命令,然后基于观察回答问题。
工作原则:
- 不确定时先用工具查证,不要凭空猜测。
- 回答尽量附证据(文件路径、命令输出片段)。
当前是 P0 阶段(通用 coding-agent 式能力);领域工具(蓝牙/wifi 解析)后续按需挂载。"""


def build_middlewares() -> list:
    """构造 agent 中间件链。

    P0 最小实现:返回空列表(裸 agent,无护栏)。
    生产级对齐 deer-flow(其 build_middlewares 约 30 项),Hyperion 计划逐步加入:
      ToolErrorHandlingMiddleware / ToolOutputBudgetMiddleware / ReadBeforeWriteMiddleware
      / LoopDetectionMiddleware / TokenBudgetMiddleware / SummarizationMiddleware
    详见 .claude/memory/backlog-production-grade.md 第 3 条(目标 P2+)。
    """
    return []


def build_demo_agent(
    *,
    role: str = "default",
    model: str | None = None,
    tools=None,
    config: AppConfig | None = None,
):
    """构造 demo ReAct agent。

    Args:
        role: 模型角色(走 config.model_roles 路由,做成本分层)。
        model: 直接指定模型名;给定则优先于 role。
        tools: 工具列表;None 表示加载 config 里声明的全部工具。
        config: 可选配置(测试时传入)。
    """
    llm: BaseChatModel = create_chat_model(name=model, role=role, config=config)
    agent_tools = tools if tools is not None else get_available_tools(config=config)
    return create_agent(
        model=llm,
        tools=agent_tools,
        system_prompt=DEMO_SYSTEM_PROMPT,
        middleware=build_middlewares(),
    )


def run_demo(
    question: str,
    *,
    role: str = "default",
    model: str | None = None,
    thread_id: str | None = None,
) -> str:
    """跑一次 demo agent,返回最后一条 AI 回复的文本。

    注意:真正 invoke 会发起 LLM 请求,需要有效的 API key(见 .env)。
    """
    cfg = get_app_config()
    agent = build_demo_agent(role=role, model=model, config=cfg)
    model_name = model or cfg.model_roles.get(role) or "default"

    # callbacks/metadata 都挂在图根:Langfuse 才能把整 run 收成一条 trace
    run_config = {
        "callbacks": build_tracing_callbacks(),
        "metadata": tracing_metadata(thread_id=thread_id, model_name=model_name),
    }
    result = agent.invoke(
        {"messages": [{"role": "user", "content": question}]},
        config=run_config,
    )
    # 取最后一条 AIMessage 的文本作为最终回复
    for msg in reversed(result.get("messages", [])):
        if isinstance(msg, AIMessage) and msg.content:
            return msg.content if isinstance(msg.content, str) else str(msg.content)
    return ""
