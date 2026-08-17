---
name: when-introduced-handoff
description: "2026-08-17 🟡#7 落地:第 16 个 MCP 工具 when_introduced(SZZ 式引入 commit 候选)+ 真仓探针全绿"
metadata: 
  node_type: memory
  type: project
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-08-17T01:33:41.692Z
---

# when_introduced 交接(2026-08-17,🟡 backlog #7)

P1/P2 backlog 🟡#7:「这个 bug 是哪个 commit 引入的」——SZZ 出候选 + agent 语义裁决(业界路线,SZZ-Agent 2026)。纯 git、零 LLM、零图依赖。

## 实现

**服务层** [code_graph.py](../../Desktop/Agent/Hyperion/src/hyperion/services/code_index/code_graph.py) `when_introduced(repo_path, *, symbol?, file?, line?, line_end?, max_commits=20)`,双锚点二选一:

- **pickaxe(symbol,可配 file 收窄)**:`git log -S<symbol> --patch` —— 找 diff 里增/删过该字符串的 commit。**-S 紧贴值**(`-Sfoo`)防 `-` 开头被当选项;短名(如 `scan`)必须配 file pathspec 否则命中一片。
- **行历史(file+line[,line_end])**:`git log -L<l>,<end>:<file>` —— 追这一行段演化,**自带改名跟随**(行号漂移不怕);行号按当前工作树。

候选表时间倒序,每条 sha/date/author/subject/added/removed。**计数语义**:pickaxe 模式只数**含 symbol** 的 ± 行(`+++`/`---`/`diff `/`@@` 头跳过)→ 引入 commit 通常是最老 added>0/removed==0 那条,**中间 added/removed 成对的多是重构搬移**;行历史模式数所追行段的 ± 行。输出格式 `--format=%x1e%H%x1f%aI%x1f%an%x1f%s` + `--patch` 一趟 git 同拿元数据和 diff(不逐 commit git show)。

**note 双保险**:哪条真引入缺陷(而非搬移)是语义判断归 agent(逐条 `git show`,引入 commit 的 message/diff 常直接暴露缺陷意图 = 假设循环辅助证据);命中 max_commits 封顶时提示加大重调。只查当前 checkout 分支(不带 `--all` 防多分支重复)。

**工具层** [mcp_memory.py](../../Desktop/Agent/Hyperion/src/hyperion/tools/mcp_memory.py) `when_introduced(repo_path, symbol?, file?, line?, line_end?, max_commits?)`:ValueError → 「没法算」友好串(同 merge_eval 模式);`_honest_truncate(body, 8000, how_to_refetch=...)`;header 带 mode + 锚点摘要 + 候选数。第 16 个 MCP 工具。

## 真仓探针(全绿)

- **hostap 上游(全史)**:`scan_only_handler` + file=scan.c → 唯一候选 `66fe0f70` 2013-02-07 "Add 'SCAN TYPE=ONLY' functionality" —— **demo2 金标 bug 机制的引入点,分毫不差**(+2/-0 纯引入)。行历史模式锚同函数定义行 3757 → 同一 commit。
- **deepin bluez(31-commit 浅史)**:`sdp_extract_seqtype` → `ae4512f` Init commit(+4/-0)+ `c991dc26` 升级同步(-4);浅史仓行为符合预期(Init 全量引入)。
- **错误路径**:symbol+line 同给 / 都不给 / line 无 file → 三条 ValueError 友好串。

## 测试

3 单测(tests/test_mcp_tools.py,`_mk_introduced_repo` 3-commit 夹具:c1 引入 bug_func / c2 加别的函数 / c3 无关文件):pickaxe 只命中 c1 且 added=1/removed=0;行历史(锚 c2 挪行后的当前行 2)只命中 c1;错误路径友好串。夹具坑:git 身份用 `-c user.name=...` 每命令传,**别用 env 里的 `a[0][-1]` 造日期**(commit 参数末字符不是数字,畸形 GIT_AUTHOR_DATE → 128 静默)。全 mcp_tools 38 绿 + 全量 285 绿 + ruff clean。

## SKILL/prompt 接线

bug-rca SKILL:allowed-tools 加 `hyperion_when_introduced`;工具表加一行(「候选难分胜负时——查这段缺陷逻辑哪个 commit 带进来的」);证伪纪律「先列候选再淘汰」节尾加一句(辅助证据路,**非硬门**——不强制每案都查)。agent prompt(config/opencode_hyperion.json)工具行同步。**故意不做**:`memory_memorize` 的 `introduced_by` 参数(backlog 原文「按需,不抢跑」——等真需求触发)。

关联:[[p1p2-high-priority-handoff]](同 backlog 双纪律)/ [[upstream-merge-handoff]](merge_eval 同分工:确定性地板+LLM 天花板)。
