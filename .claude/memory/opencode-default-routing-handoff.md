---
name: opencode-default-routing-handoff
description: 2026-08-19 落地:opencode 默认界面自动路由(去 Tab 切换)—— 8 个 rootrecall agent block 降级 subagent + localize/repair 加 hidden + 仓库根 AGENTS.md 路由表(用户拍板 skill 载入当前会话);含 opencode 1.18 机制事实与三条 e2e 全绿
metadata:
  type: project
---

# opencode 默认界面自动路由交接(2026-08-19 落地)

## 背景

用户痛点:opencode 里 10 个 `rootrecall-*` block 全 `mode: "primary"` 挤在 Tab 切换列表,用哪个工作流要先 Tab 切到对应模式。目标:打开 opencode 停在默认界面(用户的默认 = oh-my-opencode 插件的 **Sisyphus - ultraworker** 编排型 agent)直接打字提问,agent 自动判断走哪个工作流。

## 落地三件套

1. **`config/opencode_rootrecall.json`**:8 个工作流 block(bug-rca/patch-review/backport/compare/onboarding/memory-health/upstream-merge/domain-research)`mode: primary → subagent`(其余字段不动);`rootrecall-localize`/`rootrecall-repair` 保持 primary + 加 `hidden: true`。
2. **仓库根 `AGENTS.md`**(新,随 git):模型受众路由表 —— 护栏(只路由「分析某代码库/bug/记忆」类请求;开发 RootRecall 本身直接干活)+ 8 行问题形态→skill 表(浓缩 skill-routing-matrix §一/§二)+ 执行姿势(默认 `skill` 载入当前会话;`@rootrecall-*` 点名/task 委派作逃生舱)+ 公共纪律一行。**改判据要与 docs/skill-routing-matrix.md 两处同步**(AGENTS.md 是浓缩版)。
3. **`scripts/wire_opencode.sh` 加门2**:`ln -sfn $REPO/AGENTS.md $BUG/AGENTS.md`(单源真相);目标已有实体 AGENTS.md → 跳过不覆盖;幂等。原「门2 MCP 锚定」顺延为门3。

用户拍板(AskUserQuestion):执行姿势三选一取 **skill 载入当前会话**(追问连续 + MCP 走 primary 最稳);代价 = 只读流程丢 `bash:deny` 硬门,退化为 SKILL.md 文字纪律 + 人在环。

## opencode 机制事实(1.18.18 装机 / clone v1.18.8 源码核实,可复用)

- **Tab 列表过滤** = `mode !== "subagent" && !hidden`(`packages/tui/src/context/local.tsx:78`);`@` 补全反向过滤 = `!hidden && mode !== "primary"`。
- **subagent 暴露**:task 工具描述动态拼所有非 primary agent 的 `- name: description`(`packages/opencode/src/tool/registry.ts:260-273`)—— description 是路由依据,缺失显示废文案;`subagent_depth` 默认 1;子 session 继承父 deny、用自己 permission,MCP 工具默认可用。
- **skills 免费双通道**:每个 agent(含默认)system prompt 都注入 `<available_skills>` 目录 + `skill` 工具;frontmatter 只解析 name/description(allowed-tools 被忽略,与 dsh 同构)。
- **AGENTS.md 是正确注入口**:项目指令注入所有 agent 且不替换基础 prompt(`session/instruction.ts`);改 `agent.build.prompt` 会整体替换按模型的默认 prompt 且够不着插件 agent(Sisyphus)。
- **`opencode run --agent` 只拒 subagent 不拒 hidden**(`packages/opencode/src/cli/cmd/run.ts:595-617`)—— delegate 链(`delegate.py:386` 点名 localize/repair)因 hidden+primary 两全。
- **oh-my-opencode 插件**动态收集 `mode !== "primary"` 的 agent 作 Sisyphus 的委派目标(`isTaskCallableAgentMode: "all"|"subagent"`)—— 降级后天然兼容。
- 无通用「按问题自动切 primary」机制;自动路由只到 subagent 层(task)与 skill 层。

## e2e 三条全绿(headless 自跑)

1. `opencode run --agent rootrecall-localize "只回复 OK"`:DB 实锤 `agent=rootrecall-localize`、回复 OK、无「is a subagent」回退警告 —— delegate 硬门不破。
2. 仓库根默认界面问「v20 和 v25 的蓝牙连接流程有什么差异?」:session agent = **Sisyphus - ultraworker**,首个工具调用 `skill(name=compare)` → memory_recall 命中 → 短路路径 → export_report 落盘 `data/compare/bluez-rca.md`,结论合金标;Sisyphus 没抢去派自己的子代理(AGENTS.md 指令生效)。
3. 接线目录(/tmp wire 测试仓)同问:同款 `skill(compare)` 打头,菜谱走完。

## 坑位/注意

- 8 个 block 的 prompt 与 8 个 SKILL.md 本就是平行两份(无交叉引用),本次未动该结构 —— 同步负担仍在,记档。
- e2e2/3 会覆写 `data/compare/bluez-rca.md`(data/ gitignored,无害)。
- TUI Tab 列表变化无法 headless 驱动,源码过滤逻辑核实过,用户真机一眼即验(应只剩 Sisyphus/build/plan 等)。
- 记忆 `opencode-mcp-wiring.md` 里「MCP 留 primary agent」的两个 task 子代理上游 bug(#33397/#16491)是旧版结论,1.18.x 已演进(subagent 用自己的 permission、MCP 默认可用);本次选 skill-in-primary 路线本就不依赖 subagent 跑 MCP。

关联 [[opencode-mcp-wiring]](接线硬细节)/ [[opencode-config-drift]](单源真相)/ [[colleague-onboarding-toolset-handoff]](wire 脚本门1/门3 演进)/ [[compare-skill-handoff]](e2e 金标来源)。
