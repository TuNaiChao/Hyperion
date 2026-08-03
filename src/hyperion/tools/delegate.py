"""委托层 · CodingAgentDelegate(R2,★P2 MVP)。

这一层干什么(面向小白)
------------------------
Hyperion 是"接案调度员",它把 bug-RCA 里"读代码 / 写补丁 / 给根因"的力气活
**委托**给成熟 coding agent(opencode / omp),自己不重造 coding agent。
本文件就是这个委托的抽象接口 + opencode 实现 + 回执结构。

为什么不自己干:读码写补丁是成熟 coding agent 最擅长的通用能力,自建会烧光预算。
Hyperion 的差异化在记忆 + 调度,委托 = 把通用能力外包(三锁定决策 #2)。

四个角色
--------
- **DelegateStatus / DelegateResult**:回执。借 deer-flow SubagentResult 的形状 ——
  最终文本 + 终态 + token 用量 + 错误 + 原始事件流(供可观测回放)。
- **CodingAgentDelegate**(抽象接口):run(prompt, cwd, output_schema) -> DelegateResult。
  后端可换(opencode 默认 / omp / claude),从 config 加载(三锁定决策 #2:配置可换)。
- **OpencodeDelegate**(★v1 默认):subprocess 跑 `opencode run --format json --auto`,
  解析 NDJSON 事件流,聚出最终 assistant 文本,再抠 JSON。
- **OmpDelegate / ClaudeDelegate**:占位,待本机可用时实现。

结构化产出怎么稳(复用 R1 已验方案)
------------------------------------
opencode `--format json` 给的是**事件流**,不是"符合 schema 的对象";最终 schema 对象
从 assistant 文本抠。走 R1 memory/extract.py 那套「喂 JSON Schema + 模型直出 JSON +
正则 raw_decode 抠 {...}」—— 已验证对 DeepSeek 思考模式可用(见记忆 deepseek-structured-output-gotcha)。
不用 with_structured_output(思考模式连踩两坑)。

事件流结构(实测 docs/调研/r2-bug-rca-research.md §9):
  {"type":"step_start",  "part":{"type":"step-start", ...}}
  {"type":"text",        "part":{"type":"text", "text":"...", "messageID":"msg_..."}}
  {"type":"step_finish", "part":{"type":"step-finish", "reason":"stop", "tokens":{...}}}
委托接口设计:docs/设计/bug-rca-design.md §3-§4。
"""

from __future__ import annotations

import abc
import asyncio
import json
import os
import re
import signal
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from hyperion.platform.config import get_app_config
from hyperion.platform.reflection import resolve_class

# Hyperion 根:解析 config 相对路径(config/opencode_hyperion.json),给 env OPENCODE_CONFIG 用。
# delegate.py 在 src/hyperion/tools/,parents[3] = Hyperion 根。
_HYPERION_ROOT = Path(__file__).resolve().parents[3]

# ──────────────────────────────────────────────────────────────────────────
# §1 回执数据结构(借 deer-flow SubagentResult 形状)
# ──────────────────────────────────────────────────────────────────────────


class DelegateStatus:
    """委托终态(借 deer-flow SubagentStatus 思想:字符串枚举 + is_terminal)。

    为什么用字符串常量类而非 Enum:跟 deer-flow 的 additive stop_reason 风格一致,
    序列化/日志好读;以后加新状态后追加不影响旧的。
    """

    OK = "ok"  # 正常完成,拿到结果
    TIMEOUT = "timeout"  # 超时
    ERROR = "error"  # 子进程错误 / 非零退出
    SCHEMA = "schema"  # 跑完了但结果抠不出符合 schema 的 JSON

    @classmethod
    def is_terminal(cls, status: str) -> bool:
        return status in (cls.OK, cls.TIMEOUT, cls.ERROR, cls.SCHEMA)


