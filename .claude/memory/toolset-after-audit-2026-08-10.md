---
name: toolset-after-audit-2026-08-10
description: 2026-08-10 全工具审核后 Hyperion 工具集现状(9 MCP 工具;撤了 filter_logs/build_check/patch_search + demo @tool 链路)
metadata: 
  node_type: memory
  type: project
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-08-10T07:18:51.433Z
---

2026-08-10 对 Hyperion 全部工具做了同标准审核。**判据**:opencode/omp 能做 + 灵活判断类 → 撤;确定性硬门 / 差异化能力(opencode 做不到)→ 留。两批撤:

**第一批(commit `6cc6ea4`,已 push origin/main)**:撤 bug-rca 的 `filter_logs`(切片是取证,opencode 的 grep/awk 等价,领域知识转 SKILL/prompt;deer-flow/omp 双证均无专门日志切片工具)+ `build_check`(与"不编译"方针冲突 + opencode 能 make,踩坑#14)+ `patch_search`(并入 `memory_recall` 的 `kind` 参数)。

**第二批**:撤 harness 转向前"Hyperion 自己跑 ReAct demo agent"的老链路(转向后 0 主路径消费者):`tools/sandbox.py`(6 @tool)+ `tools/code_nav.py`(6 @tool;`_retrieval_bundle` 内联到 mcp_memory)+ `tools/memory.py`(2 @tool,和 MCP 重复)+ `tools/registry.py` + `platform/agent.py`(demo)+ CLI `hyperion run`/`hyperion tools` + `config.yaml tools:` 段 + `ToolConfig`/`AppConfig.tools` + delegate 占位(OmpDelegate/ClaudeDelegate)+ pr_tracker docstring。底层 `services/*` + `platform/sandbox`(LocalSandbox)+ test_sandbox_local 全保留。

**现状 = 9 个 MCP 工具**([mcp_memory.py](src/hyperion/tools/mcp_memory.py)):6 差异化核心(memory_recall/memorize/search_codebase/blast_radius/fetch_patch/ensure_repo)+ 3 确定性硬门(validate_patch/export_patch/export_report,治踩坑#15/空交付)。

**留(主用)**:CLI 基建(`mcp serve`/`index`/`lsp`/`memory`/`models`)+ `research`/`patch-report` workflow(差异化:记忆沉淀 + CRG + 批量,不像 bug-rca 转 opencode)+ `create_hyperion_agent`(deep_research 子 agent)+ `services/*` 底座。**降级参考**:`workflows/bug_rca` 老 orchestrator + delegate(CodingAgentDelegate/OpencodeDelegate,CLAUDE.md 定留参考)。

判据见踩坑#11(撤销段)+ [[harness-route-review-2026-08-07]]。下次加工具前先问「opencode 能否自己做 + 是灵活判断还是硬门」。区别于 [[delegate-already-localizes]](那是 bug-rca 不重造定位漏斗;本条是全工具面审核)。
