---
name: workspace-design-decision
description: bug-RCA 每 bug 一个 workspace 目录设计(七段)+ 本地默认/Docker R5 + 日志分层预筛 + 复用 deer-flow sandbox + 补丁 6 步验证
metadata: 
  node_type: memory
  type: project
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-07-31T03:13:14.908Z
---

2026-07-29 定稿:bug-RCA 采用「**每 bug 一个专用 workspace 目录**」(`<repo>__<bug-id>__<hash6>/`,七段:`code/`(全量 git checkout)+ `triggers/`(issue/logs/poc)+ `delegate/`(prompt/context/delegate_log)+ `artifacts/`(candidate_patches/validate)+ `patch/final.diff` + `report/` + `docs/`汇总),opencode `--dir` 在此跑(读全量代码+日志,非内联片段)。完整设计见 [docs/设计/workspace-design.md](../../../../Desktop/Agent/Hyperion/docs/设计/workspace-design.md)。

**三要点:**
1. **隔离**:默认本地目录(R2/R3),Docker 作 R5 可选。抽象复用 deer-flow `Sandbox`/`SandboxProvider` ABC + `LocalSandbox` + `env_policy`(scrub key)+ `workspace_changes`(scanner/diff 生成 patch)。
2. **大日志分层预筛**:Hyperion 粗筛(grep 关键字 + 时间窗 + addr2line 符号化 + 堆栈折叠 + LLM 摘要 → `delegate/context.md`)+ opencode 自主 grep 深挖。和代码 localize 同模式(Hyperion 给起点、delegate 深挖)。
3. **补丁 6 步验证**(SWE-bench/Agentless 标准):clean checkout → `git apply --check` → revert → build → FAIL_TO_PASS/PASS_TO_PASS(~~多候选 rerank~~ 已于 2026-07-31 移除)。quilt 场景转 `debian/patches/`。

**Why:** 解 R2 内联 prompt 三痛点(opencode 被动读文本非 agent / 补丁基于快照易错位打不上 / 大日志没法结合)+ 补丁要能 quilt apply。对标 Agentless(定位→修复→验证)+ deer-flow per-thread per-user sandbox + SWE-bench(每实例一容器)。
**How to apply:** 用户 2026-07-29 确认**方案 A + 方式 B 都上**(两个正交优化,互补)。
- **方式 B**(R2 末最简 / R3):assemble 不内联锚点代码、改给「file:line 起点 + why」指引;delegate cwd=workspace,opencode 自读全量 code+logs。省 token 75% + 发挥 opencode agent 能力 + 补丁基于真实文件可 apply(解 off-by-one)。
- **方案 A**(R3,随 trigger_parser/关键字 #53):localize file-level 从「喂整棵目录树给 LLM 选」改「关键字 → code_index BM25/embedding 检索 top-20 → LLM rerank」。解 flash localize 不稳/漏锚点(如这次漏 events.c:1813 金标准根因)。
- 落地:**R2 末**最简(workspace + AGENTS.md + 方式 B,可能同时解 delegate 慢);**R3** 完整七段 + log_preprocess + **方案 A** + 补丁验证 6 步 + services/workspace/;**R5** Docker(`AioSandboxProvider`)。关联 [[backlog-production-grade]]、[[agent-project-overview]]、[[align-to-deerflow-production-grade]]、[[research-deerflow-first]]。

**⚠️ 安全(即时):** 本机 `~/.config/opencode/opencode.json` 明文存了 uniontech-ai apiKey;跨机/dotfiles 同步前必须改 `"apiKey": "{env:UNIONTECH_AI_API_KEY}"`,key 放 `~/.zshrc`/`.env`(gitignore)。delegate 子进程用 deer-flow `env_policy` scrub 防 key 泄到 trace。