@dataclass
class DelegateResult:
    """委托回执(给 workflow 用)。

    final_text:delegate 最终的 assistant 文本(可能含 JSON,也可能纯 prose)。
    data:从 final_text 抠出的结构化对象(output_schema 非空时;抠失败为 None)。
    status:终态(见 DelegateStatus)。
    tokens:token 用量(input/output/total/reasoning,从事件流 step_finish 取)。
    error:失败时的错误信息。
    events:原始事件流(供 observability 回放;R2 保留全量,R5 可过滤)。
    """

    final_text: str
    status: str = DelegateStatus.OK
    data: Any = None
    tokens: dict[str, int] = field(default_factory=dict)
    error: str | None = None
    events: list[dict] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)  # opencode 调过的工具名(含 hyperion_* MCP;observability)

    @property
    def ok(self) -> bool:
        """workflow 用:是否成功拿到结果。"""
        return self.status == DelegateStatus.OK


# ──────────────────────────────────────────────────────────────────────────
# §2 抽象接口 + 工厂
# ──────────────────────────────────────────────────────────────────────────


class CodingAgentDelegate(abc.ABC):
    """委托后端抽象。后端可换(opencode 默认 / omp / claude),从 config 加载。

    为什么抽象从第一天起(三锁定决策 #2):即使 v1 只实现 opencode,接口先定死,
    以后加 omp/claude 零改 workflow —— 工作流只认这个接口,不认具体后端。
    """

    @abc.abstractmethod
    async def run(
        self,
        prompt: str,
        cwd: Path | str,
        output_schema: dict | None = None,
        *,
        timeout: float | None = None,
        agent: str | None = None,  # 指定 delegate agent(hyperion-localize/repair)
        continue_session: bool = False,  # 多阶段同会话续接(A:--continue 续最近 session)
    ) -> DelegateResult:
        """跑一次委托。

        prompt:        递给 delegate 的指令(含任务描述 + 产出契约)。
        cwd:           delegate 工作的仓库目录(opencode 用 --dir 指定)。
        output_schema: 期望 delegate 返回的 JSON Schema;非空时从 final_text 抠 JSON。
                       None = 只要 final_text,不抠 JSON。
        timeout:       总超时秒;None 用 config 里后端的默认值。
        """
        ...

    @classmethod
    def from_config(cls) -> CodingAgentDelegate:
        """从 config.yaml 的 delegate.backend 反射加载后端实例。

        backend 可以是:
          - 短名 opencode / omp / claude(映射到本文件的对应类)
          - 'pkg.mod:Cls' 全限定(走反射,自定义后端)
        """
        backend = get_app_config().delegate.backend
        short = {
            "opencode": "hyperion.tools.delegate:OpencodeDelegate",
            "omp": "hyperion.tools.delegate:OmpDelegate",
            "claude": "hyperion.tools.delegate:ClaudeDelegate",
        }
        dotted = short.get(backend, backend)  # 短名展开;非短名当作全限定
        klass = resolve_class(dotted, CodingAgentDelegate)  # base 校验:必须是子类
        return klass()


# ──────────────────────────────────────────────────────────────────────────
# §3 OpencodeDelegate(★v1 默认,本机已装 v1.18.3)
# ──────────────────────────────────────────────────────────────────────────


