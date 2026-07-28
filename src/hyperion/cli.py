"""Hyperion CLI 入口(`uv run hyperion ...`)。

子命令:
  hyperion models                列出 config.yaml 中配置的模型
  hyperion run "问题"            跑一次 demo agent(P0)
  hyperion index <path> [name]   为一个代码仓库建/更新向量索引(P1)
  hyperion tools [--group X]     列出已加载的 agent 工具(验证声明式加载)
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


def cmd_index(args) -> int:
    """为一个代码仓库建/更新向量索引(P1 代码理解服务)。

    用法:hyperion index <repo_path> [repo_name]
    例:hyperion index src/hyperion hyperion
       hyperion index ~/src/bluez bluez --force
    没给 repo_name → 用 repo_path 的目录名。⚠️ repo_name 必须和 config.code_index.repo
    (search_code 查的表名)一致,否则 search_code 会查空表。
    """
    from pathlib import Path

    from hyperion.services.code_index.embed import create_embedder
    from hyperion.services.code_index.index import build_index

    cfg = get_app_config()
    repo_path = Path(args.repo_path)
    if not repo_path.exists():
        print(f"错误:路径不存在: {repo_path}", file=sys.stderr)
        return 1
    repo_name = args.repo_name or repo_path.resolve().name
    vs_path = getattr(getattr(cfg.code_index, "vector_store", None), "path", "data/code_index")

    embedder = create_embedder(cfg.code_index.embedding)
    stats = build_index(repo_path, repo_name, embedder, vs_path, force=args.force)
    n = stats.get("indexed", stats.get("total_chunks", "?"))
    print(f"索引完成 [{stats.get('mode')}]:{repo_name}  {n} chunk  "
          f"commit={stats.get('repo_commit', '-')[:10]}")
    return 0


def cmd_tools(args) -> int:
    """列出 config.yaml 声明、registry 实际加载的 agent 工具(✓ 加载成功 / ✗ 失败)。"""
    from hyperion.tools.registry import get_available_tools

    cfg = get_app_config()
    try:
        tools = get_available_tools()  # 触发反射加载,加载不了会在这抛
    except Exception as e:  # noqa: BLE001 - CLI 顶层兜底
        print(f"错误:工具加载失败: {e}", file=sys.stderr)
        return 1
    loaded = {t.name for t in tools}
    rows = [tc for tc in cfg.tools if not args.group or tc.group == args.group]
    if not rows:
        print(f"(没有 group={args.group} 的工具)" if args.group else "(config.yaml 未声明工具)")
        return 1
    for tc in rows:
        print(f"{'✓' if tc.name in loaded else '✗'} {tc.name:16} [{tc.group}]")
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

    sub_index = sub.add_parser("index", help="为一个仓库建/更新向量索引")
    sub_index.add_argument("repo_path", help="仓库根目录路径")
    sub_index.add_argument("repo_name", nargs="?", default=None,
                           help="索引名(默认取目录名;须与 code_index.repo 一致)")
    sub_index.add_argument("--force", action="store_true", help="强制全量重建")
    sub_index.set_defaults(func=cmd_index)

    sub_tools = sub.add_parser("tools", help="列出已加载的 agent 工具")
    sub_tools.add_argument("--group", default=None, help="只看某 group(如 code / file:read / sandbox)")
    sub_tools.set_defaults(func=cmd_tools)

    args = parser.parse_args(argv)
    if getattr(args, "func", None):
        return args.func(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
