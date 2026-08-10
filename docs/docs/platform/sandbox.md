# 平台 · 沙箱

> `platform/sandbox/` —— agent 文件 / 命令操作的统一界面。v1 是宿主机本地沙箱(P0);Docker 接口已留(换 provider 只改 `use`)。
> 含一个内置 grep 引擎(给 `search_codebase` / agent 工具用)。

## 概览

无论后端是本地还是 Docker,对外都是 `Sandbox` 抽象类的一组方法(`execute_command` / `read_file` / `write_file` / `list_dir` / `glob` / `grep`)。本地实现 `LocalSandbox` 负责异步抽干子进程管道(防 OOM / 死锁)、输出截断、env 刮密钥。配套的 grep 引擎带二进制 / 大文件 / ReDoS / symlink 逃逸守卫。

## 源码

| 文件 | 职责 |
|---|---|
| `sandbox/base.py` | `Sandbox` ABC + `PathMapping` dataclass;抽象方法 `execute_command` / `read_file` / `write_file` / `list_dir` / `glob` / `grep` |
| `sandbox/local.py` | `LocalSandbox(Sandbox)` —— 宿主机实现 + `_BoundedCapture`(异步抽干管道) |
| `sandbox/provider.py` | `SandboxProvider` ABC + `LocalSandboxProvider` + `get_sandbox_provider(config=None)` 进程级单例 + `reset_sandbox_provider()` |
| `sandbox/env_policy.py` | `build_sandbox_env(injected=None)` —— 刮掉密钥(`is_blocked_env_name`) |
| `sandbox/_search.py` | grep 引擎:`GrepMatch` / `GrepResult` + `find_grep_matches(...)` + 守卫(`is_probably_binary` / `should_ignore_name` / `truncate_line`) |

## API

### Provider(入口)

```python
def get_sandbox_provider(config: AppConfig | None = None) -> SandboxProvider
def reset_sandbox_provider() -> None
```

### Sandbox 抽象

```python
class Sandbox(abc.ABC):
    async def execute_command(self, cmd: list[str], **kw) -> ...: ...
    async def read_file(self, path, **kw) -> str: ...
    async def write_file(self, path, content, **kw) -> None: ...
    async def list_dir(self, path, **kw) -> list[str]: ...
    async def glob(self, pattern, **kw) -> list[str]: ...
    async def grep(self, pattern, **kw) -> ...: ...
```

### grep 引擎

```python
def find_grep_matches(
    root,
    pattern: str,
    *,
    glob: str | None = None,
    literal: bool = False,
    case_sensitive: bool = False,
    max_results: int = 100,
    max_file_size: int = ...,
) -> GrepResult
```

- `literal=True` 走字符串搜索(规避正则 ReDoS);`False` 走正则。
- 返回 `GrepResult`(含 `GrepMatch` 列表 + 是否触顶标记)。

### env 刮密钥

```python
def build_sandbox_env(injected: dict | None = None) -> dict[str, str]
def is_blocked_env_name(name: str) -> bool
```

## 流程(grep)

1. 遍历 `root`,跳过内建 ignore 黑名单 + `.git` 等。
2. `is_probably_binary` / 大文件(`max_file_size`)跳过。
3. `literal` 决定字符串 / 正则匹配;symlink 逃逸守卫防跟出 root。
4. 命中行 `truncate_line` 截断,累计到 `max_results` 触顶返回。

## 配置

```yaml
sandbox:
  use: hyperion.platform.sandbox.provider:LocalSandboxProvider   # 换 Docker 只改 use
  workspace: data/sandbox/workspace
  bash_command_timeout: 600
  bash_output_max_chars: 20000
  read_file_output_max_chars: 50000
  ls_output_max_chars: 20000
```

## 边界与限制

- **Docker 后端未实现**:接口已留(`Sandbox` ABC),R5 才接;当前 `use` 只支持 `LocalSandboxProvider`。
- grep 的正则模式有 ReDoS 守卫,但仍建议优先用 `literal=True`(agent 工具搜符号常这么做)。
- `build_sandbox_env` 会刮掉带 `KEY` / `SECRET` / `TOKEN` 等字样的环境变量,避免密钥泄漏进子进程;需要注入的显式传 `injected`。

## See Also

- [configuration.md](../configuration.md) §sandbox
- [runtime.md](runtime.md) — 工具输出外化(`ToolOutputBudget`)配合沙箱的输出截断
- [../services/code-index.md](../services/code-index.md) — `search_codebase` 复用 grep 引擎