class OpencodeDelegate(CodingAgentDelegate):
    """opencode 后端。

    流程:流式跑 `opencode run --format json --auto --dir <cwd> "<prompt>"`(R3.0 #56:
    asyncio.create_subprocess_exec + 逐行 drain stdout/stderr)→ 逐行 json.loads
    → 聚 type=="text" 的 part.text(按 messageID 分组保序)→ 最后一条消息的拼接
    = final_text → 抠 JSON。超时 kill 进程组 + 存已收 stdout;delegate_log 落盘。
    """

    async def run(self, prompt, cwd, output_schema=None, *, timeout=None, agent=None, continue_session=False):
        """跑一次委托(带瞬时网络错重试 + 可选 fallback 模型;A,2026-08)。

        glm-5.2 API 偶发 `connection reset by peer`(对长/大请求不稳;repair --continue 全量重发
        ~60K 历史 + 无 prompt cache → 高暴露)。delegate 层自动重试:① 主模型(cfg.model)试 retry_max
        次;② 仍瞬时错且配了 fallback_model → 换它(如 deepseek-v4-flash,非推理快、reset 概率低)再试
        retry_max 次。重试都 --continue 续同 session(opencode 已做的改动在磁盘上,接着来)。非瞬时错
        (SCHEMA / 真 ERROR / OK)不重试直接返。
        """
        cfg = get_app_config().delegate.opencode
        retry_max = max(1, getattr(cfg, "retry_max", 2) or 1)
        fallback = getattr(cfg, "fallback_model", None)
        # 重试序列:主模型 N 次,再 fallback N 次(fallback 更稳/快,治 glm 长/大请求 reset)
        model_seq: list[str | None] = [cfg.model] * retry_max
        if fallback and fallback != cfg.model:
            model_seq += [fallback] * retry_max
        last_result: DelegateResult | None = None
        for attempt, model in enumerate(model_seq):
            # 续 session:调用方要续 OR 重试(第 2 次起都续,接 opencode 已做的活;新 session 仅 attempt0)
            cont = bool(continue_session) or attempt > 0
            last_result = await self._run_once(
                prompt, cwd, output_schema, cfg, timeout, agent, cont, model_override=model)
            # 只对"瞬时网络错"重试;其他(SCHEMA / 真 ERROR / OK)直接返
            if not (last_result.status == DelegateStatus.ERROR and _is_transient_net_error(last_result)):
                return last_result
        return last_result

    async def _run_once(self, prompt, cwd, output_schema, cfg, timeout, agent, continue_session, *,
                        model_override: str | None = None) -> DelegateResult:
        """单次跑 opencode(run() 的重试单元)。R3.0 #56 可观测:流式读 + 超时 kill 进程组 + delegate_log。"""
        cmd = self._build_cmd(cfg, prompt, cwd, agent, continue_session, model_override=model_override)
        timeout = timeout or cfg.timeout

        # env OPENCODE_CONFIG:注入 Hyperion 自带 opencode 配置(agent+steps+permission+mcp,config/opencode_hyperion.json),
        # 与用户全局 opencode.json(provider/key)合并(opencode 配置 8 层合并,路 1 调研)。
        env = dict(os.environ)
        if cfg.config:
            _cp = Path(cfg.config)
            _cp = _cp if _cp.is_absolute() else _HYPERION_ROOT / _cp
            env["OPENCODE_CONFIG"] = str(_cp)
        # HYPERION_CODEBASE:告诉 `hyperion mcp serve`(opencode 经 MCP 拉起的子进程)查哪个代码库的
        # 索引/记忆(= 建索引时的 name)。opencode 把父进程 env 透传给 MCP 子进程(local server 的
        # environment 字段不展开 {env:},靠进程 env 继承 —— r3.1 research 确认)。从 workspace 目录名
        # 推导(<repo>__<bugid>/code 的父名前半);非 workspace(无 __)→ 不设,MCP server 自身
        # _resolve_codebase 回落 config.code_index.repo / cwd(见 mcp_memory.py)。
        _parent = Path(cwd).parent.name
        if "__" in _parent:
            env["HYPERION_CODEBASE"] = _parent.split("__", 1)[0]
        env["PYTHONUNBUFFERED"] = "1"  # 防 MCP server stdio stdout 块缓冲致 tools/list 握手挂(r3.1 research: pipe buffering)

        # 流式跑:start_new_session=True 让 opencode 独立成进程组(超时可 killpg 整组,防子进程残留)
        proc = await asyncio.create_subprocess_exec(
            *cmd,  # noqa: S603 —— 跑 config 配的 opencode 二进制,非 shell
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd),
            env=env,
            start_new_session=True,
        )

        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []

        async def _drain(stream, sink: list[bytes]) -> None:
            """读原始字节块(不受 readline 64KB 行长限制);并发 drain stdout+stderr 防 stderr 管道写满阻塞。

            为什么不用 `async for line in stream`(readline):opencode --format json 的单个事件可能很大
            (read 大文件/大日志的工具结果 >64KB),readline 抛 "Separator is not found, and chunk exceed
            the limit"(e2e 实测踩到:opencode 读 2.6MB 日志)。改 read(n) 块读 + 之后 _parse_stream
            统一 splitlines,行长无上限。
            """
            if stream is None:
                return
            while True:
                chunk = await stream.read(65536)
                if not chunk:
                    break
                sink.append(chunk)

        try:
            await asyncio.wait_for(
                asyncio.gather(_drain(proc.stdout, stdout_chunks), _drain(proc.stderr, stderr_chunks)),
                timeout=timeout,
            )
        except TimeoutError:
            # 超时:kill 进程组 + 存已收 stdout(R2 块缓冲 timeout 会丢 stdout,看不到跑到哪)
            self._kill_proc_group(proc)
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)  # 收尸,防僵尸
            except TimeoutError:
                pass
            partial = b"".join(stdout_chunks).decode("utf-8", "replace")
            stderr_tail = b"".join(stderr_chunks).decode("utf-8", "replace")[-2000:]
            self._write_delegate_log(cwd, partial, stderr_tail, status="timeout")
            return DelegateResult(
                final_text="",
                status=DelegateStatus.TIMEOUT,
                error=f"opencode 超时({timeout}s);已收 {len(partial)} 字节 stdout(见 delegate_log)",
            )

        await proc.wait()  # 正常收尸
        stdout = b"".join(stdout_chunks).decode("utf-8", "replace")
        stderr = b"".join(stderr_chunks).decode("utf-8", "replace")

        if proc.returncode != 0:
            self._write_delegate_log(cwd, stdout, stderr[-2000:], status="error")
            return DelegateResult(
                final_text="",
                status=DelegateStatus.ERROR,
                error=f"opencode 退出码 {proc.returncode};stderr 尾: {stderr[-2000:]}",
            )

        final_text, tokens, events, all_text = self._parse_stream(stdout)
        # 审计 opencode 调了哪些工具(§6.2;含 hyperion_* MCP 工具 → 验证「工具驱动委托」真生效)。
        # 事件 shape 以 opencode 实测为准(tool_use part.tool / part.name);defensive 取值,缺字段则空。
        tool_calls: list[str] = []
        for _e in events:
            if _e.get("type") not in ("tool_use", "tool"):
                continue
            _part = _e.get("part") or {}
            _tool = _part.get("tool") or _part.get("name")
            if _tool:
                tool_calls.append(str(_tool))
        result = DelegateResult(final_text=final_text, tokens=tokens, events=events, tool_calls=tool_calls)
        self._write_delegate_log(
            cwd,
            stdout,
            stderr[-2000:] if stderr else "",
            status="ok",
            final_text=final_text,
            all_text=all_text,
        )

        if output_schema is not None:
            data = _extract_json(all_text)  # ← 从所有 message 找 JSON(不只最后一条)
            if data is None:
                result.status = DelegateStatus.SCHEMA
                result.error = "delegate 文本抠不出 JSON 对象"
            else:
                result.data = data
        return result

    # ── R3.0 #56 可观测辅助 ──────────────────────────────────────────
    @staticmethod
    def _kill_proc_group(proc) -> None:
        """kill 整个 opencode 进程组(start_new_session 下 proc.pid 是 group leader),兜底单杀。

        为什么 killpg 不 kill:opencode 可能 spawn 子进程(grep/tool),单杀 opencode 留孤儿;
        killpg 整组连子进程一起清(复用 platform/sandbox/local.py:_kill_process_group 同款)。
        """
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except ProcessLookupError:
                pass

    @staticmethod
    def _delegate_log_dir(cwd) -> Path:
        """delegate_log 落盘目录:优先 <workspace>/delegate/delegate_log;否则 data/runtime/delegate_log/。

        workspace 场景(bug-RCA):cwd = <workspace>/code,delegate/ 在 workspace 根(= cwd 的父)→
        日志跟 bug 一起归档到 <workspace>/delegate/delegate_log/。非 workspace(cwd=repo_root 之类,
        其父无 delegate/):落到 data/runtime/delegate_log/(gitignore)。
        """
        ws_log = Path(cwd).parent / "delegate" / "delegate_log"
        if ws_log.parent.exists():  # workspace 根有 delegate/ 目录才往那写
            return ws_log
        return _HYPERION_ROOT / "data" / "runtime" / "delegate_log"

    @staticmethod
    def _write_delegate_log(
        cwd,
        stdout: str,
        stderr_tail: str,
        *,
        status: str,
        final_text: str = "",
        all_text: str = "",
    ) -> None:
        """把本次 delegate 的 stdout(全量)+ 摘要落盘(可观测回放;替 /tmp/delegate_debug.txt)。

        两个文件:<ts>-<status>.stdout.log(原始 NDJSON 流,供 _parse_stream 复盘)+
                  <ts>-<status>.summary.md(摘要:stderr 尾 + final_text + all_text 头)。
        """
        try:
            log_dir = OpencodeDelegate._delegate_log_dir(cwd)
            log_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            (log_dir / f"{ts}-{status}.stdout.log").write_text(stdout, encoding="utf-8")
            (log_dir / f"{ts}-{status}.summary.md").write_text(
                f"# delegate log {ts} [{status}]\n\n- stdout: {len(stdout)} 字节\n"
                f"- stderr 尾:\n```\n{stderr_tail}\n```\n\n"
                f"## final_text ({len(final_text)} chars)\n{final_text[:6000]}\n\n"
                f"## all_text ({len(all_text)} chars, head 8000)\n{all_text[:8000]}\n",
                encoding="utf-8",
            )
        except OSError:
            pass

    @staticmethod
    def _build_cmd(cfg, prompt: str, cwd, agent=None, continue_session=False,
                   model_override: str | None = None) -> list[str]:
        """组装 opencode run 命令(--continue 续会话 + --agent 指定 agent + -m 指定模型)。flags 实测自 --help。

        model_override:重试/fallback 时换模型(如 glm-5.2 → deepseek-v4-flash);None 用 cfg.model。
        """
        cmd = [cfg.bin, "run", "--format", cfg.format, "--auto", "--dir", str(cwd)]
        if continue_session:
            cmd += ["--continue"]  # 续同 cwd 最近 session(多阶段同会话,避重复探索)
        use_model = model_override or cfg.model  # 重试/fallback 覆盖 > config 默认
        if use_model:
            cmd += ["-m", use_model]
        use_agent = agent or cfg.agent  # 调用方传 > config 默认
        if use_agent:
            cmd += ["--agent", use_agent]
        if cfg.variant:
            cmd += ["--variant", cfg.variant]
        cmd.append(prompt)
        return cmd

    @staticmethod
    def _parse_stream(stdout: str) -> tuple[str, dict, list[dict], str]:
        """解析 NDJSON 事件流 → (final_text, tokens, events, all_text)。

        final_text:最后一条 messageID 的拼接(delegate 的「最终回复」,给报告/observability)。
        all_text:  所有 messageID 拼接 —— 给 _extract_json 找 JSON 用。
                   (glm-5.2 --format json 多轮时,JSON 可能在中间消息,不只最后一条。)
        """
        messages: dict[str, list[str]] = {}  # messageID -> [text parts] 保序
        msg_order: list[str] = []  # messageID 首次出现顺序
        tokens: dict[str, int] = {}
        events: list[dict] = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue  # 跳过非 JSON 行(opencode 偶有诊断输出)
            events.append(evt)
            part = evt.get("part") or {}
            if evt.get("type") == "text":
                mid = part.get("messageID", "_default")
                if mid not in messages:
                    messages[mid] = []
                    msg_order.append(mid)
                if part.get("text"):
                    messages[mid].append(part["text"])
            elif evt.get("type") == "step_finish":
                tk = part.get("tokens")
                if isinstance(tk, dict):
                    # 取累计用量(最后一个 step_finish 是最新累计快照);
                    # 只留 str 键 + int 值(total/input/output/reasoning;cache 是 dict 被过滤)
                    tokens = {k: v for k, v in tk.items() if isinstance(k, str) and isinstance(v, int)}
        final_text = "".join(messages[msg_order[-1]]) if msg_order else ""
        # all_text:所有 message 按出现顺序拼接,_extract_json 从这里找 JSON
        all_text = "\n".join("".join(messages[mid]) for mid in msg_order)
        return final_text, tokens, events, all_text


