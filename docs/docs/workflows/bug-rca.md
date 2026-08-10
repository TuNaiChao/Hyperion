# 工作流 · bug-RCA(bug_rca)

> [!WARNING]
> **这是降级参考路径。** post-pivot(2026-08)后,bug-RCA 的**主路径**是 opencode + bug-rca skill + Hyperion MCP 工具(agent 自驱、能自纠),见 [../guides/bug-rca-opencode.md](../guides/bug-rca-opencode.md)。
> 本 LangGraph 编排器(六节点固定管线)保留向后兼容,CLI `hyperion bug-rca` 仍可跑(会打印转向提示),但**新工作请走主路径**。`graph.py` 明确标注「不要把本 workflow 暴露成 MCP 工具」。

## 概览(参考路径)

老的 bug-RCA 编排器:输入 repo + trigger(和 / 或 log)→ 委托 opencode 多阶段定位 → 组装修复 prompt → 委托 opencode 修 → verify-refine 迭代(证伪式自审 + `validate_patch` 执行硬门)→ 报告 + 沉淀记忆。把读码 / 改代码的重活委托给 coding agent(`CodingAgentDelegate`),Hyperion 负责召回 + 组装上下文 + 调度 + 沉淀。

## 源码

| 文件 | 职责 |
|---|---|
| `bug_rca/state.py` | `BugRcaState(TypedDict, total=False)`(repo_root / trigger 必填;含 workspace / scope / recalled_lessons / localization_json / patch / verified / verdict_chain / repair_loops …) |
| `bug_rca/graph.py` | `build_graph()` + `async run(repo_root, trigger, log_path=None) -> dict` |
| `bug_rca/nodes.py` | 六节点函数 + 迭代 verify-refine;常量 `LOCALIZE_SCHEMA` / `REPAIR_SCHEMA`(JSON Schema) |
| `bug_rca/report.py` | `render_report(state: BugRcaState) -> str`(8 段) |
| `tools/delegate.py` | 委托层:`CodingAgentDelegate` ABC + `OpencodeDelegate`(见下) |

## 流程(六节点)

```
ingest ──▶ recall_lessons ──▶ delegate_localize_loop ──▶ assemble_repair
                                                        │
                                                        ▼
                            report_memorize ◀── delegate_repair_loop
```

1. **ingest**:解析 trigger / log。
2. **recall_lessons**:`memory_recall` 召回历史 bug 教训(确定性预注入,治"漏召回历史根因")。
3. **delegate_localize_loop**:委托 opencode 多轮自审定位(证伪式:自己质疑再重定位),封顶 `delegate.max_localize_loops`。
4. **assemble_repair**:把定位结果 + 召回的教训组装成修复 prompt。
5. **delegate_repair_loop**:委托 opencode 修 + verify-refine(`validate_patch` 执行硬门控;`delegate.max_repair_loops`)。
6. **report_memorize**:`render_report`(8 段)+ 沉淀 `bug_lesson` 入记忆。

## 委托层(delegate.py)

把 coding 活外包给 opencode / omp / claude;subprocess 跑 + 解 NDJSON 事件流 + 抠 JSON。

```python
class DelegateStatus: OK / TIMEOUT / ERROR / SCHEMA      # + is_terminal(status) 类方法
@dataclass
class DelegateResult: final_text; status; data; tokens; error; events; tool_calls; @property ok

class CodingAgentDelegate(abc.ABC):
    async def run(prompt, cwd, output_schema=None, *, timeout=None, agent=None, continue_session=False) -> DelegateResult
    @classmethod
    def from_config() -> CodingAgentDelegate

class OpencodeDelegate(CodingAgentDelegate):   # ★v1 唯一真实现
    # asyncio.create_subprocess_exec + 逐块 drain(避 readline 64KB 限制)+ 超时 killpg
    # + delegate_log 落盘 + 瞬时网络错重试(_is_transient_net_error)+ 可选 fallback_model
    # + _parse_stream 解事件流聚 final_text
```

> [!NOTE]
> **delegate gap**:`from_config` 的短名映射含 `"omp"` / `"claude"`,但这两个类**未实现**(只有 `OpencodeDelegate`)。配 `delegate.backend: omp/claude` 会 `AttributeError`。omp / claude 是占位,待本机可用时实现。

## API

```python
def build_graph() -> CompiledStateGraph
async def run(repo_root: str, trigger: str | None, log_path: str | None = None) -> dict
    # 返回 {report_path, patch_path, verified, ...}

def render_report(state: BugRcaState) -> str   # 8 段:问题描述/根因/定位定界/证据/补丁/验证/建议/附录
```

## 配置

委托参数见 [configuration.md](../configuration.md) §delegate(`backend` / `max_localize_loops` / `max_repair_loops` / `opencode.{model,timeout,retry_max,fallback_model}` …)。

## 边界与限制

- **降级参考路径**:新工作走主路径([../guides/bug-rca-opencode.md](../guides/bug-rca-opencode.md))。
- **只有 `OpencodeDelegate` 真实现**(omp / claude 占位)。
- verify 门控只查 apply(`validate_patch`),**不**查语义最优 —— `verified=True` 不保证补丁是金标落点。
- **stale doc**:`bug_rca/state.py` 等仍提及 `filter_logs` MCP 工具,但该工具已撤(2026-08-10);日志切片改由 opencode 的 grep / awk 做。以 [../tools/mcp-tools.md](../tools/mcp-tools.md) 现存 9 工具为准。
- 真调 opencode(delegate),整条较慢(数分钟)。

## See Also

- [../guides/bug-rca-opencode.md](../guides/bug-rca-opencode.md) — ★主路径(opencode + skill + MCP)
- [../cli-reference.md](../cli-reference.md) §`hyperion bug-rca`
- [../services/workspace.md](../services/workspace.md) — workspace + validate_patch
- [../services/memory.md](../services/memory.md) — recall_lessons / report_memorize
- [../configuration.md](../configuration.md) §delegate
- 上级 [../../设计/harness-v2/02-bug-rca.md](../../设计/harness-v2/02-bug-rca.md)
