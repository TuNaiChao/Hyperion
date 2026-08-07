---
name: bug-rca
description: Root-cause a bug in a C / system-software codebase (Linux kernel, BlueZ, wpa_supplicant, systemd, dbus, network stacks …). Use when the user asks to find the root cause of a bug / crash / hang / regression / CVE, or asks "why does X break / leak / deadlock". This is a TOOLBOX, not a fixed pipeline — you drive the reasoning + edit, calling Hyperion's tools as needed, iterating on hypotheses and patches. A patch is NOT considered correct until it applies cleanly AND is verified by a human / on a real device. Delivers a root cause, on-disk patch(es), and (once verified) a memorized lesson + on-disk report.
allowed-tools:
  - hyperion_memory_recall
  - hyperion_search_codebase
  - hyperion_filter_logs
  - hyperion_blast_radius
  - hyperion_validate_patch
  - hyperion_export_patch
  - hyperion_memorize
  - hyperion_export_report
  - read
  - grep
  - glob
  - edit
  - bash
---

# Bug 根因定位 + 修复(工具箱 · 人在环迭代)

面向小白:你在给一个 C/系统软件仓库(Linux 内核 / BlueZ / wpa_supplicant / systemd 这类)定位一个 bug 的
**根因**并修复。Hyperion 不抢你的活 —— **读码、推理、改代码是你(opencode)的强项**;Hyperion 只递工具
(翻记忆 / 语义搜码 / 切日志 / 算影响面 / 验 apply / 落补丁 / 落报告 / 沉淀教训)。你拿着这把"手术刀 +
记忆",自己开刀。

## ⚠️ 这不是固定流水线 —— 是工具箱 + 人在环迭代

