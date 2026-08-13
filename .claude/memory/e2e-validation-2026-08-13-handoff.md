---
name: e2e-validation-2026-08-13-handoff
description: "onboarding + memory-health 两 skill 真机 e2e —— memory-health 抓到 2 真 bug(id 不渲染 / 前缀不匹配 → 纠正链闭环走不通),已修+真 DB 重放验证;onboarding 全绿但记两渣症"
metadata:
  type: project
---

**2026-08-13**:architecture-review 七项全落(建议 A/B/C/D + 功能1/2 + correction-link)后,可建队列空了,转「验证优先」—— 跑两个待验 skill 的真机 e2e(onboarding + memory-health-check,都用 wpa 真索引 + 48 条记忆库)。**memory-health e2e 抓到 correction-link 的两个连锁 bug,已修 + 真 DB 重放验证闭环通;onboarding 全绿但暴露两渣症(记 backlog)。**

## 为什么做(背景)

architecture-review 的 7 项全 `[x]`,backlog #1-64 剩的全是触发式(merge-tree/C 切分/ONNX,挂前置条件)。历史规律:skill 真机 e2e 是每次抓真 bug 的地方 —— compare e2e 抓「记住了还重跑」注入层 gap、memory-health 首轮抓 dump 静默截断。**onboarding + correction-link 两个 skill 还是「待真机跑」状态**,是当前杠杆最高动作。详见 [[memory-health-check-handoff]] [[correction-link-handoff]] [[onboarding-skill-handoff]]。

## 方法

