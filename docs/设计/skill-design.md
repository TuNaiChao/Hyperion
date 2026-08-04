# Skill 子系统设计文档

> 状态:**规划中(2026-08-04 调研 + 设计完成,待 R3 全套收尾后实施)** · 实现阶段:post-R3(归入 R4 skills/MCP)
> 上位文档:[architecture.md §8](architecture.md) · 参考:**opencode Agent Skills(原生)** + deer-flow skills 系统 + [Agent Skills 开放规范](https://agentskills.io/specification)
>
> **关键调研结论(改变架构)**:opencode 已有**一等公民的 skill 原生支持**([opencode.ai/docs/skills](https://opencode.ai/docs/skills/))—— 自动发现 `SKILL.md` + 原生 `skill({name})` 工具(progressive disclosure)+ `permission.skill` 权限。**Hyperion 不需要自建 skill 激活**,只当**仓库 + 选择器 + 物化器**:把选中 skill 写进委托工作区,opencode 原生接管。这正中踩坑 #2 教训(委托型 agent 别重造 delegate 已有的能力)。

---

## 0. 这是什么(面向小白)

**给 Hyperion 一个"可复用的任务方法论包"(skill),让它在委托 opencode 干活时,把这个包一起送过去,opencode 按方法论干活。**

举用户的真实场景:Linux 系统软件常要在某个分支上修 CVE 漏洞。用户写一份"cve-patch"skill(描述 + 修复方法论:怎么 triage 受影响代码、最小补丁原则、backport 注意、怎么验证)。bug-RCA 时挂上这个 skill → Hyperion 把它送进 opencode 的工作区 → opencode 的修复 agent 自动加载方法论 → 按它修 → Hyperion 用 git diff 收补丁。**Hyperion 间接获得了 CVE 自动修复能力,但一行修复代码都没写**(全靠委托 + skill)。

**与现有模块的关系:** skill 是**横切能力**——bug-RCA / 深度调研 / 未来的 cve-fix workflow 都能挂 skill。它不是新支柱,是给现有委托链加的"方法论注入"。

---

## 1. 现状(2026-08-04):不支持(仅占位)

```
config/extensions_config.json → "skills": {}          # 空占位
factory.py 路线图 → "8. Skill*(若引入 skills...)"     # 槽位预留
state.py → "skill_context ← 若引入 SkillActivationMiddleware"  # 注释占位
```
skill 在 [CLAUDE.md 路线] 是 R4 项(`R4 ... + skills/MCP`),目前**零代码**。本设计因用户需求驱动(CVE 自动修复)提前到 post-R3 落地。

---

## 2. 关键调研发现:opencode 原生支持 Agent Skills

[opencode.ai/docs/skills](https://opencode.ai/docs/skills/) 确认 opencode 有一等公民的 skill 支持(基于 [Agent Skills 开放规范](https://agentskills.io/specification),与 Claude Code 同标准):

### 2.1 自动发现
opencode 从这些路径发现 `SKILL.md`:
- 项目:`.opencode/skills/<name>/SKILL.md`、`.claude/skills/<name>/SKILL.md`、`.agents/skills/<name>/SKILL.md`(**从 cwd 向上遍历到 git 根**)
- 全局:`~/.config/opencode/skills/`、`~/.claude/skills/`、`~/.agents/skills/`

### 2.2 按需加载(progressive disclosure,省 token)
启动时只给 agent 注入 `<available_skills>` 清单(每条仅 name + description):
```
<available_skills>
  <skill><name>cve-patch</name><description>...</description></skill>
</available_skills>
```
agent 觉得相关时调原生 `skill({name:"cve-patch"})` 工具加载全文。**省 token**:不相干的 skill 不进上下文。

### 2.3 frontmatter 规则(严格)
- `name`(必填):正则 `^[a-z0-9]+(-[a-z0-9]+)*$`,1-64 字符,**须与目录名一致**。
- `description`(必填):1-1024 字符,要足够具体让 agent 选对。
- 可选:`license` / `compatibility` / `metadata`(str→str map)。
- **未知字段被忽略**(Claude Code 的 `allowed-tools` opencode 不认 → 权限走 `permission.skill`,不是 frontmatter)。

### 2.4 权限
`opencode.json`:
```json
"permission": { "skill": { "*": "allow", "cve-*": "allow", "internal-*": "deny" } }
```
| 值 | 行为 |
|---|---|
| allow | 立即加载 |
| deny | 对 agent 隐藏 |
| ask | 加载前问用户 |
可按 agent 覆盖(`agent.<name>.permission.skill`)。可 `tools:{skill:false}` 整个禁掉。

### 2.5 含义
**Hyperion 不需要自建 skill 激活**。opencode 是消费者,自带激活。Hyperion 只需:① **存** skill(仓库 + loader);② **选** skill(用户 CLI flag / workflow 参数);③ **物化** 进委托工作区(`.opencode/skills/<name>/SKILL.md` + 权限)。

---

## 3. 架构:Hyperion 存/选/物化,opencode 消费

```
用户上传 CVE skill ─→ Hyperion skill 仓库(skills/cve-patch/SKILL.md)
                              │ (loader 解析 frontmatter + 验 name 正则 + description 长度)
hyperion bug-rca --skill cve-patch
                              │
bug_rca workflow ─→ delegate.run(skills=["cve-patch"])
                              │
OpencodeDelegate 物化:
   ├─ 写 <workspace>/.opencode/skills/cve-patch/SKILL.md(从仓库 copy)
   └─ workspace 级 opencode config 加 permission.skill: {"cve-patch":"allow"}
                              │
   opencode run --dir <workspace>  ← opencode 自动发现(从 cwd 走)
                              │
   opencode repair agent 看到 <available_skills> 含 cve-patch
      → 调 skill({name:"cve-patch"}) 加载修复方法论
      → 按方法论 edit code/ 改代码
                              │
Hyperion 用 git diff 观察补丁(现有机制,不变)+ validate_patch(现有)
```

### 关键范围决策(防过度设计)
**不在 Hyperion 自己的 agent 里激活 skill。** deer-flow 自建 `SkillActivationMiddleware`+`ToolPolicy`+`Slash`+`Catalog`+`Describe`(5+ 模块)是因为**deer-flow 自己的 agent 吃 skill**。Hyperion 的 lead agent 做调度+记忆(不直接修代码),不需要吃 skill。消费者是 opencode(原生支持)。所以 v1 不建激活中间件,留 pull-by-need(S5)。

---

## 4. 分阶段计划

| 阶段 | 干啥 | 文件 | 模式 |
|---|---|---|---|
| **S1** | skill 仓库 + loader + CLI(`hyperion skill list/add/remove/show`)。loader 借 deer-flow `parser.py`/`types.py`:解析 frontmatter、验 opencode name 正则、description 长度。仓库落 `skills/<name>/SKILL.md`(项目级 git 跟踪;per-user 留 R4) | `services/skills/{store,loader,types}.py` + `cli.py` cmd | loader 核心**窗口展示·你手敲**;store/CLI/test 我改 |
| **S2** | delegate 物化:`OpencodeDelegate.run` 加 `skills: list[str]` 参数;跑前把选中 skill 写进 `<cwd>/.opencode/skills/<name>/SKILL.md` + workspace 级 opencode config 加 `permission.skill` allow | `tools/delegate.py` + workspace 级 config 生成 | 我改(胶水) |
| **S3** | bug_rca 透传:`hyperion bug-rca ... --skill <name>`(可重复)→ workflow state → delegate。skill 可在 localize/repair 任一阶段挂 | `workflows/bug_rca/{state,nodes}.py` + `cli.py` | 我改(接线) |
| **S4** | 发货样例 `skills/cve-patch/SKILL.md`(用户场景):description + 修复方法论(triage 受影响代码 / 最小补丁 / backport / 验证)。端到端可演示 | `skills/cve-patch/SKILL.md` | 窗口展示·你手敲(方法论是核心) |
| (pull-by-need) S5 | Hyperion lead agent 自带 skill 激活(`SkillActivationMiddleware`,deer-flow 式)——**仅当将来要 Hyperion 自己的 agent 吃 skill 才做**。R4+ | `platform/runtime/middlewares/skill_activation.py` | 窗口展示 |

---

## 5. CVE 端到端(验证设计闭环)

```bash
# 1. 上传 skill(或直接放 skills/cve-patch/SKILL.md)
hyperion skill add cve-patch --file ~/my-cve-skill.md

# 2. 带 skill 跑 bug-rca
hyperion bug-rca --repo example/demo2/wpa --log journalctl_b.txt --skill cve-patch

# 3. 发生了啥:
#    Hyperion 把 cve-patch 物化进 <workspace>/.opencode/skills/cve-patch/SKILL.md
#    opencode repair agent 启动 → <available_skills> 含 cve-patch
#    agent 调 skill({name:"cve-patch"}) → 拿到修复方法论
#    按方法论 edit code/ → Hyperion git diff 观察补丁(既有)+ validate_patch(既有)
```

---

## 6. 借鉴对照(做了 deer-flow 功课)

| 借 deer-flow | 不借(因 opencode 原生 / YAGNI) |
|---|---|
| `SKILL.md` 格式 + frontmatter(name/description) | `SkillActivationMiddleware`(opencode 原生激活) |
| `parser.py`/`types.py` 解析+校验骨架 | `ToolPolicyMiddleware`(opencode 用 `permission.skill`) |
| `skills/public/` 仓库组织 | `slash.py`(deer-flow 的 `/skill` 命令;Hyperion 用 CLI flag) |
| install/validate 思路 | `catalog.py`/`describe.py`(deferred discovery;opencode 自带 progressive disclosure) |
| SkillScan 安全扫描(投毒防御) | 暂不(可 pull-by-need:skill 来源不可信时) |

deer-flow 相关代码(本地只读副本):`deer-flow/backend/packages/harness/deerflow/skills/{parser,types,storage,installer}.py` + `deer-flow/skills/public/*/SKILL.md` 样例。

---

## 7. 待办 / 开放问题

- **per-user skill 仓库**(R4 多用户/租户隔离):v1 项目级 `skills/`,per-user 留 R4(落 `{base_dir}/users/{user_id}/skills/`,同 deer-flow)。
- **skill 投毒防御**:若 skill 来源不可信(社区上传),需 SkillScan 式静态扫描(deer-flow `skills/skillscan/`)。v1 假定 skill 可信(用户自己写/项目内)。pull-by-need。
- **workspace 级 opencode config 生成**:bug_rca 现用全局 `config/opencode_hyperion.json`(env `OPENCODE_CONFIG`)。加 skill 需在 workspace 生成一份含 `permission.skill` 的 config(或合并进现有 config)。S2 落实时定(看 opencode 是支持 config 合并还是要单文件)。
- **opencode 版本**:本机 v1.18.3 已确认有 skill 支持(见 [docs](https://opencode.ai/docs/skills/))。若老版本无 `skill` 工具 → 物化后 agent 不发现 → 降级(skill 不生效但不崩)。
- **S5 lead-agent 激活**:仅当未来 Hyperion 自己的 agent 要吃 skill(不只是委托)才做。当前无此需求。

---

## 8. 参考

- [opencode Agent Skills 文档](https://opencode.ai/docs/skills/) —— 发现路径 / frontmatter / 权限(核心依据)
- [Agent Skills 开放规范](https://agentskills.io/specification) —— 跨平台 SKILL.md 标准
- [Claude Code skills 文档](https://code.claude.com/docs/en/skills) —— 同标准(`allowed-tools` 是 Claude 私有,opencode 忽略)
- deer-flow(本地 `deer-flow/`):`backend/packages/harness/deerflow/skills/` + `skills/public/*/SKILL.md`
