---
name: multi-codebase-per-call-handoff
description: "2026-08-11 多库地基收尾完成 —— per-call codebase 推广到 4 个 MCP 工具;数据层早就绪,只改工具层。"
metadata: 
  node_type: memory
  type: project
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-08-11T01:17:44.200Z
---

**2026-08-11 路线 #1「多库地基」收尾完成。**

核实结论(开工前查实):数据层**早就就绪** —— ① code_index 严格 table-per-repo(`store.py` `_repo_dir` → `data/code_index/<repo>/lancedb/`,`retrieve(query, repo, ...)` 按 repo 路由);③ 记忆有 `codebase` 列 + `idx_ki_scope` 索引 + `Scope(owner,codebase)` 全程过滤(`make_id` 按命名空间)。**唯一 gap 全在 MCP 工具调用层**:9 工具里只有 `blast_radius` 支持 per-call 切仓,其余 3 个检索/记忆工具靠 server 启动时 `_resolve_codebase` 烘焙的单一闭包 codebase(一进程一仓)。

**改动**(`src/hyperion/tools/mcp_memory.py`):把 `blast_radius` 的 `active = codebase or repo` 模板推广到 `memory_recall` / `memory_memorize` / `search_codebase` —— 各加可选 `codebase: str | None = None`,每次调用构 `active_scope = Scope(owner="default", codebase=codebase or repo)` 覆盖闭包默认。顺带清掉变 dead 的闭包 `scope` 变量(ruff 抓出)。数据层零改动。`memorize` 返回串补回显 `codebase=`。

**验证**:FastMCP schema 4 工具都暴露 `codebase`(required=False,向后兼容);`tests/test_mcp_tools.py` 加 3 个 per-call 测(策略同 `test_blast_radius_not_built`:传不存在 codebase 断言返回串含该名;recall/memorize 用 `_FakeMemSvc` monkeypatch `get_memory_service` 绕开真 db/网络);13 测绿(10 旧非网络 + 3 新),ruff 干净。文档 `docs/docs/tools/mcp-tools.md` 更新(4 工具标 🔀 + per-call NOTE)。

**Why / How to apply:** 多库不需要跨仓 union,per-call 选仓就够(同 opencode 会话切多仓)。下一步解锁路线 #2(feature 2a 调用链 `call_chain`,CRG 多跳+PageRank)。关联 [[harness-route-review-2026-08-07]]、[[toolset-after-audit-2026-08-10]]。

**⚠️ 测试 gotcha(非本轮引入,但踩到了):** `tests/test_mcp_tools.py::test_memory_recall_kind_filter` 是**既有**测试,调真 `svc.recall` → 真 `embedder.embed_query`(DashScope 网络);无 API 访问的环境会**挂起**(>60s 被 timeout 杀)。跑 mcp 测试用 `uv run pytest tests/test_mcp_tools.py -k "not kind_filter"` 跳过它;新加的 recall/memorize 测用 `_FakeMemSvc` 不碰网络,是正确范式。
