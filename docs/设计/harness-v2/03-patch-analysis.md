# 03 · P-A 补丁/PR 分析设计(harness 转向后)

> Reconcile:取代 `docs/设计/pr-review-design.md`(pre-pivot,把 P-A 设计成一个 workflow)。
> post-pivot:**单补丁/PR 分析 = tool + skill**(跟 bug-RCA 一致),不建 workflow;**批量聚合报告**(1b)走 batch 形。

## 用户需求(原始 4 条)
- **1a** 给补丁文件 / GitHub PR 链接 → 结合代码库源码分析(正确?作用?是否合入?)→ 存知识库。源码本地没有 → **自动 clone**(用户自定义 git 连接)。
- **1b** 对某代码库的**多个**补丁/PR 合入做总结分析 → 报告(质量/安全/功能方向的作用)。
- **1c** 补丁/PR 链接的**检索**(如"跟蓝牙连接有关的补丁")。
- **1d** 后续考虑 **Gerrit** PR(先不实现,留可扩展性)。

## 形态总览(全 tool+skill,apply+build 封顶)

| 子功能 | 形态 | 阶段 |
|---|---|---|
| 1a 单补丁/PR 分析 | `patch-review` skill + `hyperion-patch-review` agent + 共享工具 + `build_check` 新工具 | **1** |
| 1c 检索 | `hyperion_memory_recall`(已有) | 1 尾(白捡) |
| 1d Gerrit | `PatchFetcher` ABC stub | 1(留接口) |
| 1b 批量聚合报告 | `patch-report` skill + 聚合工具(batch,复用 deep_research cited-reporter) | 2 |

---

## 1a · 单补丁/PR 分析(阶段 1)

