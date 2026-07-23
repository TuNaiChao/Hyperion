"""Hyperion CLI entry point (`uv run hyperion ...`)."""

from __future__ import annotations

import argparse
import sys

from hyprion.platform.config import get_app_config


def cmd_models() -> int:
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hyperion", description="Hyperion agent")
    sub = parser.add_subparsers(dest="cmd")

    sub_models = sub.add_parser("models", help="列出 config.yaml 中配置的模型")
    sub_models.set_defaults(func=cmd_models)

    args = parser.parse_args(argv)
    if getattr(args, "func", None):
        return args.func()
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
