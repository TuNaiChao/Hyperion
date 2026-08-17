---
name: e2e-backlog-clearance-handoff
description: 2026-08-17 三挂账 e2e 清零:domain-research 落 DB+recall 闭环 / upstream-merge 零 checkout+三态 / correction-link 降权渲染
metadata: 
  node_type: memory
  type: project
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-08-17T03:41:56.117Z
---

# 三挂账 e2e 清零(2026-08-17)

CLAUDE.md 里 3 处「opencode e2e 待真机跑」全部清零,当日跑完当日验。前置:DB 备份到 `/tmp/memory.db.bak-e2e`(e2e 有 memorize 副作用,备份铁律)。

## ① domain-research(最后一个没 e2e 的 skill)

驱动:`/tmp/hyperion-e2e-domain.sh`(topic=BLE LE Data Channel L2CAP,codebase=bluez)。**全绿**:
- DB 落点 `90a8f376`:kind=domain_knowledge + `source_tier=imported` + `source_url` 指 bluetooth.com 官方 Core Spec 6.1(raw sqlite 查证非幻觉)。
- 报告 `data/domain_research/bluez-rca.md`:7 源交叉(Core 6.1 主源 + 6.2/5.4 版本一致 + Silicon Labs + MathWorks + 内核 l2cap_core.c 代码级印证),confidence 0.85。
- **recall 闭环**(价值命题真数据起效):`search('BLE L2CAP credit based flow control', codebase=bluez)` 新条目以 0.031 最高分命中,排在一堆 bug_lesson 前 —— 「领域知识进 recall 治踩坑#11 误诊」这条设计决策被实证。
- agent 自驱质量超预期:主动澄清「connectionless CID 0x0002 是 BR/EDR 概念、LE 不存在」防以后误套。

**踩坑(自查 DB 的姿势)**:RecallHit 的字段是 `corrected_by/item_id`(在 schema.py:211),不是 `id/item`;`search()` 签名是 `(query, Scope, top_k=)` 不是 kwargs codebase。CORRECTED 标记在 `render()` 输出里,不在 `summary` 字段里 —— 验渲染要打 `h.render()` 不是查子串。

## ② upstream-merge(merge-tree 升级后重跑)

夹具 `/tmp/um-e2e/fork`:main(base)→ upstream(U1 init→10 / U2 新增 extra.c / U3 process→20),fork = base + cherry-pick U1 + F1(process→99),**全程停 main**(零 touch 姿势)。金标:U1 already_fixed / U2 conflict* / U3 conflict(*见下)。

**agent 行为全对**:
- **没切分支、没动工作树**(结束后 `symbolic-ref=main` + porcelain 空 + 无多余文件)—— checkout 硬门降级后 agent 真的不再切了,新姿势成立。
- 三态与工具一致;U1 用 patch-id 双验(eb47a9d 两侧同 id)。
- **超预期深挖**:对 U2(只新增不相交文件 extra.c)被判 conflict 起疑 → 自己跑 `merge-tree --merge-base=c79fec5 fork 96c7a87` 复现 + /tmp 克隆真 `git merge` 对照 → 定性为「hunk 邻接保守合并误报」(U2 侧 core.c 恒等于 base,但 fork 改了邻行)→ 报告里明写「新增文件干净可加,core.c 冲突是保守误报,保留 fork 值即可」。这正是「确定性工具出三态、语义裁决归 agent」分工想要的输出。
- 守边界:未 memorize(等用户真机验证)。

**夹具坑(连踩两次,记死)**:`git cherry-pick -q` 不存在(无 -q flag)→ usage 129 静默什么都不做,后续步骤全部建立在空 fork 上;正确姿势 `git cherry-pick <sha>` 裸调。另一坑:checkout fork 后做本地改动再回 main,`git commit -qam` 前要确认还在 fork。

## ③ correction-link 复检(不用跑 agent,DB+渲染双验)

- **在库**:4 条 abort-failure 派全部 `corrected_by=d0a311d0`(体检闭环卡)。
- **渲染**:`search('wpas_abort_ongoing_scan 失败 -ENOENT…', wpa)` top50 里 4 条旧派命中,`render()` 每条带 ` (已被纠正)  id=xxxxxxxx` 标记。
- **降权**:直查 abort-failure 主题时旧派仅 1 条挤进 top30 且垫底,纠正者派置顶 —— `CORRECTED_PENALTY=0.3` 真数据起效,且「降权不隐藏」边界保持。

## 收尾

- CLAUDE.md 3 处「待真机跑」清零 + merge_eval 旧描述(apply --check/checkout fork_ref)同步为 merge-tree 措辞;`grep -c 待真机跑 CLAUDE.md` = 0。
- /tmp 夹具与 /tmp/opencode/um-test 全清。
- e2e 写入的 domain_knowledge(80 条总数 +1)是合法新数据,保留不回滚(备份仍在 /tmp)。

关联:[[domain-knowledge-handoff]] / [[p1p2-backlog-568-handoff]](#6 的 e2e 就是本篇②) / [[correction-link-handoff]] / [[e2e-revalidation-2026-08-13-handoff]](上轮 e2e)。
