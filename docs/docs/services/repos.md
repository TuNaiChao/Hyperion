# 服务 · 仓库解析器(repos resolver)

> `services/repos/resolver.py` —— 本地没有的代码仓,按 `config.patch.git` 自动 clone(幂等)。给 `ensure_repo` MCP 工具、patch_report workflow 用。

## 概览

P-A 补丁 / PR 分析常需要完整代码仓(跑 `validate_patch`、CRG 影响面)。`ensure_repo` 接受仓库名或路径:本地有就直接用,没有就按 `patch.git.remotes` 配的地址 clone 到 `clone_dir`,命中后幂等(不再重复 clone)。

## 源码

| 文件 | 职责 |
|---|---|
| `services/repos/resolver.py` | `ensure_repo` + `repo_name` |

## API

```python
def ensure_repo(name_or_path: str, *, cfg=None) -> tuple[Path, bool]
    # 返回 (本地绝对路径, 是否本次新 clone)

def repo_name(name_or_url: str) -> str
    # 从名字 / url 抽规范仓库名
```

## 流程

1. `repo_name(...)` 归一化仓库名。
2. 先看本地(`clone_dir/<name>`)有没有 → 有则返回 `(path, False)`。
3. 没有 → 查 `patch.git.remotes[name]`(自定义镜像 / SSH);查不到则按约定拼 url。
4. `git clone`(默认 `--depth 1` 浅克隆)到 `clone_dir` → 返回 `(path, True)`。
5. 幂等:已命中就不再 clone。

## 配置

```yaml
patch:
  git:
    clone_dir: data/repos     # auto-clone 落点
    shallow: true             # --depth 1(要完整历史改 false)
    remotes: {}               # {仓库名: git url} 自定义镜像 / SSH,如 {bluez: https://...}
```

## 边界与限制

- 默认浅克隆(`--depth 1`);`validate_patch` / CRG 一般够用,要完整历史改 `shallow: false`。
- 仓库地址优先用 `remotes` 里的自定义(内网镜像 / SSH);没配则按默认约定。
- clone 是同步 subprocess git(非 async)。

## 示例

```python
from hyperion.services.repos.resolver import ensure_repo

path, cloned = ensure_repo("wpa_supplicant")
print(path, "新 clone" if cloned else "已存在")
```

## See Also

- [../tools/mcp-tools.md](../tools/mcp-tools.md) — `ensure_repo`
- [../configuration.md](../configuration.md) §patch
- [../workflows/patch-report.md](../workflows/patch-report.md)
