# 服务 · 工作目录与补丁验证(workspace)

> `services/workspace/` —— 给 bug-RCA 每 bug 一个专用工作目录 + 补丁能否干净 apply 的验证(Tier 0)。
> post-pivot 下 workspace 主要由 opencode 直接驱动;Hyperion 提供 `validate_patch` 硬门。

## 概览

一个 bug 一个隔离目录,避免不同 bug 的代码改动互相污染:`create_workspace` 复制全量代码 + 建干净 git base commit,产出七段结构。`validate_patch` 是补丁验证的执行硬门(零 LLM,纯 git):测正反向 apply,返回能否干净贴上。

## 源码

| 文件 | 职责 |
|---|---|
| `workspace/manager.py` | `create_workspace` —— 建七段工作目录 |
| `workspace/validate.py` | `validate_patch` —— Tier 0 补丁验证(纯 git) |

## API

### create_workspace

```python
WORKSPACE_ROOT = Path("data/workspaces")

def create_workspace(repo_root, trigger, *, bug_id=None) -> Path
```

产出 `<repo>__<bugid>/`,七段:

```
<repo>__<bugid>/
├── code/            全量代码副本(干净 git base commit:git add -A && git commit)
├── triggers/        issue.md(trigger 文本)
├── delegate/        委托 agent 的日志 / 产物
├── patch/           补丁
├── report/          RCA 报告
└── AGENTS.md        强制 opencode 自读 / 自改 / 只返 JSON 不贴 diff
```

`.gitignore` 排除 opencode 产物,保持工作目录干净。

### validate_patch

```python
def validate_patch(patch: str, forward_dir, *, reverse_dir=None, timeout=60.0) -> dict
```

返回:

```python
{
  "verified": bool,                  # 正向能否干净 apply
  "forward_method": "strict"|"3way"|"patch"|"empty",  # 实际用哪种方式贴上
  "revert_ok": bool,                 # 反向能否干净回退(reverse_dir 提供时)
  "log": str,
}
```

正向 apply 的降级链路(逐级尝试):

```
git apply --check(strict)  ──失败──▶  git apply --3way  ──失败──▶  patch -p1  ──▶ empty
```

> [!NOTE]
> `validate_patch` 对 agent 传参做了归一化(末尾换行 normalize),避免 agent `rstrip` 掉末尾换行导致 `git apply` 误判"补丁损坏"。

## 流程(validate_patch)

1. 把 patch 写临时文件。
2. 正向:`git apply --check`(strict)→ 不过试 `--3way` → 再不过试 `patch -p1`。
3. 记录 `forward_method`;`verified = True` 当任一方式成功。
4. 若给 `reverse_dir`,反向再测一次回退 → `revert_ok`。

## 配置

workspace 落点 `data/workspaces/`(常量 `WORKSPACE_ROOT`,可由调用方覆盖)。validate 无配置。

> [!WARNING]
> `build_check` 试编译门已于 **2026-08-10 撤销**(构建信号歧义 + opencode 自己能 make)。补丁验证**只到 apply(Tier 0)**。编译 / 测试 / 复现永不做 —— 用户真机自验。

## 边界与限制

- **只验 apply,不验语义**:`verified=True` 指补丁能干净贴上,**不**保证补丁修对了 bug。语义正确性靠读码推理 + 用户真机。
- validate 是纯 git / patch 命令,零 LLM,确定性。
- `patch -p1` 是最后兜底(非 git 原生),需系统装 `patch`。

## 示例

```python
from hyperion.services.workspace.validate import validate_patch

patch = open("fix.patch").read()
res = validate_patch(patch, forward_dir="data/workspaces/wpa__b1/code")
print(res["verified"], res["forward_method"])   # True strict
```

## See Also

- [../tools/mcp-tools.md](../tools/mcp-tools.md) — `validate_patch` / `export_patch`
- [../guides/bug-rca-opencode.md](../guides/bug-rca-opencode.md) — workspace 在主路径里的用法
- [../workflows/bug-rca.md](../workflows/bug-rca.md) — orchestrator 怎么用 workspace
- 上级 [../设计/workspace-design.md](../../设计/workspace-design.md)
