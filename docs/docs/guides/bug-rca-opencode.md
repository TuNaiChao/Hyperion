# 指南 · 在 opencode 上做 bug-RCA(主路径)

> bug-RCA 的**主路径**:opencode + bug-rca skill + Hyperion MCP 工具,agent 自驱定位根因 + 改代码 + 落盘补丁;**你拿补丁到真机 / 复现环境验证**,通过才沉淀记忆 + 报告。
> 这是「工具箱 + 人在环迭代」范式。老的 `hyperion bug-rca` 编排器是降级参考路径,见 [../workflows/bug-rca.md](../workflows/bug-rca.md)。

## 前置

1. 装好 Hyperion(`uv sync --extra mcp`)、配好 `.env`(见 [../getting-started.md](../getting-started.md))。
2. 装好 opencode,配好 provider / model(走全局 `~/.config/opencode/opencode.json` 或 `opencode providers login`)。

## 1. 安装 opencode.json(关键,最易踩坑)

> [!WARNING]
> opencode 启动时从当前目录向上找 **`opencode.json`** —— 它默认不认 `config/opencode_hyperion.json` 这个模板文件名。必须把模板"安装"成**项目根的 `opencode.json`** 才会被识别(否则看不到 hyperion MCP、选不到 hyperion-bug-rca agent)。

在项目根执行(**合并模式**:保留你已有配置 + 注入 hyperion 的 mcp/agent):

```bash
python -c "
import json, os
tmpl = json.load(open('config/opencode_hyperion.json'))
cfg = json.load(open('opencode.json')) if os.path.exists('opencode.json') else {}
cfg.setdefault('mcp', {}).update(tmpl.get('mcp', {}))      # 加 hyperion MCP server
cfg.setdefault('agent', {}).update(tmpl.get('agent', {}))  # 加 hyperion-* agent
cfg['mcp']['hyperion']['environment']['HYPERION_CODEBASE'] = '<你的仓库名>'   # 例 'wpa'
json.dump(cfg, open('opencode.json','w'), ensure_ascii=False, indent=2)
print('opencode.json 已合并安装')
"
```

- **API key / model / provider** 放**全局** `~/.config/opencode/opencode.json` 或 `opencode providers login`(凭证库,最安全);**别放项目根**(明文 + git 风险)。
- **hyperion 的 mcp / agent** 放**项目根** `opencode.json`(每项目不同 `HYPERION_CODEBASE`)。
- 两者段不重叠(`provider`/`model` vs `mcp`/`agent`),opencode 按段全加载,**永不冲突**。

`HYPERION_CODEBASE` 告诉 Hyperion MCP server 查哪个仓库的索引 / 记忆(= LanceDB 表名 + memory scope),必须跟建索引时的名字一致。

验证安装:`opencode agent list` 应能看到 `hyperion-bug-rca` / `hyperion-localize` / `hyperion-repair`。

## 2. 建代码索引(一次性)

```bash
uv run hyperion index <仓库路径> <名字>     # 例:hyperion index example/demo2/wpa wpa
```

`search_codebase` / `blast_radius` 要索引;`memory_recall` / `validate_patch` / `export_patch` 不需要。

## 用法 A:交互式 TUI(推荐 —— 人在环天然)

```bash
opencode                   # 在项目根(opencode.json 所在)启动 TUI
```

TUI 里切到 `hyperion-bug-rca` agent(按 **Tab** 循环 primary,或 `/` 看斜杠命令),输入框给任务:

> 我在 wpa_supplicant 有个 bug 要定位根因 + 修复:WiFi 扫描不到(持续 ssid-not-found,重启 wpa_supplicant 才恢复)。日志 `example/demo2/journalctl_b.txt`(1.6 万行,用 grep/awk 按故障时间窗筛,别读全量)。代码仓 `example/demo2/wpa`。

agent 自驱:加载 bug-rca skill → 按需调 `memory_recall` / `search_codebase` / `blast_radius`(大日志自己用 grep/awk 切)→ 立根因 + 主动证伪 → `edit` 改代码 → `validate_patch` → `export_patch` 落盘。**TUI 实时显示它调的每个工具和结果**。

落盘补丁后你拿去真机验证,然后**在同一对话框**反馈:
- **没修对** → 说清现象 + 给方向(如"会不会是结果被并发请求覆写、误路由?重新定位")→ agent 回头改、再 validate/export。
- **修对了** → `补丁验证通过了,memorize + export_report 收尾。` → agent 沉淀记忆 + 写报告。

## 用法 B:非交互 `opencode run`(脚本 / e2e / CI)

```bash
opencode run --agent hyperion-bug-rca --model <provider/model> \
  --format json --print-logs \
  "<bug 现象 + 故障日志绝对路径 + 代码仓绝对路径>"
```

跑完取 `data/bug_rca/<repo>.patch` 去验证;验证后 `opencode run --continue ...` 续 session 反馈结果。`--format json --print-logs` 输出 JSON 事件流 + 日志到 stderr,便于脚本解析;交互调试去掉这俩。

## 产物

| 文件 | 何时有 | 路径 |
|---|---|---|
| 补丁 | 每版都落盘(覆盖,最新为准) | `data/bug_rca/<repo>.patch` |
| 报告 | **验证通过后**(agent 收尾) | `data/bug_rca/<repo>-rca.md` |
| 记忆 | **验证通过后**(`memory_memorize`) | 记忆库(kind=bug_lesson) |

## 关键约定

1. **`validate_patch` 只验补丁能 apply,不验修对。** 系统软件常无单元测试,**真机 / 复现是唯一 oracle**,每版补丁都要人验证。
2. **`memory_memorize` / `export_report` 验证通过后才做。** 没验证就 memorize = 把没坐实的根因写进记忆,污染下次同类 RCA。
3. **别盲信 agent 的根因。** 主动追问它:是否检查了**故障时刻之前**的日志?是否把**显眼的现象行**当成了根因?交互式能即时纠偏,这是它比脚本式强的地方。
4. **`opencode.json` 陷阱**:opencode 只认这个文件名;模板 `config/opencode_hyperion.json` 不安装就看不到 agent/MCP。

## 排查

| 现象 | 解法 |
|---|---|
| TUI 选不到 hyperion-bug-rca | 项目根没有 `opencode.json`(只有模板);跑第 1 步安装,`opencode agent list` 验证 |
| agent 不调 hyperion_* 工具(裸 grep) | MCP 没连上:`opencode.json` 的 `mcp.hyperion` 段要在;`hyperion mcp serve` 要能跑(`uv sync --extra mcp`);**用 local stdio 接**(http MCP 在 opencode 1.18.x 不注册原生工具);timeout ≥ 120000ms 防首次冷启 |
| 工具首次调用超时 | embedder 冷启(sentence-transformers ~1.2GB);timeout ≥ 120000ms |
| 补丁 apply 过但真机没修对 | 正常 —— apply 过 ≠ 修对(见关键约定 1);迭代改 + 再验证 |

## See Also

- [../tools/mcp-tools.md](../tools/mcp-tools.md) — 9 个 MCP 工具
- [../services/workspace.md](../services/workspace.md) — validate_patch 的 Tier 0 边界
- [../workflows/bug-rca.md](../workflows/bug-rca.md) — 降级参考路径(编排器)
- [../../../.claude/skills/bug-rca/SKILL.md](../../../.claude/skills/bug-rca/SKILL.md) — 给模型的 skill 指令
- `config/opencode_hyperion.json` — opencode 接线模板