# ──────────────────────────────────────────────────────────────────────────
# §4 结构化产出:从文本抠 JSON(复用 R1 extract.py 思路)
# ──────────────────────────────────────────────────────────────────────────


def _try_parse_json(s: str) -> dict | None:
    """从 s 开头 raw_decode 一个 JSON 对象;非 dict 或失败返回 None。"""
    try:
        obj, _ = json.JSONDecoder().raw_decode(s)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _extract_json(text: str) -> dict | None:
    """从文本抠最外层 {...} 对象(鲁棒版)。

    JSON 可能在 fenced ```json ``` 块里,或藏在中间消息;扫所有候选,取能 parse 且 keys
    最多的 dict(= 最完整的 schema 对象)。解「--format json 多轮下 JSON 不在最后消息」的 schema 问题。
    """
    # 1. 优先 fenced ```json {...} ```(可能有多个,逐个试,命中即返回)
    for m in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL):
        obj = _try_parse_json(m.group(1))
        if isinstance(obj, dict):
            return obj
    # 2. fallback:扫每个 '{' 位置 raw_decode,取 keys 最多的 dict
    best: dict | None = None
    for i in range(len(text)):
        if text[i] == "{":
            obj = _try_parse_json(text[i:])
            if isinstance(obj, dict) and (best is None or len(obj) > len(best)):
                best = obj
    return best


