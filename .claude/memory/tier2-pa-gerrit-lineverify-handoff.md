---
name: tier2-pa-gerrit-lineverify-handoff
description: Tier2 #4「P-A 遗留」第 1 档完成(2026-08-11)——Gerrit 私仓鉴权(/a/+Basic)+ URL 分流 + 报告行锚定验证;--deep/语义去重缓
metadata:
  type: project
---

**2026-08-11 Tier 2 #4(P-A 遗留)第 1 档完成,代码完未 commit**。用户拍板做第 1 档「Gerrit 修 + 验证加强」,重型 `--deep` 逐 PR ReAct + 跨 PR 语义去重缓(YAGNI)。27 测绿(7 新)+ bluez 真补丁探针三态全对 + ruff 全项目干净 + 231 测全绿回归。

## 痛点(三个真 bug,Explore agent 核实)

1. **Gerrit 无鉴权(Gap A)**:[fetcher.py](../../src/hyperion/services/patch/fetcher.py) `GerritFetcher.__init__` 只收 timeout/retries/transport → 私有/限速 Gerrit 403。注释自认 backlog。
2. **Gerrit URL 静默失败(Gap B)**:`from_config()` 永远返 `GitHubFetcher()`;`node_fetch_prs` 拿它抓所有 URL → Gerrit URL 不匹配 `_GH_PR_RE` 抛 ValueError → 被 try/except 静默吞掉(抓不到还不报错)。
3. **报告只查文件级**:[report.py](../../src/hyperion/workflows/patch_report/report.py) `verify_and_append` 只查 citation.file ∈ changed_files(防编造文件);citation.line 对不对得上 diff hunk 没查 → AI 引错行号能蒙混。

## 改动 1:GerritFetcher 鉴权(Gap A)

`GerritFetcher.__init__` 加 `username`/`http_password` 参数,**默认从 env 读**(`GERRIT_USERNAME`/`GERRIT_HTTP_PASSWORD`,对齐 GitHubFetcher 读 `GITHUB_TOKEN` 的惯例)。两者都给才算「有凭据」(`self.authed`)。`fetch`:有凭据 → 端点走 `/a/` 前缀(`https://{host}/a/changes/...`)+ `httpx.BasicAuth`;无凭据 → 匿名 `/changes/`(行为不变)。XSSI 剥离 + base64 解码**两种格式都一样**(`/a/` 下同样带 `)]}'` 前缀 + base64),故不动。
**⚠️关键**:Gerrit 的 "HTTP password" **不是账户登录密码**,是 Settings → HTTP Credentials 里专门生成的 token。v1 走 env,不进 config.yaml(对齐 GITHUB_TOKEN;将来要私仓集中配再加 `patch.gerrit.{username,http_password}`)。

## 改动 2:URL 分流(Gap B)

新增模块级 `fetcher_for_url(url, cfg=None)`:URL 命中 `GerritFetcher._GERRIT_RE` → `GerritFetcher()`(自动带 env 凭据);否则 `GitHubFetcher()`。[nodes.py](../../src/hyperion/workflows/patch_report/nodes.py) `node_fetch_prs._one` 改成按 URL 各取 fetcher(每条 URL 独立实例)。`from_config()` 保留作向后兼容(返 GitHubFetcher 默认,docstring 标注不按 URL 分流的坑)。

## 改动 3:报告行锚定软查

- [fetcher.py](../../src/hyperion/services/patch/fetcher.py) 新增 `diff_hunk_lines(diff_text) -> dict[file, list[(lo,hi)]]`:解析 `@@ -a,b +c,d @@` 取**新文件侧行区间** `[c, c+d-1]`(d 省略=1;length 0 纯删除 hunk 跳过),按 `+++ b/<file>` 归文件(`/dev/null` 跳过)。跟 `_diff_changed_files` 同样的剥前缀。co-locate diff 解析。
- [report.py](../../src/hyperion/workflows/patch_report/report.py) `verify_and_append`:从 `state["artifacts"]` 各 `PatchArtifact.diff` 汇总 `hunks`。**两层独立指标**:
  - **硬(file 通过率)**:citation.file 不在任何 changed_files →「可疑」(可能编造文件)。
  - **软(行锚定率)**:文件通过 + 有数字 line + 该文件解到 hunk → 看 line ∈ 某区间。锚到 = 真实改动;否则「未锚定」(可能引上下文/引错)。**只提示不删**——合法引上下文也可能未锚定,不能冤枉。
  - 分母只计「文件通过 + 有数字 line + 解到 hunk」的 citation(file 没过 / 没给 line / 无 hunk 都不计)。透明:总追加 Verifier 段,不静默删改。

## bluez 真数据探针(`/tmp/probe_bluez_lineverify.py`,非提交)

用真 bluez 安全补丁 `e81b6b9`(PBAP 堆溢出 + gatt-client UAF;实际修复在 `debian/patches/...patch` 文件内,含真 hunk)当 artifact diff。`diff_hunk_lines` 解出 `obexd/client/pbap.c: [(330,337)]` + `src/gatt-client.c: [(2261,2268)]`(对)。三种 citation 三态全对:
- A) `pbap.c:331` ∈ [330,337] → **锚定** ✓
- B) `gatt-client.c:9999`(文件对、行号离 hunk 万里)→ **未锚定** ✓
- C) `src/foo.c`(编造)→ **可疑** ✓
- file 通过率 2/3、行锚定率 1/2(分母不含 foo.c,因 file 没过)。

## 其他

- 测:[test_fetcher.py](../../tests/services/patch/test_fetcher.py) +5(`test_diff_hunk_lines_helper` + Gerrit auth 3:有凭据 /a/+Basic、匿名回归、env 读凭据;`_gerrit_handler` 共用 record URL/auth;+ `test_fetcher_for_url_dispatches` 含 project 含斜杠);[test_aggregate.py](../../tests/workflows/patch_report/test_aggregate.py) +3 行锚定(在 hunk ✅ / 出 hunk 未锚定 / 无 artifacts 不查行)。**test 小坑**:断言「未锚定 not in md」会误中 anchor 率说明文字(「引上下文也可能未锚定」)→ 改匹配段头 `"未锚定(文件对"`。
- gotcha:旧 verify 测不传 `artifacts` → 无 hunk → 行锚定分母 0 → 不报率、不算未锚定(回归旧 file-only 行为,故旧测不破)。
- `--deep` flag 仍端到端接线(cli→state→analyze→`_analyze_one_pr(..., deep=deep)`),但 deep 参数下游**仍不用** —— documented stretch no-op(不引重型 per-PR ReAct;pivot 倾向把深活委托 coding agent)。

## 故意不做(YAGNI)

- **--deep 逐 PR ReAct 深审**:pivot 倾向委托、贵;flag 保留为 stretch no-op。
- **跨 PR 语义去重**:`_aggregate._same_subject` 结构去重(theme 同 + 文件 Jaccard ≥0.5)已覆盖同文件重复;LLM 语义去重业界开放难题,aggregate 那次 LLM 综合已能口头提重复,不另建机关。
- **Gerrit 凭据进 config.yaml**:v1 走 env(对齐 GITHUB_TOKEN);私仓集中配留将来。

关联 [[p-a-1a-handoff]](P-A 全线) [[pitfall-log]](踩坑#13 skill/prompt 面向模型) [[avoid-overengineering]] [[tier2-index-prerequisite-handoff]](Tier2 #5)。CLAUDE.md 低优 backlog P-A 遗留标「Gerrit+行级验证已成;--deep/语义去重缓」。
