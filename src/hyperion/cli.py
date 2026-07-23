"""Hyperion CLI 入口(`uv run hyperion ...`)。

子命令:
  hyperion models            列出 config.yaml 中配置的模型
  hyperion run "问题"        跑一次 demo agent(P0)
"""

from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from hyperion.platform.config import get_app_config


def cmd_models(args) -> int:
    """列出 config.yaml 中配置的模型与角色路由。"""
    cfg = get_app_config()
    if not cfg.models:
        print("(config.yaml 中未配置任何模型)")
        return 1
    for m in cfg.models:
        caps = []
        if m.supports_thinking:
            caps.append("thinking")
        if m.supports_vision:
            caps.append("vision")
        cap_str = f"  [{', '.join(caps)}]" if caps else ""
        print(f"- {m.name:20} {m.use:45}{cap_str}")
    if cfg.model_roles:
        print("\nroles:")
        for role, target in cfg.model_roles.items():
            print(f"  {role:16} -> {target}")
    return 0


def cmd_run(args) -> int:
    """跑一次 demo agent,打印最终回复。

    真正 invoke 会发起 LLM 请求,需要有效 API key(.env)。
    构造时校验 key:无 key 会抛 OpenAIError,这里兜底成可读提示。
    """
    # 延迟导入:run 路径较重(模型工厂 + 工具 + langgraph),只在用到时加载
    from hyperion.platform.agent import run_demo

    try:
        answer = run_demo(args.question, role=args.role, model=args.model)
    except Exception as e:  # noqa: BLE001 - CLI 顶层兜底,要给用户可读提示而非栈
        msg = str(e).lower()
        if any(k in msg for k in ("api_key", "api key", "credential", "missing")):
            print(
                "错误:缺少 LLM API key。请在项目根的 .env 填入(参照 .env.example),\n"
                "      例如 OPENAI_API_KEY=sk-... 或 DEEPSEEK_API_KEY=...",
                file=sys.stderr,
            )
            return 2
        print(f"运行出错:{e}", file=sys.stderr)
        return 1
    print(answer)
    return 0


def main(argv: list[str] | None = None) -> int:
    # 把 .env 读进环境变量;必须在任何 config/$VAR 解析之前
    load_dotenv()

    parser = argparse.ArgumentParser(prog="hyperion", description="Hyperion agent")
    sub = parser.add_subparsers(dest="cmd")

    sub_models = sub.add_parser("models", help="列出 config.yaml 中配置的模型")
    sub_models.set_defaults(func=cmd_models)

    sub_run = sub.add_parser("run", help="跑一次 demo agent")
    sub_run.add_argument("question", help="要问 agent 的问题")
    sub_run.add_argument("--model", default=None, help="指定模型名(默认走 role 路由)")
    sub_run.add_argument("--role", default="default", help="模型角色(默认 default)")
    sub_run.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    if getattr(args, "func", None):
        return args.func(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