def _is_transient_net_error(result: DelegateResult) -> bool:
    """delegate 回执是否"瞬时网络错"(值得重试,治 glm API connection reset)。

    opencode 调 LLM API 偶发 connection reset / EOF / timeout(glm 端对长/大请求不稳);这些是瞬时的,
    重试(可能换 fallback 模型)通常能过。扫 result.error + events 里的 error 事件文本匹配关键字。
    非瞬时错(opencode 退出码非 0 但无网络关键字、SCHEMA、OK)不重试。
    """
    if result.status != DelegateStatus.ERROR:
        return False
    blob = result.error or ""
    for e in result.events:
        if isinstance(e, dict) and e.get("type") == "error":
            msg = ((e.get("error") or {}).get("data") or {}).get("message", "")
            blob = f"{blob}\n{msg}"
    haystack = blob.lower()
    return any(s in haystack for s in (
        "connection reset", "broken pipe", "eof",  # TCP 层瞬时
        "timeout", "timed out", "deadline exceeded",
        "transport", "socket hang up", "fetch failed", "network",
        "502", "503", "504", "service unavailable",  # 网关/服务端瞬时
    ))


# ──────────────────────────────────────────────────────────────────────────
# §5 备选后端(占位,待本机可用时实现)
# ──────────────────────────────────────────────────────────────────────────


class OmpDelegate(CodingAgentDelegate):
    """omp 后端(占位 —— 本机暂未装:github 墙 + bun)。

    待本机可用时实现:`omp --mode rpc`(NDJSON 流,推荐)或 `omp -p`(纯文本兜底)。
    omp 的 strict schema 强校验是它最大价值(见 r2-bug-rca-research.md §3)。
    实现时复用 OpencodeDelegate 的 _extract_json + DelegateResult。
    """

    async def run(self, prompt, cwd, output_schema=None, *, timeout=None):
        raise NotImplementedError("OmpDelegate 未实现(本机 omp 未装)。config delegate.backend 改 opencode,或待 omp 装好后实现。")


class ClaudeDelegate(CodingAgentDelegate):
    """claude code 后端(占位 —— 需另装 claude CLI)。可选高档后端(R4+)。"""

    async def run(self, prompt, cwd, output_schema=None, *, timeout=None):
        raise NotImplementedError("ClaudeDelegate 未实现(本机 claude CLI 未装)。")
