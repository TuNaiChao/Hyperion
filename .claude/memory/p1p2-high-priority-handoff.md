---
name: p1p2-high-priority-handoff
description: 2026-08-14 P1/P2 backlog 🔴高优先 4 项全落地 + opencode e2e 真机全绿(金标吻合+双纪律生效)
metadata: 
  node_type: memory
  type: project
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-08-14T10:07:54.407Z
---

# P1/P2 高优先 4 项落地交接(2026-08-14,commit 7535527)

来源:2026-08-14 P1/P2 两支柱全面分析(docs/code-research-module-analysis.md + bug-rca-module-analysis.md)产出的 backlog(docs/p1-p2-improvement-backlog.md)🔴 1–4,当日全部落地。

## 四项改动

1. **#4 deep_research CRG 建图 try 降级**:`node_index` 的 `CodeGraph.build`/`architecture_overview`/`stats` 包 try(CRG 未装/建图异常 → 空结构继续,不崩 workflow);`node_plan` 的 `CodeGraph.open` 同理(图未建 → hubs=[])。与 CLI `index` 子命令降级标准对齐。2 单测(tests/workflows/deep_research/test_index_degrade.py)。
2. **#1 定向复核纪律**(auto-query 接回主路径,0 代码):bug-rca SKILL 证伪节 + agent prompt 加硬性步骤——"根因候选收敛成 1-2 个时,**必须**再调一次 `memory_recall(query=problem_summary)` 定向复核;query 用 problem_summary(现象一句话),别用原始日志原文(journalctl 噪声淹没信号,路线 #4 A1 证伪教训)"。
3. **#2 多假设清单纪律**(0 代码):"定位先列 2-3 个候选根因(记忆先验+日志线索),逐个找证据/反证按强度淘汰;单候选=锚定"。业界依据 CogniGent 2026(多候选假设并行证伪打分)。治踩坑 #11(锚定显眼日志行)的单假设病根。
4. **#3 五工具诚实截断**:`_honest_truncate(body, limit, how_to_refetch=...)` 统一替换 5 处裸 `body[:8000]`(blast_radius/call_chain/cross_version_diff/merge_eval/repo_map)——超长才截 + note 明说截掉多少字符/怎么补取(减小 top_n/depth/map_tokens 等)。踩坑 #19(memory_dump 静默截断)同源治理。2 单测。

282 测全绿 + ruff clean。

## e2e 验证(真机,自跑)

模型 `unionstoh-ai/deepseek-v4-flash-0731`(历史 e2e 全绿模型;**glm-5.2 端点当日不稳**,"Unexpected server error" 即它——换模型不换判断标准)。任务 = demo2 wpa P2P scan 泄漏 bug,定位-only。24 步正常收尾:

- **根因 vs 金标逐点吻合**:D-Bus scan-only 覆盖 `scan_res_handler`(dbus_new_handlers.c:1446)→ P2P 结果误路由 scan_only_handler(events.c:1858)→ 只释放普通 scan_work、不识 p2p_scan_work(scan.c:2441)→ radio work 队列永久堵死;修复 = scan_only_handler 末尾释放孤儿 p2p-scan work。连"iw 直连 nl80211 所以正常"旁证都对。
- **多假设清单生效**:报告含「候选淘汰记录」段——A(handler 覆盖,胜出)/ B(结果被清空,证据淘汰)/ C(abort ENOENT,**时序证伪**:泄漏点 10:12:12 早于 abort 10:12:19)。
- **定向复核双重坐实**:memory_recall 双时点(09:57:04 定位前发散 + 09:59:56 定稿前复核);报告首句"定向复核完成,先验与证据完全吻合",且用先验 1588c403(radio-sibling 双接口)**反向排除**替代解释——复核不光确认还淘汰。
- **correction-link 真数据起效**:agent 采信 scan-only 覆盖竞态派(d5ad928d/5733afc3,被纠正链扶正)而非 abort-failure 误诊派(corrected_by 降权)。
- **边界守住**:定位-only 无 edit、未 memorize(未验证不记)、日志切片守纪律。

## e2e 方法论(可复用)

- 驱动脚本 /tmp/hyperion-e2e-bugrca.sh:set -a 加载 .env → cd Hyperion 根 → `timeout 570 opencode run --agent hyperion-bug-rca --format json --auto -m uniontech-ai/deepseek-v4-flash-0731 "<任务>"`。
- **最终结论不靠 stdout**:session 推流(event part type=text)或查 `~/.local/share/opencode/opencode.db` sqlite(message/part 表按 session_id);工具时间线查 log/opencode.log 按 run= 过滤 permission= 行。

关联:[[route4-auto-query-handoff]](#1 的原设计)/ [[pitfall-log]] #11 #19 / [[correction-link-handoff]](e2e 顺带验证降权生效)。
