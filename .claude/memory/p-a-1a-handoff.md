---
name: p-a-1a-handoff
description: "P-A 阶段1a(补丁/PR 鉴定 = patch-review tool+skill)完成交接;3 新工具+2 新 service+memorize 升级;e2e 通;5 个 e2e/review 暴露的问题全修;踩坑 #14/#15/#16"
metadata: 
  node_type: memory
  type: project
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-08-07T07:12:07.393Z
---

**2026-08-07 P-A 阶段 1a 完成**(post-pivot 第二条 tool+skill 线;bug-RCA 主体已闭环后的新主线)。补丁/PR 鉴定 = `patch-review` skill + `hyperion-patch-review` agent + MCP 工具,跟 bug-RCA 一个形。

**交付**:
- config `patch:` 段(`patch.git` remotes/clone_dir + `patch.build` commands/timeout)+ AppConfig 模型。
- `services/repos/resolver.py`(ensure_repo auto-clone,按 config remotes;幂等)。
- `services/patch/fetcher.py`(PatchFetcher ABC + GitHubFetcher(httpx,token/重试) + GerritFetcher stub[1d 留接口])。
- `services/workspace/build.py`(build_check 核心:worktree 隔离 apply + 跑构建 + 进程组超时杀 + 失败归因)。
- memorize 扩参(fix_patch/symptom/blast/commit_sha/tags + patch-content id 去重,对齐 ingest.py)。
- 3 新 MCP 工具(fetch_patch/ensure_repo/build_check)注册 → 共 11 工具。
- `patch-review` SKILL.md + `hyperion-patch-review` agent。
- **42 测全绿 + ruff 干净**;e2e(opencode+flash)端到端通:validate_patch/recall/search_codebase/blast_radius 原生触发。

**5 个 e2e/review 暴露的问题全修**(用户拍板):
- #14 build_check 退出流程(系统软件构建信号歧义)→ 流程只到 apply,编译用户自验;工具保留按需可用。
- #15 validate_patch/build_check 入口 normalize(LF+末尾换行)—— agent rstrip 尾换行致 git apply "补丁损坏"误判。
- #16 patch-review agent `bash:deny`(只读鉴定;edit:deny 挡不住 bash `git apply` 改用户仓)。
- #1 memorize 推迟到用户验证后(对齐 bug-rca / 踩坑#12;鉴定未验证不 memorize)。
- #2 改名 patch-rca→patch-review(rca 是 misnomer)+ #3 SKILL description 精简为纯触发器。

**封顶**:apply 是硬门;build 工具在但不接入(信号歧义);不跑测试/不复现(永不做)。correctness 基于 apply+读码推理,顶 plausible,非 verified。

**关联**:[[harness-pivot-handoff]] [[bug-rca-skill-toolbox-hitl]] [[pitfall-log]] #14/#15/#16 [[memory-append-only-directive]]。完整设计 [03-patch-analysis.md](../../Desktop/Agent/Hyperion/docs/设计/harness-v2/03-patch-analysis.md);踩坑/演变见 docs/踩坑记录.md + docs/设计演变史.md。下一步:1b 批量聚合 / Gerrit(1d)。