- **底座预检**(不经 opencode,fastmcp Client 直调):`repo_overview`(onboarding 主数据源,要图)+ `memory_dump`(memory-health 主数据源)在 wpa 上直调绿 → 排除「图/DB 层就坏」的低级问题,避免 e2e 跑半天发现底座坏。
- **opencode e2e**:`set -a; . ./.env; set +a`(opencode 不读 .env,踩坑[[opencode-mcp-wiring]])+ `opencode run --agent hyperion-<name> --format json`(json 事件流)+ 给仓库绝对路径(踩坑[[compare-skill-handoff]] #2:gitignored 仓 glob 看不见)。
- **验证不只看 agent 自述**(踩坑#20):DB raw 查实证 —— memorize 的条目真在 DB、corrected_by 真回填、不是 agent 幻觉。

## memory-health e2e:抓到 2 真 bug(纠正链闭环走不通)

### 首轮:agent 分析全对,但闭环卡死

首轮 e2e(16 steps)agent **体检分析完全正确**:37 条全量摊开、四类信号聚出(溯源弱 3 / 待巩固 7 / STALE 11)、矛盾**正确判定「可闭环」**(B派 summary 明言「纠正先前 abort-failure 误诊」+ 时序铁证:泄漏 10:12:12 早于 abort 失败 10:12:19,证伪 A派)。**但 `memory_memorize(corrects=[A派id])` 没执行成功** —— 被 16 步上限截停,agent 自述「dump/recall 输出里拿不到被纠正条目 id,被逼去 grep SQLite WAL(二进制无匹配)」。agent 自己给了精准诊断。

### bug 1:id 不渲染(契约级缺口)

**实证**(踩坑#20 不轻信 agent 自述,读码):`_render_audit_card`([mcp_memory.py:92](../../src/hyperion/tools/mcp_memory.py#L92))渲染 kind/summary/loc/conf/tier/sha/hits/STALE 但**无 id**;`RecallHit.render()`([schema.py:247](../../src/hyperion/services/memory/schema.py#L247))同样无 `item_id`(虽然 RecallHit 有该字段,line 225)。**而 `memory_memorize` 的 docstring**([mcp_memory.py:198-199](../../src/hyperion/tools/mcp_memory.py#L198))明写「Pass the IDs you saw in `memory_recall` or `memory_dump` output」—— **承诺了拿不到的东西**。

**修**:两处渲染加 `id=xxxxxxxx`(截断 8 位,与 sha/CORRECTED 同款对称);code/structural 路 item_id=None 不渲染(避免 `id=None` 噪声)。
- [mcp_memory.py `_render_audit_card`](../../src/hyperion/tools/mcp_memory.py#L92):加 `kid = f"  id={it.id[:8]}" if it.id else ""`
- [schema.py `RecallHit.render`](../../src/hyperion/services/memory/schema.py#L247):加 `kid = f"  id={self.item_id[:8]}" if self.item_id else ""`

### bug 2:mark_corrected id 前缀不匹配(连锁 bug)

修完 bug 1 重跑 e2e:**新纠正条 `d0a311d0` 写成功了**(summary 写「矛盾已闭环」),但 DB raw 查 A派 4 条 `corrected_by` **仍全空**。`memorize_items`([memorize.py:156-158](../../src/hyperion/services/memory/backends/native/memorize.py#L156))消费 `corrects` 调 `store.mark_corrected(target_id, ...)` 链路在,但 `mark_corrected`([store.py:463](../../src/hyperion/services/memory/backends/native/store.py#L463))`WHERE id=?` 精确匹配 —— **DB id 是 16 位(`b448561a6f8f45b0`),agent 传 8 位(`b448561a`)**(正是 bug 1 渲染的形态)→ 精确匹配失败 → `rowcount=0` → 静默 no-op(只 warning log)。

**这是 bug 1 的直接后果**:我渲染 8 位,但 `mark_corrected` 要完整 id。

**修**:`store._resolve_id(item_id)` 辅助方法 —— 精确匹配(快路,内部调用方传完整 id 直接中)失败则前缀匹配(`LIKE ? || '%' LIMIT 2`);恰好 1 条前缀命中 → 用完整 id;0 条或 >1 条(歧义)→ None。`mark_corrected` + `set_invalid`(都是 agent 写入路径)都走 `_resolve_id`;`bump_access`/`set_kind`/`consolidate`(内部,传完整 id)保持精确匹配。**歧义拒绝**(宁漏不错):前缀撞车时不改。

### 真 DB 重放验证(不靠 agent 自述)

不重跑 e2e(避免再造 closure 卡污染库),直接用真 store + 8 位前缀重放 `mark_corrected`:
- 修前:A派 3 条 `corrected_by` 全空(第 4 条 `8de9ae88` 是 8 位完整 id)
- 重放:`b448561a/4f739d5a/b1e79133/8de9ae88` → mark_corrected 全 True(前缀解析成功;`8de9ae88` 走精确匹配快路)
- 修后:A派 `corrected_by=d0a311d0`,`corrected_by` 总计数 0→4

**闭环验证完成**:agent dump → 看 8 位 id → memorize(corrects=[8char]) → mark_corrected 前缀解析 → 旧条标 corrected_by。

## onboarding e2e:全绿(自驱质量高)+ 记两渣症

onboarding e2e(wpa,24 steps 预算)**自驱 11 步 / 26 工具全绿**,质量高:
- ✅ **recall-first 短路判定正确**:命中 10 条 wpa 记忆但都是零散模块事实(driver_nl80211/wpa_cli/P2P 粘合层),**没一条是架构导览级** → 主题对不上短路条件 → **走完整调研**(SKILL step 2 的正确分流,不是「命中就偷懒短路」)。
- ✅ **主旅程选对**:`wpa_supplicant_connect` → associate → IE 构建 → `wpa_drv_associate` 驱动调用 → 事件回调 → EAPOL → COMPLETED(全仓连接总纲,hub `wpa_supplicant_associate@wpa_supplicant.c:1931` 8 个调用者)。
- ✅ **报告落盘**(133 行)+ **memorize 非幻觉**(DB raw 查 id=`7add3ff5`,kind=codebase_fact/kind_detail=module,summary 是真架构描述)。
- ✅ **工具分布合理**:read×12(逐节点走旅程)+ call_chain×4 + repo_overview×2 + repo_map×1 + search×1。

**两渣症(记 backlog,不致命)**:
1. **memorize 的 evidence=[] 空**:onboarding 记的架构事实没带 file:line evidence(SKILL step 7 要求 `evidence=[<file:line+片段>]`,agent 记了结论但没落锚点)。这是 agent 证据纪律问题(SKILL 已要求),非工具 bug —— onboarding 的 memorize 不带 file/file:line 参数(`memory_memorize` 的 `file`/`line` 是单锚点,架构事实涉及多 file:line 塞不进)。**记 backlog:架构级 memorize 要不要支持多 evidence**(当前 `memory_memorize` 只接受单 file:line)。
2. **repo_overview 746 社区截断**:wpa 746 社区塞进单个 tool 返回爆了 8000 截断,agent「每次首个社区处截断,hub/bridge 部分取不到」,被迫改用 repo_map PageRank + call_chain 组合绕过(最后还是成功出报告)。**记 backlog:repo_overview 大仓社区数过多时的输出策略**(社区分页 / 只返 top-N 社区 + 全量 hub/bridge,或 agent 提示分次取 communities)。

## 结论

- **验证优先于再造**的价值再次坐实:两个「待验」skill 的 e2e 抓到 correction-link 的 2 连锁 bug(读码/单测都看不出 —— 单测用完整 id 测 `mark_corrected`,从没测过 8 位前缀;e2e agent 拿到的是渲染的 8 位)。这正是 [[recall-validation-handoff]]「N=2 真机推翻 N=1」+ [[compare-skill-handoff]]「e2e 抓注入层 gap」同款规律。
- **correction-link 现真闭环**:首次真数据触发(A派 4 条 abort-failure 误诊被 B派 scan-only 覆盖竞态纠正),corrected_by 回填 + 检索降权 0.3×(待下次 recall 验降权效果)。
- onboarding 的两渣症 + memory-health 的「`memory invalidate` 也吃前缀」都记 backlog,非本轮范围。

## 改的文件(本轮)

1. [src/hyperion/tools/mcp_memory.py](../../src/hyperion/tools/mcp_memory.py) —— `_render_audit_card` 加 id 渲染。
2. [src/hyperion/services/memory/schema.py](../../src/hyperion/services/memory/schema.py) —— `RecallHit.render` 加 item_id 渲染(memory 路才出)。
3. [src/hyperion/services/memory/backends/native/store.py](../../src/hyperion/services/memory/backends/native/store.py) —— `_resolve_id`(精确+前缀+歧义拒绝)+ `mark_corrected`/`set_invalid` 走它。
4. [tests/test_mcp_tools.py](../../tests/test_mcp_tools.py) —— audit card 加 id 断言。
5. [tests/services/memory/test_memory_native.py](../../tests/services/memory/test_memory_native.py) —— RecallHit.render id 测 + mark_corrected 前缀+歧义测。

## 验证

- memory 全量 + mcp_tools:**75 passed**(原 74 + 新 1 recall render 测;2 warnings = 无关 `table_names()` DeprecationWarning)。
- ruff clean。
- 真 DB 重放:corrected_by 0→4(A派 4 条全标)。
- 不跑真模型/orchestrator e2e(用户自验铁律);onboarding e2e 是本会话自跑(同 compare/memory-health 先例)。

## 不做(YAGNI / backlog)

- onboarding memorize 多 evidence 支持(当前单 file:line)→ backlog。
- repo_overview 大仓社区截断策略 → backlog。
- 重跑 memory-health e2e 验降权效果(corrected_by 0.3× 降权单测已绿 [[correction-link-handoff]] test_recall_demotes_corrected;真 recall 降权待自然触发)。

关联 [[correction-link-handoff]] [[memory-health-check-handoff]] [[onboarding-skill-handoff]] [[recall-validation-handoff]] [[pitfall-log]](#20 草稿判断实证推翻) [[compare-skill-handoff]]。
