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
        """跑一次委托。agent=指定 opencode agent(hyperion-localize/repair);continue_session=True 续同 cwd 最近 session。

        R3.0 #56 可观测增强:① 流式逐行读 stdout/stderr(弃 R2 的块缓冲盲等,实时可见);
        ② 超时 kill 整个进程组 + 存已收 stdout(R2 timeout 会丢 stdout,无法诊断跑到哪);
        ③ 正式 delegate_log 落盘(替 /tmp/delegate_debug.txt)。
        """
        cfg = get_app_config().delegate.opencode
        cmd = self._build_cmd(cfg, prompt, cwd, agent, continue_session)
        timeout = timeout or cfg.timeout

        # env OPENCODE_CONFIG:注入 Hyperion 自带 opencode 配置(agent+steps+permission,config/opencode_hyperion.json),
        # 与用户全局 opencode.json(provider/key)合并(opencode 配置 8 层合并,路 1 调研)。
        env = dict(os.environ)
        if cfg.config:
            _cp = Path(cfg.config)
            _cp = _cp if _cp.is_absolute() else _HYPERION_ROOT / _cp
            env["OPENCODE_CONFIG"] = str(_cp)

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
            """逐行流式读(弃块缓冲);并发 drain stdout+stderr 防 stderr 管道写满阻塞 opencode。"""
            if stream is None:
                return
            async for line in stream:
                sink.append(line)

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
        result = DelegateResult(final_text=final_text, tokens=tokens, events=events)
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
    def _build_cmd(cfg, prompt: str, cwd, agent=None, continue_session=False) -> list[str]:
        """组装 opencode run 命令(--continue 续会话 A + --agent 指定 agent C)。flags 实测自 --help。"""
        cmd = [cfg.bin, "run", "--format", cfg.format, "--auto", "--dir", str(cwd)]
        if continue_session:
            cmd += ["--continue"]  # A: 续同 cwd 最近 session(多阶段同会话,避重复探索)
        if cfg.model:
            cmd += ["-m", cfg.model]
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