### 流程(patch-review skill 7 步;apply 硬门 + memorize 推迟到用户验证后)
1. **取补丁**:本地 `.patch`/`.diff` 文件 → 直接读;URL(GitHub PR)→ `fetcher` 抓 diff + meta。
2. **(若需)取代码库**:repo 本地有 → 用;没有 → `ensure_repo` auto-clone 到 `data/repos/<name>/`。
3. **读补丁 + 上下文**:agent 用 `search_codebase` 找补丁涉及的代码(语义搜,别盲读全文)。
4. **apply 门【硬门】** `hyperion_validate_patch(补丁, repo_path)` —— Tier 0,能不能干净打上。
5. **不自动编译;提示用户**(2026-08-07 调整 + 2026-08-10 build_check 工具撤销)—— 系统软件构建环境重、依赖多,自动编译结果易歧义(e2e 实证:wpa 因无 git tag / 缺 libnl 等依赖 build 失败,`builds=no` 不归咎补丁)。patch-review 流程**不跑编译,只到 apply**;**明确提示用户必须自行编译测试**。(`hyperion_build_check` 工具 2026-08-10 撤销:与"不编译"方针冲突 + opencode bash 能 make,见踩坑#14。编译/修对全用户自验。)
6. **blast + LLM 鉴定** `hyperion_blast_radius(改动文件)` 看影响面;LLM 综合判(见决策卡)。
7. **用户验证通过后才 memorize**(2026-08-07 调整,对齐 bug-rca / 踩坑#12)—— 鉴定只是读码判断(没编译没测试),**未经验证不 memorize**;`hyperion_memory_memorize(kind=bug_lesson, fix_patch=<补丁>, ...)` 推迟到用户告知编译/真机验证通过后(可跨 session)。

### 决策卡(analyze 输出,correctness 封顶 builds)
```jsonc
{
  "applies": true,                  // Tier 0:validate_patch 过?
  "builds": "需用户自验",              // 不自动编译(系统软件构建留给用户);2026-08-07 起流程不跑 build
  "intent": "...",                  // 这补丁想干啥(作用)
  "blast_radius": {...},            // 影响面(blast_radius 工具)
  "correctness": "safe|needs-review|risky",  // 基于 apply + 读码推理(不报 verified/tested;编译由用户自验)
  "correctness_reason": "...",
  "merge_recommendation": "merge|review|reject|needs-info",  // 该不该合
  "confidence": "low|medium|high",
  "risks": ["..."],
  "notes": "..."                    // 诚实标注:plausible,非 verified
}
```

### 验证封顶(用户定:apply;build 工具在但暂不接入流程 —— 编译由用户自验;**不跑测试、不复现**)
| 级 | 干啥 | 状态 |
|---|---|---|
| Tier 0 apply | `git apply --check` 补丁能干净打上 | ✅ `hyperion_validate_patch`(已有,硬门) |
| **Tier 0.5 build** | 打上 + 跑构建(编译过) | 🔧 `hyperion_build_check` 工具**已实现 + 单测**,但**暂不接入 patch-review 流程**(系统软件构建信号歧义,e2e 实证);编译由用户自验。环境就绪可接回 |
| ~~Tier 1 跑测试~~ | FAIL_TO_PASS / PASS_TO_PASS | ❌ **永不做**(用户定) |
| ~~Tier 2 功能复现~~ | 复现原 bug 场景 → 确认消失 | ❌ **永不做** |

`correctness` 基于 **apply + 读码推理**;**不报 tested/verified**(没跑测试、没复现;build 也暂不接入,编译由用户自验)。诚实:这是"看着靠谱",非"包对"。理由:系统软件(wpa/bluez)构建/测试套件(hwsim 等)+ bug 复现环境太重,边际不值。

### 新件(阶段 1)
1. **`services/patch/fetcher.py`**(⏳ 阶段1新):`PatchFetcher` ABC(`async fetch(url) -> PatchArtifact{url,title,body,diff,merge_commit_sha,changed_files,source_kind}`)+ `GitHubFetcher`(httpx → GitHub REST API,`GET /repos/{o}/{r}/pulls/{n}` 两路:Accept v3.diff → diff,json → meta;token 从 `GITHUB_TOKEN` 可选;重试借 deer-flow github_api 模式)+ `GerritFetcher` stub(`raise NotImplementedError`,**1d 留接口**)+ `from_config()` 工厂(仿 delegate)。
   - **divergence 注记**:老 pr-review-design.md 提 `services/github/pr_fetcher.py`;改 `services/patch/fetcher.py` —— 因 Gerrit 非 GitHub,一处内聚。
2. **`services/repos/resolver.py`**(⏳ 阶段1新):`ensure_repo(name_or_path, *, cfg) -> Path` —— 先查本地(`data/repos/<name>/` 或显式路径);缺则 `git clone [--depth 1] <remote> <dest>`(subprocess,仿 `_observe_patch`)。幂等。config `git:` 段(`clone_dir: data/repos, shallow: true, remotes: {bluez: https://...}, identity_file: $ENV`)。**1a "自动 clone"**。
3. **`hyperion_build_check` 工具**([tools/mcp_memory.py](../../../src/hyperion/tools/mcp_memory.py) `build_server()`):补丁打 repo 副本 → 跑构建(自动认 Makefile→`make` / meson.build→`meson compile` / CMakeLists.txt→`cmake` / configure→`./configure && make`,或配置指定)→ `{builds, errors}`。**best-effort**:缺依赖/没构建环境 → 返 `builds=unchecked` + hint,不崩。lazy 导入 + try/except(仿 search_codebase)。
4. **`patch-review` SKILL.md**(`.claude/skills/patch-review/` ⏳):上面 7 步 playbook,apply/build/memorize 硬门。
5. **`hyperion-patch-review` opencode agent**([config/opencode_hyperion.json](../../../config/opencode_hyperion.json)):playbook 烙进 prompt + steps + 硬门强制(仿 hyperion-bug-rca)。
6. **memorize 升级**(小):patch lesson 一起存 `fix_patch`(补丁)/`symptom`/`blast_radius_files`/`commit_sha`。

### 复用
`validate_patch`([services/workspace/validate.py:15](../../../src/hyperion/services/workspace/validate.py#L15),Tier 0)、`blast_radius`(CodeGraph.impact_radius)、`memory_recall`;PatchIngestPipeline 的 `_summarize` 给 memorize 抽结构化 lesson(从补丁抽 根因/symptom/blast,[services/memory/ingest.py:240](../../../src/hyperion/services/memory/ingest.py#L240))。

---

## 1c · 补丁检索(阶段 1 尾,基本白捡)

需求:问"跟蓝牙连接有关的补丁" → 检索出对应补丁/PR。

**已有**:`hyperion_memory_recall`(recall 4 路:BM25+向量+code+structural)能按语义查 patch_insight/bug_lesson KI。1a 的 memorize 带 `symptom` + tags(`patch_insight`/`pr:`/`module:`)后,自然语言查询直接命中。

**可选增强**(若需要):`hyperion patch-search <query>` CLI 别名 + `hyperion_patch_search` MCP 工具(薄封 recall,限定 kind=bug_lesson + tags)。MVP 不做,recall 够用。

> ✅ **1c 已实现(2026-08-07)** → 🔄 **2026-08-10 撤销(并入 recall)**:`hyperion_patch_search` 原薄封 `recall` 过滤 `kind=bug_lesson`;全工具审核(同 filter_logs 标准)判定"可合并" —— 删 patch_search,kind 过滤做进 `memory_recall(kind=...)` 等价且少一个工具。原:query 驱动语义命中("跟蓝牙连接有关的补丁"),RecallHit 无 tags 字段故按 kind 过滤(覆盖 patch_insight)。CLI 别名仍 backlog。

---

## 1d · Gerrit(阶段 1,✅ 已实现 2026-08-07)

`GerritFetcher.fetch()` 已实现:URL 解析 `<host>/c/<proj>/+/<num>`;REST `GET /changes/?q=change:<num>&o=CURRENT_REVISION`(剥 Gerrit `)]}'` XSSI 前缀取 subject/revision sha/id)+ `GET /changes/<id>/revisions/current/patch`(base64 解码 → unified diff);changed_files 从 diff 抽(`_diff_changed_files`)。匿名读公开 change 可行;私有/限速的 Gerrit HTTP 凭据留 backlog。单测(MockTransport + XSSI 前缀 + base64)绿。

---

## 1b · 批量聚合报告(✅ 已实现 2026-08-07,batch workflow)

> **已落**:`workflows/patch_report/`(StateGraph,镜像 deep_research)+ CLI `hyperion patch-report`。
> pipeline:ingest→fetch_prs(并发 GitHubFetcher/GerritFetcher)→analyze(每 PR:validate_patch + CodeGraph.analyze_changes 风险 + SECURITY_KEYWORDS 安全分层 + cited-reporter LLM → PRFinding)→aggregate(确定性分桶统计 theme/tier/module + 一次 LLM cited 综合)→report(cited 渲染 + 轻量 Verifier file∈changed_files 回查)→memorize(聚合结论→codebase_fact)。
> e2e(wpa.patch 真 LLM + 真 CRG 图)GREEN:applies=True/risk=0.4/modules=[6653,6664]/cited summary 准确命中金标根因(scan_res_handler 误路由→p2p-scan 孤儿)+ aggregate 综合准。GitHub 批 fetch e2e 被匿名限速挡(单 fetch 已验)。light 模式(默认);deep ReAct 子集 + line 精确 Verifier 留 backlog。

需求:给一组 PR(某代码库这半年的全部 PR)→ 跨 PR 聚合 → 报告(哪些模块在烂/改动频繁、哪些是安全修复、整体质量/安全/功能趋势)。

**形态**:`patch-report` skill + 聚合工具(agent 驱动 per-PR 跑 1a + 聚合;或 batch workflow 形像 `hyperion research`)。复用 [deep_research](../../../src/hyperion/workflows/deep_research/) 的 cited-reporter + verify(零幻觉)。

**聚合维度**(老 pr-review-design.md 的设计,沿用):
- **按模块**(CRG `community_id`)+ **按主题**(security/function/refactor/perf)分桶。
- **安全分层**:`compute_risk_score`(含 `SECURITY_KEYWORDS` +0.20)+ keyword 预筛 → **只命中的子集**送 LLM 深 CWE 分类(introduce-vs-fix / taint path);其余只 graph-only(省 token)。
- **每桶 map-reduce 摘要** → `render_report`(cited `PR#:file:line`)+ `_verify_report_citations` 零误引硬门。
- 聚合结论抽成 `codebase_fact` 入记忆(P1→P3 loop)。

**CLI**:`hyperion patch-report --urls <file|-> --repo <path> --codebase <name> [--deep]`(仿 `cmd_research`)。

> 1b 是唯一保留 batch 形的子功能(产报告 = 像 deep_research 的批量管线);单补丁分析(1a)坚持 tool+skill,不混。
