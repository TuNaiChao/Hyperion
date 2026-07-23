"""env_policy —— 把平台凭据从沙箱子进程的环境里刮掉。

威胁模型(为什么需要它):
  父进程(我们的 agent)持有 API key(从 .env 读进 os.environ,如 OPENAI_API_KEY)。
  如果 bash 工具直接把 os.environ 透传给子进程,agent 生成的命令就能 `env` 或
  `$OPENAI_API_KEY` 把密钥读出来、甚至外发。所以每次起子进程前,先在这里把
  "长得像密钥的"环境变量剔除,只把干净的环境传进去。

对应 deer-flow 的 build_sandbox_env(deer-flow/backend/.../sandbox/env_policy.py)。
"""

from __future__ import annotations

import fnmatch
import os

# "看起来像密钥"的变量名模式 —— 即使值其实不敏感也一并刮掉(宁错杀)。
# 大小写不敏感:下面统一把变量名转大写后再用 fnmatch 比较。
_SECRET_NAME_PATTERNS = (
    "*KEY*",       # OPENAI_API_KEY / DEEPSEEK_API_KEY / ANTHROPIC_API_KEY ...
    "*SECRET*",
    "*TOKEN*",
    "*PASS*",
    "*PASSWORD*",
    "*CREDENTIAL*",
    "*DSN*",
)

# 这些是基础设施连接串,绝不该进沙箱:按精确名匹配。
_BLOCKED_EXACT_NAMES = frozenset(
    {
        "DATABASE_URL",
        "REDIS_URL",
        "GH_PAT",
        "MYSQL_PWD",
        "REDISCLI_AUTH",
    }
)


def is_blocked_env_name(name: str) -> bool:
    """判断某个环境变量名是否禁止进入沙箱。"""
    if name in _BLOCKED_EXACT_NAMES: # 基础设施精确名,直接拉黑
        return True
    upper = name.upper() # 统一大写,做到大小写不敏感(OpenAI_Api_Key 也刮掉)
    return any(fnmatch.fnmatchcase(upper, pat) for pat in _SECRET_NAME_PATTERNS)


# injected 覆盖优先:留给将来"agent 故意要给子进程某个密钥"的合法场景(现在用不到,但接口预留)。
def build_sandbox_env(injected: dict[str, str] | None = None) -> dict[str, str]:
    """构造一份"干净"的子进程环境:宿主 env 去掉密钥项。

    Args:
        injected: 调用方显式要注入的变量(请求级密钥等)。它们会覆盖同名宿主变量,
                  即"显式注入优先于被刮除的宿主值"。
    """
    env = {k: v for k, v in os.environ.items() if not is_blocked_env_name(k)}
    if injected:
        env.update(injected)
    return env
     