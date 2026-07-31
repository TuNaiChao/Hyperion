# bug-RCA Workspace 设计(每 bug 一个专用工作目录)

> 状态:设计稿(2026-07-29)→ **R3.1 #51 已落最简形态**(create_workspace + validate Tier0 + git diff 观察,已 e2e 验);完整七段/日志预筛/双通道 待 R3.2+。
> 依据:两路调研(opencode 最佳实践 + sandbox/workspace 设计,含本地 deer-flow 代码精读 + OpenHands/SWE-agent/Agentless 最新做法)。
> 关联:[architecture.md](architecture.md) §workspace、[bug-rca-design.md](bug-rca-design.md)、[memory-design.md](memory-design.md)。

## 0. TL;DR(一句话)

**每次 bug 分析 = 一个专用 workspace 目录(`<repo>__<bug-id>__<hash>/`,含全量代码 checkout + 日志 + 触发 + delegate 通信 + artifacts + 补丁 + 报告 + 文档汇总),opencode 在该目录里跑(`--dir`),读真实代码+日志、生成可 apply 的补丁;Hyperion 负责组装这个目录、调度、预筛日志、验证补丁、沉淀记忆。** 隔离默认本地目录(R2/R3),Docker 作 R5 可选;沙箱抽象直接复用 deer-flow。

---

## 1. 为什么需要 workspace(解决什么)

之前 R2 MVP 的做法是把 localize 圈出的**锚点代码内联进 prompt**,直接 `--dir <repo>` 跑 opencode。痛点:

1. **opencode 被当"读文本模型"用,废了它的 agent 长处**(grep/read/LSP 自导航)。
2. **补丁易错位**:基于 prompt 内联的代码快照(还被 sticky/窗口截断)写 diff,路径/行号可能和真实文件不同步 → `git apply` 打不上。
3. **日志没法结合**:大日志(journalctl 几 MB)塞不进 prompt,opencode 只能看代码片段。
4. **artifacts 散落**:补丁、报告、候选没地方归拢,难回溯、难沉淀记忆。

workspace 模型一次性解决:opencode 在**全量代码 checkout + 日志同在**的目录里自主分析 → 补丁基于真实文件 → 可 apply;所有产物归档到固定结构 → 可回溯 + 沉淀。**对标 Agentless(定位→修复→验证)+ deer-flow(per-thread per-user sandbox)+ SWE-bench(每实例一容器)。**

---

## 2. workspace 目录结构(七段)

```
~/.hyperion/workspaces/<repo>__<bug-id>__<hash6>/   ← opencode --dir 指这里;session 自动归属隔离
├── META.json                  # Hyperion 写:bug 元数据(workspace_id/repo_url/base_commit/bug_id/
│                              #                  created_at/delegate{type,model}/status/host_os)
├── code/                      # Hyperion 写:全量 git checkout(git clone --filter=blob:none 省空间)
│   └── (源码 + .git/)         #   opencode 在此读码 + 改码;补丁基于真实文件
├── triggers/                  # Hyperion 写:bug 输入
│   ├── issue.md               #   ★问题描述/漏洞报告(支持 .md/.txt/.pdf 或直接 prompt;ingest 解析成统一文本)
│   ├── keywords.json          #   ★从 issue 抽的关键字(错误码/函数名/符号/症状词)—— 驱动日志+代码预筛
│   ├── logs/                  #   ★日志(journalctl/btmon/coredump/...)—— opencode 必读
│   └── poc/                   #   重现脚本/PoC(可选)
├── delegate/                  # Hyperion ↔ opencode 通信
│   ├── prompt.md              #   Hyperion 写的任务契约(线索 + 嫌疑起点指引 + JSON schema)
│   ├── context.md             #   Hyperion 组装的手术刀上下文(记忆召回 + 结构图 + 预筛日志关键行)
│   ├── contract.json          #   机器可读契约(输出格式/工具白名单/超时)
│   └── delegate_log/          #   opencode 执行日志(事件流 step_events,供可观测回放)
├── artifacts/                 # opencode 写:中间产物
│   ├── candidate_patches/     #   多候选补丁(001.diff/002.diff …,Agentless 做法)
│   └── validate/              #   每候选的验证日志(apply/build/test)
├── patch/
│   └── final.diff             # Hyperion 选定的最终补丁(必须 git apply --check 可过)
├── report/                    # Hyperion 写:分析报告(给用户)
│   ├── root_cause.md          #   根因分析(trigger-chain + 证据 file:line)
│   ├── fix.md                 #   补丁说明(why this fix)
│   ├── test_results.md        #   测试验证结果
│   └── risk.md                #   补丁风险评估(回归/兼容性)
└── docs/                      # ★文档汇总(沉淀进记忆 P3)
    ├── summary.md             #   <500 字摘要(喂 MemoryService 跨 bug 召回)
    ├── lessons.md             #   经验教训(下次同类 bug recall 命中)
    └── timeline.md            #   分析时间线(谁在何时做了什么)
```

