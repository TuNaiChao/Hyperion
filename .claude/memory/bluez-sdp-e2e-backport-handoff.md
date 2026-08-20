# bluez 一句话 e2e + backport 行动闭环 · 交接卡(2026-08-20)

> 验收目标:「期望②贴脸」(用户原始措辞的自然语言问句跑真 bluez)+「期望③分析→行动全链」
> (sync 报告提名的安全修复 → backport 补丁上 v20)。两次 headless opencode run,零人工干预
> (除 source .env),素材全真零构造。

## 场景

- **素材**:问题描述.txt 全部取自上游公开事实(ASAN negative-size-param 现象、SEQ32/ALT32 构造、
  上游 c50c7ea445 修复线索);bug 目录 /tmp/bluez-e2e-sdp/ 只放这一个 txt。
- **版本**:用户预想措辞 "5.50.61" 在 v20 仓无此 tag(早期索引 bluez-v20-5.50.61 是旧会话遗留),
  用**真实 tag 5.50.2**(v20 仓 5.50 线仅 5.50.1/5.50.2/5.50.1000.3)。

## Run 1:bug-rca 一句话(~4 分钟,deepseek-v4-flash)

`opencode run "结合问题描述.txt,分析 bluez 5.50.2 这个 SDP 解析越界 bug 的根因,修复并落报告"`

1. 路由 bug-rca ✓;find_repo(project=bluez, version=5.50.2) ✓;memory_recall 命中同类教训 ✓
2. **根因与上游金标准(Pauli Virtanen dc07b3d5)逐字一致**:lib/sdp.c:1261 SEQ32/ALT32 分支
   `*size = bt_get_be32(buf)` uint32→int 溢出;修复 = val32 + `> INT_MAX` 检查(26 行补丁)
3. memorize 带 `verification=apply_only`(conf 封顶 0.5,id cdb2ade286b9ecc3)—— P2 纪律生效 ✓
4. 交付:bug_rca/bluez.patch(26 行)+ bluez-rca.md

### 偏差与发现(诚实记录)

- ⚠ **agent 没走 ephemeral checkout**:find_repo 返回 Related 基线后,agent 判 v20 基线 HEAD
  (5.50.1-53)≈ 5.50.2(事实上 0 个 C 文件差异)直接用基线仓干活,并改脏基线工作树。
  wpa P0 那次是 MISS→checkout;这次 Related+本地路径存在 → agent 优化掉了开仓。
  结论正确但流程偏差:bug-rca SKILL 的「仓库就绪」措辞可再收紧(backlog:Related 基线也优先
  checkout ephemeral,保基线干净 + 版本钉死)。基线已手动复位,quilt 应用态可用 quilt push -a 重建。
- 🐛 **export_patch 在 quilt 树上打垃圾**:`git add -A` 把 .pc/ 构建产物全量打入(26 行修复膨胀
  30 万行),agent 手工 git reset/clean 救回。**已修**(add 后补 `git reset -- .pc`,+回归测);
  debian 仓工作树不干净时的 stash 往返也值得 SKILL 提示(backlog)。

## Run 2:backport 行动闭环(~4 分钟)

`opencode run "…c50c7ea445(sdp 整数溢出)和 e81b6b9f87(PBAP 堆溢出+gatt-client UAF)…backport 到 v20"`

1. 路由 backport ✓;find_repo ×2;**memory_recall×2 直接给出 v20 路径线索(lib/sdp.c:1222、
   obexd/client/pbap.c:322、src/gatt-client.c:2089)—— P1 --ingest-report 摄取的 sync 报告记忆
   首次在行动链上真实变现(含 sdp 路径漂移知识)**
2. 三个 fix-point 逐一读 v20 函数体判「有同一 bug」,适配纯路径/行号漂移(5.85→5.50):
   sdp lib/bluetooth/sdp.c→lib/sdp.c、pbap.c 同路径 330→337、gatt-client.c 2261→2091
3. validate_patch strict 通过(备份→还原→验→恢复,逐字节一致);export_patch(name 输入 →
   bluez-v20.patch 53 行 3 文件 +11/-2)+ backport 卡(94 行)
4. **未 memorize**:"用户真机验证通过后再 memorize(届时带 real_machine)" —— 验证纪律全程在线 ✓

## 交付物与待办

| 项 | 落点 |
|---|---|
| sdp 修复补丁(Run 1) | ~/.local/share/rootrecall/bug_rca/bluez.patch(26 行)|
| backport 补丁(Run 2,三合一) | ~/.local/share/rootrecall/bug_rca/bluez-v20.patch(53 行)|
| backport 卡 | ~/.local/share/rootrecall/bug_rca/bluez-v20-rca.md |
| v20 基线工作树 | 3 文件已改未 commit(交付物;验证后由用户决定 commit/drop)|
| ⏳ 等用户 | 真机验证三点(SDP 发现 / PBAP 通讯录 / GATT 析构)→ 通过后升级记忆 real_machine |

## 一句话总结

「空目录 + 一句话 → 根因+补丁」在真 bluez v20 上复现;「sync 提名 → 一句话 → backport 补丁」
行动闭环首次全链跑通,且记忆库在行动链上产生了可度量的路径定位价值。
