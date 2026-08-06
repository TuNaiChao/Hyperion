---
name: harness-pivot-handoff
description: "2026-08-06 用户拍板:Hyperion 从「调度型 orchestrator」转向「tool+skill server / 领域 harness」。已实现+commit 47654bd(+push origin/main):D0 MCP Streamable HTTP + 2 新工具(blast_radius/validate_patch)+ bug-rca SKILL.md + hyperion-bug-rca opencode agent(validate/memorize 硬门)+ orchestrator 降级 + CLAUDE.md 身份。e2e 绿:opencode 自驱 recall→search→blast→edit→validate(×2,抓补丁缺陷逼修正)→memorize,不走 orchestrator。下轮:落盘 patch 步骤 + P-A patch 分析。"
metadata:
  node_type: memory
  type: project
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-08-06T11:22:45.385Z
---

2026-08-06 里程碑:Hyperion 转向 **tool+skill server / 领域 harness**(用户拍板)。bug-RCA 主路径从
「自己调度 opencode 走固定六节点管线」改成「**opencode + bug-rca skill + 6 个 hyperion MCP 工具,agent 自驱、能自纠**」。orchestrator 降级留参考。**已 commit `47654bd` + push origin/main。**

## 为什么转(根因 + 证据)
- Anthropic 架构师 talk *"Don't Build Agents, Build Skills Instead"*(领域专业知识打包成 skill/tool,别重造 agent 底盘)。
- code-review-graph(已 vendor)+ Sourcegraph 都是 **tool-server(PROVIDER)**,不跟 agent 竞争推理。
- 踩坑 #2 项目级泛化:bug_rca orchestrator 正是 #2 警告的平行管线;+ #7/#8/#9 脆弱 + 产次优补丁。
- 完整设计:`docs/设计/harness-pivot-design.md`。

## 做了啥(11 文件,+483/−15)
- **D0 MCP Streamable HTTP**:`hyperion mcp serve --transport http`(mcp SDK 1.28.1 内置;FastMCP 构造吃 host/port → uvicorn;`run()` 不收 host/port)。config `mcp:` 段 + `config/codex_hyperion.toml`(`[mcp_servers]` 下划线)。解 ③ cold-boot。活体测:/mcp 返 406=端点活着。
- **2 新工具**(mcp_memory.py `build_server`,被 `hyperion*` glob 自动放行):`hyperion_blast_radius`(wrap `CodeGraph.impact_radius`,图缺优雅降级)+ `hyperion_validate_patch`(wrap `validate_patch`,git apply --check 硬门)。5 单测绿。
- **bug-rca SKILL.md**(`.claude/skills/bug-rca/`):7 步 playbook,validate/memorize 硬门。跨平台 agentskills.io。
- **hyperion-bug-rca opencode agent**(opencode_hyperion.json):playbook 烙进 prompt + steps=25 + validate/memorize **硬门强制**(agent=enforcement 层,skill=方法论,正交)。
- **orchestrator 降级**:bug_rca/graph.py docstring 标 post-pivot 参考 + CLI `bug-rca` deprecate 提示。
- **CLAUDE.md** 身份重写(调度型 → 领域 harness)+ 痛点② 新口径。

## e2e 实证(关键,2 轮)
- **e2e #1**(默认 agent + 自动发现 skill):机制成立(skill 加载、recall+filter、edit、产合理补丁),但 **advisory skill 没被严格走完** —— 跳了 search/blast/validate/memorize + 撞步数上限没出报告。补丁落点其实不错(dbus_new_handlers.c 误路由入口加守卫)。
- **e2e #2**(hyperion-bug-rca agent,硬门):**干净成功**。recall→search_codebase→blast(error=图没建,优雅)→edit→**validate_patch×2**(第1次 applies=False p2p_supplicant.c:2448 → agent 修正 → 第2次 ✅strict 通过)→**memory_memorize**(id=b448561a 入库)。**validate 硬门抓到真缺陷逼修正 = 门控有价值的实证**。根因 = abort 失败(ret=-2)时不释放 p2p_scan_work → radio work 永久泄漏(p2p_supplicant.c:2451-2452),带 file:line 证据链 + 证伪(**还 recall 出旧记忆 conf=0.35 "scan_res_handler 覆盖竞态" 并用日志证伪 = 先验→证伪闭环生效**)。
- ⚠️ **补丁/报告没落盘**:e2e 是机制验证跑,agent 只 edit 代码(已清回 demo2)+ 聊天回复报告;没写 .patch/.md。gap 见下。

## 下轮(用户定:搁着,连同 P-A 一起)
1. **✅ 落盘补丁硬门(2026-08-06 done,`ab11d56` 已 push)**:不做原计划的 bash 步,改成**第 7 个 MCP 工具 `hyperion_export_patch`**(调研:文字指令 soft,纯 bash 会静默吞空 diff;工具层更硬 + 对称 validate_patch)。git add -A && diff --cached → `data/bug_rca/<repo>.patch`,空 diff 自检。skill 七→**八步**、2→**3 硬门**(validate/export/memorize)。**e2e #3 绿**:opencode+hyperion-bug-rca 跑 demo2,**7 工具全原生触发**(recall→search→filter→blast→validate×2→**export_patch 落盘 wpa.patch 33 行**→memorize id=4f739d5a),agent 自述"硬门⑦过→⑧memorize"。**踩坑 #10**:opencode 1.18.11 http MCP 不注册原生工具(agent 绕 curl)→ 用 **local stdio**(timeout 提到 120s 防首次冷启)。详见 [[opencode-mcp-wiring]]。

   **✅ 报告落盘硬门(2026-08-06 done,`92b5df4` 未 push)**:用户指出 ① 只落了补丁、报告仍只在聊天里(被标"开放 gap"挂着,不该 —— 报告跟补丁同等是交付物)→ 补第 8 个 MCP 工具 `hyperion_export_report(content, repo_path)`(agent 把报告 markdown 作参数传入 → 写 `data/bug_rca/<repo>-rca.md` + 空内容自检)。**跟 export_patch 对称**:补丁内容 git 生成(工具自己 diff),报告内容 agent 生成(传 content);排 memorize 之后(含 memorize id,最终交付物)。skill 八→**九步**、3→**4 硬门**;+2 单测(空内容拒写 / 写文件逐字一致);02-bug-rca.md 报告 gap 关闭。
2. **P-A patch 分析**:`hyperion_build_check`(Tier 0.5 编译门)+ `patch-rca` skill + GitHub httpx 抓取 + auto-clone(`services/repos/resolver.py`)+ Gerrit stub(`PatchFetcher` ABC)。PatchIngestPipeline 补 symptom + pr_meta。完整设计 `docs/设计/harness-v2/03-patch-analysis.md`。
3. feature 2(调用链/跨版本)= 同 tool+skill 形,更后。

关联:[[skill-design-decision]] [[pitfall-log]] #2/#7/#8/#9 [[delegate-already-localizes]] [[avoid-overengineering]] [[similar-bug-recall-roadmap]](recall→证伪闭环实证)。
