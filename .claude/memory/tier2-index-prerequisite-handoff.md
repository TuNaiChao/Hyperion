---
name: tier2-index-prerequisite-handoff
description: Tier2 #5「hyperion index 前置门槛」完成(2026-08-11)——index 一键建向量+结构图 + blast_radius 路径容错;bluez 真机验证 4 工具全通
metadata:
  type: project
---

**2026-08-11 Tier 2 #5 完成,代码完未 commit**(用户给 `code-test/v20/v25` 两个版本 bluez 5.50/5.85 作真机素材)。两处改 + bluez 实测验证 + code-test gitignore。36 测绿、ruff 全项目干净。

## 痛点(为什么做)

代码情报工具分两套预建依赖,但只暴露了一个建法:
- **向量索引** → search_codebase
- **结构图(CRG graph.db)** → blast_radius / call_chain / repo_map
- (仅 git,不预建)→ cross_version_diff / merge_eval

`hyperion index` 只建向量索引,**没有 CLI 建结构图**;而 blast_radius/call_chain/repo_map 降级提示却写「先建 `hyperion index`」——**误导死路**:照做后图仍缺,照旧 FileNotFoundError。任意新仓(正是 bluez 场景)一上手就卡。这正是 Tier1 分析标的「实操断点」。

## 改动 1:`hyperion index` 一键建两者([cli.py](../../src/hyperion/cli.py) cmd_index)

向量索引建完后,接着 `CodeGraph.build(repo_root, repo_name)`:
- ~~已建图且非 `--force` → 跳过~~ **2026-08-18 已升级为增量刷新**(见 [[colleague-onboarding-toolset-handoff]] 的 D):已建图重跑走 `CodeGraph.update()`(built_head 快照 + CRG incremental_update,只重解析改动文件),不再静默跳过;
- `--force` → 先 `shutil.rmtree` 旧图目录再重建(免 stale 节点);
- **CRG extra 没装(ImportError)/ 建图失败 → 非致命降级**:打提示 + 不挡向量索引(search_codebase 仍可用),结构图工具各自降级提示。
- 新 `--no-graph` flag(只建向量索引,快)。argparse help / docstring 都改成「建两者」。

降级提示「先建 hyperion index」现在**成真**了。

## 改动 2:blast_radius 路径容错([code_graph.py](../../src/hyperion/services/code_index/code_graph.py) CodeGraph.impact_radius + `_resolve_file_paths`)

**bluez 实测活捉的真 bug**:index 用相对 repo_root(`code-test/v25/bluez`)→ CRG 存路径带前缀(`code-test/v25/bluez/src/shared/gatt-client.c`);而 agent / search_codebase / git diff 都给仓库相对路径(`src/shared/gatt-client.c`)→ `get_impact_radius`→`get_nodes_by_file` **精确匹配落空 → blast_radius 静默返 0**(5744 变 0)。同 route5 的 repo_map 渲染剥前缀,但 blast 的**匹配**没剥,更阴(静默空,非渲染乱)。

修法:impact_radius 先 `_resolve_file_paths` 把输入解析成图里真存的路径再喂下层 —— 精确命中 > `endswith("/"+f)` 后缀兜底(剥 repo_root 前缀;绝对/相对/prefixed 三种都收敛)> 解析不到原样喂(如实返空,不假装)。多义(短名撞多文件)→ 全收(宁多勿漏,blast 面本该宽)。`get_all_files()` 一次取清单(~700 文件,一次 SQL DISTINCT,便宜)。

## bluez 真机验证(`hyperion index code-test/v25/bluez bluez`)

- **向量索引 [full]:18790 chunk**(commit e81b6b9),194M;DashScope text-embedding-v4,大仓约 8 分钟(13k+ chunk 批嵌)。
- **结构图建好**:data/structgraph/bluez/graph.db 133M;CRG full_build fork 并行解析(有 lancedb fork experimental RuntimeWarning,**无害**)。
- **探针 4 工具全通**:
  - search_codebase:5 hit,准(`gatt-db.c:263 service_clone` / `gatt-client.c` use-after-free 区,score 0.93);
  - call_chain:`bt_bap_attach` 4 callers(file:line+pagerank);
  - repo_map:1024 预算塞 83 符号(budget 限,非图小);
  - blast_radius:`src/shared/gatt-client.c`(相对)→ **改前 0 / 改后 175 changed · 5744 impacted**;`gatt-db.c`→4542。

## 其他

- **`code-test/` gitignore**:54M 第三方 bluez 源码树不入库(example/ 同惯例);ruff 默认 respect-gitignore → 顺带消了 107 个 bluez 自带文件的 lint 噪声(sap_client.py 等)。
- 测:加 `test_impact_radius_path_resolution`(相对/精确/不存在三态);`changed_nodes` 是 GraphNode 对象用属性访问非 `.get()`。36 测绿(跳 kind_filter 真网络测)。
- **gotcha**:`uv run` 后台跑大仓 index OK;`hyperion index` 是「建两者」入口,旧文档/记忆若说「index 只建向量」已过时。

## 不做(YAGNI)

- 不给结构图单独 CLI(`graph` 子命令)——index 一键够了,加命令反增摩擦;`--no-graph` 是逃生口。
- 不在 index 时改 CRG 存储格式(统一存仓库相对路径)——会动 wpa 旧图 + 多消费者,风险大;路径容错在查询侧(impact_radius)解决更局部。

关联 [[toolset-after-audit-2026-08-10]] [[route5-repomap-handoff]](CRG 绝对路径同源) [[pitfall-log]] [[multi-codebase-per-call-handoff]] [[avoid-overengineering]]。Tier2 #4(P-A 遗留:patch-report deep+去重+Gerrit)未做,是下一块。