> ⚠️ **现状 vs 目标**:上图是**完整目标结构**(R3 演进)。**R3.1 #51 已落最简形态**(`manager.create_workspace` 实际建):`code/`(copytree + 追加 `.gitignore` 排除 `.omo/`/`.opencode/` + `git init`/base commit)+ `triggers/issue.md` + `delegate/`(含 `delegate_log/`)+ `patch/` + `report/` + `AGENTS.md`。`META.json`/`keywords.json`/`logs/`/`context.md`/`artifacts/`/`docs/` 等 = R3.2+ 待建(见 §5/§8)。

### 命名约定
- workspace 目录名:`<repo>__<bug_id>`(repo 短名 + bug_id,默认时间戳)。例:`wpa__20260730-143022`。(目标加 `__<hash6>` 防重,待 META.json 落地。)
- candidate patches:`NNN.diff`(三位序号);final patch 固定名 `final.diff`(脚本引用)。

### 谁写谁
| 目录 | 写入方 | 说明 |
|---|---|---|
| `code/` / `triggers/issue.md` / `AGENTS.md` | Hyperion | 建 workspace 时:copytree + `.gitignore` + `git init`/base commit + 复制 issue |
| `delegate/{prompt,context}.md` | Hyperion | 召回记忆 + code_index + **预筛日志** → 组装;prompt 是「线索 + 嫌疑起点指引 + JSON schema」(方式 B,非内联代码) |
| `delegate/delegate_log/` | Hyperion(#56 可观测) | opencode stdout 流 + 摘要持久化(替 `/tmp` 诊断) |
| `artifacts/candidate_patches/` | opencode | 多候选(Agentless)—— **兜底,`delegate.rerank.enabled` 默认关** |
| `patch/final.diff` | Hyperion | git diff 观察 code/ 改动(主路径)+ 可选 rerank 兜底选 top-1 |
| `report/` / `docs/` | Hyperion | 综合产物生成报告;`docs/summary.md` 沉淀进 MemoryService |

### 多 bug 隔离
**每 bug 一个 workspace 目录**(强推荐):对标 deer-flow per-thread;不同 bug 可能 base_commit/repo 版本不同,共享 checkout 会打架;单个 workspace 可整目录删,不影响其他。workspace root 默认 `~/.hyperion/workspaces/`(gitignore,跨机只同步 `docs/summary.md` 进记忆)。

---

## 3. 隔离方案:本地默认 + Docker 可选

| | 本地目录 | Docker |
|---|---|---|
| deer-flow 立场 | **默认 `LocalSandboxProvider`**(一人/本地优先) | `AioSandboxProvider`(生产/不信任代码) |
| OpenHands 立场 | `LocalRuntime`「仅开发,无隔离」 | `DockerRuntime` 默认 |
| 隔离/安全 | 弱(deer-flow `security.py` 明说不是安全边界) | 强 |
| macOS 成本 | 无 | Docker Desktop 商用要 license + ~6GB 内存 + bind mount 慢 2-10x |
| 启动 | 毫秒 | 秒级(warm pool 缓解) |

**决策:R2/R3 本地目录,R5 才考虑 Docker。** 理由:
1. deer-flow 默认本地证明「一人/本地优先」可接受。
2. Hyperion 分析的代码是自己的/已知开源仓,不是「完全不可信」;LLM patch 被执行概率低(主要编译验证),有 `git reset` 兜底。
3. **抽象层留好**(复用 deer-flow `Sandbox`/`SandboxProvider` ABC),以后切 Docker 零改业务代码。

**何时升级 Docker**:跑未知第三方 PoC/exploit、多用户共享、跨机可复现硬需求(R5)。macOS 上跑测试时切 Docker(buildx + linux/amd64 镜像);Linux 主战场用本地或 moby/podman(无 license)。

---

## 4. opencode 在 workspace 里怎么跑

### 启动命令(由 `delegate.py:_build_cmd` 组装)
```bash
opencode run \
  --dir <workspace>/code \
  --agent hyperion-localize \      # C:指定子 agent(hyperion-localize/repair,steps 强制收敛)
  -m uniontech-ai/glm-5.2 \
  --format json --auto \
  [--continue] \                   # A:续同 cwd 最近 session(verify-refine 双循环承载)
  "$(cat .../delegate/prompt.md)"
# --dir = workspace/code → opencode 在此读+改;session 按 cwd 隔离;AGENTS.md 自动注入契约
# --continue 与 --agent 正交(已核查 opencode run.ts):可续同 session 中途换 agent(localize→repair)
```

### AGENTS.md(放 workspace 根,强制契约,注入 system prompt)
opencode 自动发现 `AGENTS.md`(从 cwd 向上遍历;不是 `opencode.md`)。三级叠加:本地 `AGENTS.md` → 全局 `~/.config/opencode/AGENTS.md` → `~/.claude/CLAUDE.md` 回退。全局 `opencode.json` 的 `instructions` 字段(支持 glob + 远程 URL)放**跨 bug 通用契约**。

workspace 根的 `AGENTS.md`(本 bug 特定):
```markdown
# 本 bug 工作目录(强制)
- 代码在 ./code/,日志在 ./triggers/logs/ —— 必须先读日志再分析
- Hyperion 已预筛日志关键行到 ./delegate/context.md,可自行 grep 原始日志深挖
- 嫌疑起点(file:line)见 ./delegate/prompt.md,优先读这些
- 阶段① localize:返回 {root_cause,evidence,trigger_chain,verdict,falsification};**不要 patch**
- 阶段② repair:用 edit 直接改 ./code/ 里的文件;返回 {verdict,falsification};**禁止贴 diff 文本**(Hyperion 用 git diff 观察)
- 改完必须自审(verdict);只改根因相关文件,禁止顺手重构;证据必须 file:line 溯源
```

### 关键 opencode 机制(调研核实)
- `--dir` 目录**必须先存在**(opencode 不自动建)→ Hyperion 先 `mkdir -p`。
- **session 按 `--dir` 天然隔离**(SQLite 单库 `~/.local/share/opencode/opencode.db`,按 project 分桶),`--title/--session/--fork` 管多 bug;`opencode export <id> --sanitize` 沉淀到 `delegate_log/`。
- 配置 **8 层合并**(全局 ← 项目 `opencode.json` ← `.opencode/` ← 环境变量 ← 受管),后者覆盖冲突键。
- `--format json` 是**块缓冲**(跑完才 flush),delegate timeout 时 [delegate.py](../../src/hyperion/tools/delegate.py) 要存已收 stdout(可观测性补丁,backlog)。

---

## 5. 问题描述解析 + 关键字驱动预筛(ingest 核心)

问题描述是 bug-RCA 的**起点 + 关键字源头**。ingest 把它解析成统一文本、抽关键字,关键字再**统一驱动日志预筛 + 代码 localize**(省 token = Hyperion 差异化)。

### 5.1 问题描述输入(多格式)
- `triggers/issue.{md,txt,pdf}` 或直接 prompt(cli `--trigger` / API 文本)。
- 解析:txt/md 直读;**PDF 用 pypdf/pdfplumber 抽文本**(demo1 就是 PDF 漏洞报告驱动);统一规范化写 `triggers/issue.md`。
- 谁写:用户放文件进 `triggers/`(或 cli 传文本),Hyperion ingest 解析。

### 5.2 关键字抽取(预筛源头)
从 issue 抽关键字 → `triggers/keywords.json`(错误码 / 函数名 / 文件路径 / 内核符号 / panic·OOPS·BUG·Warning / 模块名 / 症状词):
1. **规则**(快,确定性):正则抽错误码、函数名(`[a-z_]+\(\)`)、路径、内核符号、通用关键字。
2. **LLM 抽**(补):「这个 bug 涉及哪些关键符号/错误码/症状/模块?」(role=locator,flash)。

### 5.3 日志预筛(关键字驱动)
- **Hyperion 粗筛**:用 keywords + 通用关键字(panic/OOPS/错误码)grep `triggers/logs/` + 故障时间窗 + addr2line 符号化 + 堆栈折叠 + LLM 摘要 → `delegate/context.md`。
- **opencode 深挖**:拿预筛关键行后,用自带 grep/read 按线索深挖原始日志(`AGENTS.md` 提示)。
- 几 MB~GB 日志不能全喂 opencode(爆 token);Hyperion 粗筛省 token + 精准调度。

### 5.4 代码 localize file-level 预筛(关键字驱动,= 方案A)
- 现状(R3.1):LLM 三层漏斗(file→function→line)已跑通;file-level 仍是 LLM 看目录树选(未接 code_index 检索)。
- 改(方案A,R3.2+):用 keywords 对 `code_index` 做 BM25/embedding 检索取 top-20 文件 → LLM rerank top-5(输入从整棵树 → 20 行,快 + 准,对标 Agentless 正路)+ 可选 **localize 文件投票**(rerank A,复用 majority_vote)。

### 实现
`services/trigger_parser/`(多格式解析 + 关键字抽取,R3)+ `services/log_preprocess/`(日志预筛,见 #50)+ localize file-level 改检索(方案A)。**关键字是三者的统一纽带**。

---

## 6. 补丁验证(Tier 0 已落;全链门控构建环境 R5)

补丁由 Hyperion 用 `git diff --cached` 观察 `workspace/code/` 改动生成(**不信任 delegate 吐的 diff**,根治 R2 off-by-one),`services/workspace/validate.py` 做 Tier 0 验证:

**Tier 0(R3.1 已落,零 LLM):**
1. **forward `--check`**:`git apply --recount --check patch`(严格);失败降级 `--3way` → `patch -p1 --dry-run`(记降级路径,反映补丁质量)。
2. **reverse `--check`**:补丁能干净 revert(证必要:能撤回 = 真实改动,不是空补丁)。

**目标全链(门控于「构建环境就绪」,R5 Docker)** —— wpa/bluez build 是硬前提且多无测试套件,不强凑:
3. clean checkout(`git checkout <base> && git clean -xfd`)。
4. 编译(`make -j`,log → `artifacts/validate/build.log`)。
5. 测试回归:FAIL_TO_PASS(patch 后必 pass)+ PASS_TO_PASS(无回归)。
6. 多候选 rerank(Agentless)—— **兜底,`delegate.rerank.enabled` 默认关**(无测试 oracle 时投票平凡,见 [bug-rca-design.md §7.6](bug-rca-design.md))。

**quilt 场景**(系统包/Debian):`final.diff` → `debian/patches/fix-bug-N.diff` + 更新 `series` → `quilt push -a`。

**diff 观察通道(R3.1 现状 = 单路 git diff;双通道待办 F5):**
- ✅ **跑后 git diff**(已落,主路径):`nodes._observe_patch` = `git add -A && git diff --cached`,ground truth,行号/格式天然对。
- 🆕 **流内 filediff**(待办):opencode `--format json` 流里 `edit` 完成时吐 `tool_use.part.metadata.filediff = {file,patch,additions,deletions}`(`opencode/.../tool/edit.ts`)。落地后与 git diff 交叉校验(防"工具改了文件 opencode 未报")。当前单路 git diff 够用。

> ⚠️ **verify 范围(F3)**:wpa_supplicant / bluez 的**构建环境是未落实硬前提**且多无测试套件。**R3.1 只做 Tier 0**;编译/F2P/P2P 门控于「构建环境就绪」(独立子任务,可能并 R5 Docker)。无测试套件时 repro 用**日志符号化替代**(见 [bug-rca-design.md §7.5](bug-rca-design.md) F4)。

---

## 7. 跨环境复用(Linux ↔ macOS)

`opencode.json` / `AGENTS.md` / `.opencode/` 都**可进 git/dotfiles**。API key 三种存法(按安全排序):
1. **环境变量**(最推荐):自定义 provider 用 `{env:VAR}` 替换。
2. `{file:path}` 替换:key 放 `~/.secrets/`,config 只引用。
3. `auth.json` 明文(`~/.local/share/opencode/auth.json`,路径硬编码,不进 git)。

**换机 4 步**:
```bash
curl -fsSL https://opencode.ai/install | bash            # 1. 装
git clone <dotfiles> ~/dotfiles && ~/dotfiles/install.sh  # 2. 拉 ~/.config/opencode/(opencode.json+AGENTS.md+agents/)
echo 'export UNIONTECH_AI_API_KEY=...' >> ~/.zshrc         # 3. key 走 env(不进 git)
opencode debug config && opencode models uniontech-ai      # 4. 验证
```

⚠️ **跨机同步前**:本机 `opencode.json` 若明文存 key,必须先改 `"apiKey": "{env:UNIONTECH_AI_API_KEY}"`,否则 key 进 git 泄露。两台机 opencode 版本用 `opencode upgrade` 对齐。

---

## 8. 落地分阶段

| 阶段 | workspace | 隔离 | 日志预筛 | 补丁验证 |
|---|---|---|---|---|
| **R2 末(最简)** | 本地目录 + 7 段 lite;delegate cwd=workspace | 本地 | 跳过(trigger 预摘要) | 步骤 1-3(apply/revert) |
| **R3.1(已落最简)** | code/triggers/delegate/patch/report + AGENTS.md + `.gitignore` + git base | 本地 | 跳过(trigger 摘要) | Tier0(apply/revert)+ git diff 观察 |
| **R3.2+(完整)** | 完整 7 段 + META + artifacts | 本地(`LocalSandbox`) | 完整粗筛 5 步 | 双通道 + (rerank 兜底默认关) |
| **R5(生产)** | 同结构 | Docker(`AioSandboxProvider`) | 同 | + 多架构镜像 + warm pool |

**R2 末最简形态**(可能同时解当前 delegate timeout):delegate 改成「workspace 目录 + AGENTS.md 契约 + 方式 B 指引 prompt」(opencode 读全量 code/+logs 而非内联片段)。

---

## 9. 复用 deer-flow 资产清单(免造轮子)

| deer-flow 文件 | 复用价值 |
|---|---|
| `sandbox/sandbox.py` | `Sandbox` ABC(6 抽象方法:execute_command/read_file/write_file/list_dir/glob/grep)直接搬 |
| `sandbox/sandbox_provider.py` | `SandboxProvider` ABC + acquire/get/release 生命周期 + 工厂反射 |
| `sandbox/local/local_sandbox.py` | `LocalSandbox`(path mapping + 子进程 + `_BoundedPipeCapture` pipe drain + 进程组 SIGKILL timeout) |
| `sandbox/env_policy.py` | env scrub(继承 os.environ 时清 `*KEY*/*SECRET*/*TOKEN*`)—— **防 API key 泄到 delegate 子进程** ★ |
| `workspace_changes/{scanner,diff}.py` | 前后扫描 + `difflib.unified_diff` 生成 patch —— **待接入**(R3.1 暂用原生 `git add -A && git diff --cached` 单路;接入后获 scanner 保护:敏感文件/二进制/大小上限) |
| `sandbox/security.py` | `uses_local_sandbox_provider` / `is_host_bash_allowed` 模式 |
| `community/aio_sandbox/` | R5 切 Docker 时直接引入(warm pool + ownership store) |

deer-flow 子 agent 产 patch 的方式 = `str_replace` 工具调用(非吐 diff 文本),由 `workspace_changes` 从外部观察生成 diff —— Hyperion 可同样观察 `code/` 改动生成 patch,不依赖 opencode 吐格式正确的 diff。

---

## 10. Hyperion 接入点(对照 architecture.md)

- **新建 `src/hyperion/services/workspace/`**:`WorkspaceManager`(创建/列出/归档 workspace)+ `LocalWorkspaceProvider`(对标 deer-flow `SandboxProvider`)。七段目录初始化 + META.json + git checkout。
- **新建 `src/hyperion/services/log_preprocess/`**(R3):grep + 时间窗 + addr2line + 折叠 + 摘要,产 `delegate/context.md` 日志段。
- **改 `src/hyperion/workflows/bug_rca/`**:八步双循环 `ingest→recall→localize→assemble_localize→delegate_localize_loop→assemble_repair→delegate_repair_loop(含 git diff 观察 + validate_patch Tier0)→report_memorize`,见 [bug-rca-design.md §7.6](bug-rca-design.md)。
- **改 `src/hyperion/tools/delegate.py`**:opencode cwd = `<workspace>/code/`,任务输入 = `delegate/prompt.md`;timeout 时存已收 stdout(可观测性)。
- 复用:`services/code_index/`(召回写 `delegate/context.md`)+ `services/memory/`(完成后写 `docs/summary.md` → 记忆)。

---

## 11. 安全

- **opencode.json key 必须用 `{env:VAR}`**,不进 git(跨机前检查)。
- **env_policy scrub**(复用 deer-flow):delegate 子进程不继承宿主 `*KEY*/*SECRET*`,防 key 通过 opencode 工具调用泄到日志/trace。
- **敏感文件**:workspace scanner 对 `.env/.key/*credential*` 只存元数据不存内容(复用 deer-flow `workspace_changes`)。
- **不信任代码**:R5 Docker 隔离前,避免让 opencode 跑未知 PoC/exploit(本地无隔离)。

---

## 调研依据

- **opencode**:官方文档(cli/config/rules/agents/providers/share)+ GitHub sst/opencode;本机实测 v1.18.9,uniontech-ai provider。
- **deer-flow**:`backend/packages/harness/deerflow/sandbox/`(sandbox.py / sandbox_provider.py / local/ / env_policy.py / security.py)+ `workspace_changes/`(scanner.py / diff.py)+ `subagents/builtins/general_purpose.py`。本地精读。
- **OpenHands**:docs.all-hands.dev/usage/architecture/runtime(DockerRuntime 默认 / LocalRuntime 无隔离 / 三 tag 镜像 / volume overlay COW)。
- **Agentless**:arXiv 2407.01492(三阶段 Localization→Repair→Patch Validation,多候选 + repro test + rerank)+ GitHub OpenAutoCoder/Agentless。
- **SWE-bench**:全 Docker 化 harness(每实例一容器,git checkout base → apply → FAIL_TO_PASS/PASS_TO_PASS)+ mini-swe-agent。
