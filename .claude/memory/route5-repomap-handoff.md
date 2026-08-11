---
name: route5-repomap-handoff
description: "2026-08-11 #38 repo-map 完成(第 12 个 MCP 工具):Aider repomap 式全仓 PageRank 符号地图。B 路线(复用 CRG 图 + _pagerank,不抄 tags.scm)。探针 wpa 真图 149 符号塞 2048 预算。修 2 bug:绝对路径压缩 + token 估算用显示名。"
metadata:
  node_type: memory
  type: project
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-08-11T00:00:00Z
---

**2026-08-11 backlog #38 完成,第 12 个 MCP 工具 `repo_map`**(CLAUDE.md 核心顺序 #1-#4 之后的代码情报延伸)。对标 Aider repomap:给 agent 一张**按 PageRank 排名、塞进 token 预算**的全仓符号地图 —— bug-RCA 委托前给 delegate 全局视角 / 深度调研「关键模块」骨架。

**设计 = B 路线(用户拍板,偏离 backlog 原文)**:backlog 原写「新建 repomap.py + 抄 tags.scm + 补 c.tags.scm」(Aider 原教旨)。精读现有代码后发现**不需要** ——
- CRG 已对 C/Python 用 tree-sitter 抽好 CALLS 边并存进结构图;
- Hyperion 已有 `_pagerank` + `_pagerank_python_pure`(route2 加,纯 python 幂迭代降级不 OOM);
- `CodeGraph.call_chain` 已用 `_build_networkx_graph()` 拿整图 + CALLS 子图 + `_pagerank`(种子邻域版)。

故 `CodeGraph.repo_map()` 把「种子邻域 PageRank」换成「**整图** PageRank」+ 加渲染:**零新依赖、零 tags.scm 维护**。重抄 tags.scm = 重复造边抽取,撞踩坑#2「委托型别重造已有能力」精神(对标 [[delegate-already-localizes]])。代价:CALLS 边比 Aider def-ref 边窄(只调用,不含类成员引用),但 CALLS 是高信号子集且 CRG 已验证。

**实现**(`src/hyperion/services/code_index/code_graph.py`):
- 模块级 `_render_repomap_tree(files, meta, scores)`:按文件分组 + 文件/楼内双降序 + ├──/└── 树。
- `CodeGraph.repo_map(*, map_tokens=2048)`:整图 CALLS 子图 → `_pagerank` → 降序 → `_batch_get_nodes` 富化 → 贪心填 token 预算 → 渲染。返 `{repo, map_text, n_symbols, n_files, map_tokens_budget/used, truncated, top_symbols, note}`。
- MCP 工具壳 `repo_map(map_tokens, codebase)`(mcp_memory.py,镜像 call_chain 壳:open→FileNotFoundError/ImportError 降级);docstring 工具数 11→12 + 清单 + per-call codebase 列表同步。

**和已有工具分工**(防重叠):`hub_nodes`=度数 top-15 扁平;`repo_map`=PageRank centrality + token 预算(~100+ 符号)+ 文件分组树;`architecture_overview`=社区/模块结构。三者互补。

**探针实证(wpa 真图,data/structgraph/wpa)**:149 符号 / 69 文件 塞进 2048 预算(tokens_used=2036/2048);top = wpa_cli_cmd / wpa_ctrl_command / _wpa_ctrl_command / send_and_recv_msgs(结构核心,合理 —— dispatch + nl80211 收发原语)。绝对路径压缩后输出干净:`wpa_supplicant/wpa_cli.c` + `wpa_cli_cmd (Class) L3831 pr=0.003`。

**修了 2 个真 bug(探针活捉)**:
1. **CRG file_path/qualified_name 存绝对路径** → 直接渲染全图被 `/home/tnc/...` 前缀淹没。修:渲染剥「全仓公共路径前缀」(os.path.commonpath)显示相对路径 + 符号行剥 `<path>::` 前缀只留 `Class::symbol`。
2. **token 估算用全长 qn(含 abs path)→ 虚高 ~2.5x 早停**(67 符号)→ 改用「显示名」(剥路径前缀)估算 + 新文件加表头成本 → packing 67→**149 符号**(同预算 2.2x)。配套:渲染器加空 files 守卫(小预算一个都装不下时 `paths[0]` 曾 IndexError)。

**验证**:30 测绿(test_code_graph 3 新:渲染器单元测 + 小仓集成 + 空图降级;test_mcp_tools 2 新:未建图降级 + 假图 happy-path);ruff 干净;wpa 探针实证排名合理。`Any` import 补到 code_graph.py(repo_map 元组注解用)。

**backlog(记本记忆)**:① 函数签名富化(parse_repo 拿 signature 渲 `def foo(args)` 骨架,要解 CRG `file::func` ↔ parser `Class.method` 格式匹配,先不碰);② CLI 子命令(call_chain/cross_version/blast 全纯 MCP 无 CLI,repo_map 对齐);③ Aider 式「每符号标谁引用它」(超预算,用 call_chain 查);④ 非 CRG 降级(没图提示先 index,同 blast/call_chain,不重造)。

关联:[[route2-call-chain-handoff]](同 _pagerank/整图 积木)、[[route3-cross-version-handoff]]、[[align-to-deerflow-production-grade]]、[[avoid-overengineering]](B 路线 = 用户复杂度直觉对)。