**别想着"从头到尾一步步走完"。** bug 根因很少一次猜中,补丁很少一次修对。本 playbook 是**工具箱 +
迭代循环**,对标 Anthropic ["Build Skills Not Agents"](https://cobusgreyling.substack.com/p/anthropic-says-dont-build-agents)(工具箱,非固定管线)+ [POPPER(ICML 2025)](https://openreview.net/forum?id=iTevNo8PzG)(迭代证伪)+ [RepairAgent(ICSE 2025)](https://www.computer.org/csdl/proceedings-article/icse/2025/056900a694/251mGP1fmRq)(补丁-验证循环):

1. **迭代假设-证伪**:RCA 是"假设 → 找证据(含反证)→ 修正"的循环。反复调 `recall`/`search_codebase`/
   `filter_logs`,每轮**主动证伪**当前根因(含时序一致性检查,踩坑 #11),直到根因站得住。**没坐实就继续
   迭代,别硬下结论。**
2. **补丁-验证循环**:edit 改代码 → `validate_patch`(每版验 apply)→ `export_patch` 落盘 → **人/真机验证**
   → 没修对就回 edit 再改 → 再 `export_patch`。每生成一版补丁就落盘一版。
3. **验证通过才沉淀**:`memorize` + `export_report` 是**收尾动作**,只在补丁经验证确认后才调。**没验证就
   memorize = 把没坐实的根因/补丁写进记忆 = 污染 + 误导下次同类 bug。**

## 八把工具(按需取用,无固定顺序)

| 工具 | 啥时候调 | 关键提醒 |
|---|---|---|
| `hyperion_memory_recall(query)` | 想知"这仓库以前有没有类似 bug"时 | 返回**先验线索非答案**;与证据矛盾以证据为准;⚠️ 别拿残缺证据自信证伪先验(踩坑 #11 记忆反噬) |
| `hyperion_search_codebase(query)` | 用**概念/自然语言**找入口符号 | 只回索引里真实存在的 file:symbol:line(幻觉不出假路径);别传猜的文件名 |
| `hyperion_filter_logs(path, since, until)` | 大日志按故障时间窗切片省 token | ⚠️ **从更早切**(根因在现象上游,不在现象本身);切了窗会看到边界提醒——若根因假设落在窗起点附近,前推 `since` 重筛(踩坑 #11) |
| `hyperion_blast_radius(changed_files)` | 动手改之前,看改动连带波及谁 | 结构图驱动(BFS),非 LLM;图没建会提示先 index |
| `hyperion_validate_patch(patch, repo_path)` | **每改出一版补丁都调** | 执行硬门(`git apply --check`),零 LLM;⚠️ **只验"能干净打上",不验"修对了"** |
| `hyperion_export_patch(repo_path)` | **每生成一版补丁就调**(落盘给人/真机验证) | 空 diff 报错;落 `data/bug_rca/<repo>.patch`(覆盖上一版,最新版为准) |
| `hyperion_memorize(kind, summary, ...)` | **补丁经验证确认后**才调 | ⚠️ 没验证就记 = 污染记忆;kind=`bug_lesson` |
| `hyperion_export_report(content, repo_path)` | **补丁经验证确认后**才调(收尾) | 报告含根因+证据+patch 路径+**验证结果**;落 `data/bug_rca/<repo>-rca.md` |

## 典型工作流(参考,非强制 —— 实际按证据情况自由编排)

```
recall / search_codebase / filter_logs  →  立假设 + 主动证伪(含时序一致性检查)
    ↺  (找到反例 / 时序不对)  →  再 search/filter,修正假设,重来
blast_radius(要改的文件)  →  edit 改代码(最小改动)
validate_patch(这版补丁)              ← 每版都验 apply
    ↺  (applies=False)  →  修到 applies=True
export_patch  →  落盘这版补丁
═══════════  人在环验证(关键,别跳)  ═══════════
人 / 真机验证这版补丁
    ↺  (没修对)  →  人反馈现象 → edit 改 → validate_patch → export_patch → 再交人验证
(验证通过)  →  memorize(沉淀教训)+ export_report(落最终报告)收尾
```

## 人在环验证(最容易被低估,但最关键)

`validate_patch` 过 = 补丁**能干净打上**(路径/格式/context 对),**≠ 修对了**。系统软件(wpa_supplicant /
BlueZ / 内核)大多**没有单元测试**,apply-check 是最弱的验证。**真正的 oracle 是真实设备 / 复现环境**:

- 每生成一版补丁 → `export_patch` 落盘 → **交人拿到真机 / 复现环境验证**(投屏复现 / 蓝牙连接 / 重触发原故障场景)。
- 验证**通过** → 调 `memorize`(沉淀)+ `export_report`(落最终报告)收尾。
- 验证**失败** → 人反馈现象 → 你回 `edit` 改 → `validate_patch` → `export_patch` 新版 → 再交人验证。
- 这可能跨多个 session(opencode `--continue` 续)。**`memorize` / `export_report` 只在最终验证通过后调。**

> 为什么这么较真:[METR 等研究发现](https://galileo.ai/blog/human-in-the-loop-agent-oversight),"自动测试通过"
> 的 PR 约一半不会被合并 —— 自动验证(test / apply)远不足以判定补丁正确。系统软件尤其:apply 过 ≠ 修对,
> 真机/复现才是 oracle。

## 防确认偏差(踩坑 #11 —— glm-5.2 连续 4 次误诊的教训)

LLM 会**系统性抓"显眼日志行"**(ERROR / 失败)当根因,忽略更早、更安静的因果链起点。这是模型固有的
anchoring / confirmation bias(改不了模型),但能用工具对抗:

- `filter_logs` 从**更早**切(根因在现象上游);看到返回末尾的"时间窗边界提醒"就**前推 `since` 重筛**,
  确认窗前没有更早的起因。
- 立假设后**主动证伪 + 时序一致性检查**:现象出现时间**不得早于** purported 根因;早于 = 你抓的多半是
  **症状不是根因**(如 `abort failed` 是"扫描早完成、状态没清"的后果),退回去往更早查。
- 别拿**残缺证据**自信证伪先验记忆(踩坑 #11 记忆反噬:e2e#5 里 agent 因 filter_logs 漏了起点行 → 以为
  "无 NEW_SCAN_RESULTS" → 自信推翻了正确的"误路由"先验)。

## 反模式(别这么干)
- ❌ **强求一次走完全流程** —— 根因/补丁常需迭代多次;没坐实就别往下硬走。
- ❌ **把 `validate_patch` 通过当"修对了"** —— 它只查 apply;修对要真机 / 人验证。
- ❌ **未经验证就 `memorize`** —— 污染记忆,误导下次同类 bug 的 RCA。
- ❌ **抓最响日志行当根因,没查它之前是否已有现象**(踩坑 #11);filter_logs 切晚了就前推。
- ❌ **拿残缺证据自信证伪先验记忆**(踩坑 #11 记忆反噬)。
- ❌ **顺手重构 / 无谓探索** —— 只修这个 bug,最小改动。

---
*Hyperion harness · bug-RCA skill · 工具箱 + 人在环迭代版(2026-08-07 重写,对标 Anthropic "Build Skills
Not Agents" + POPPER ICML 2025 迭代证伪 + RepairAgent ICSE 2025 补丁-验证循环 + METR 自动验证不够)。*
