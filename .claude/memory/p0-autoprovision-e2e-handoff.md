# P0 自动开仓 · 真 opencode 全链 e2e 交接卡(2026-08-20)

> 验收目标(「期望②」的终局形态):全局安装后,在**只放问题 txt + 日志的空目录**里用一句自然语言问 opencode,验证 路由 → find_repo → 自动开仓建索引 → bug-rca → 补丁/报告落盘 全链。P0(commit `1c2d409`)的前置。

## 场景与素材(全真,零构造)

- **bug**:demo2 金标准(wpa P2P find→stop→WiFi scan 全空);日志 = 真机 `journalctl_b.txt`(1.6 万行);金标准补丁 = `example/demo2/fix-p2p-scan-orphan-minimal.patch`。
- **基线**:`repo register wpa --path example/demo2/wpa --url <同路径本地> --role baseline --branch release/eagle`(url 用本地路径,镜像 clone 零网络)。
- **legacy 索引迁移(顺带验证 #23 迁移路径)**:对源仓重跑 `index` → incremental noop(0 chunk 重嵌)+ manifest 补记 repo_path,老索引纳入管理 ✓。
- **全局安装**:`install --global` 真装 —— 8 skills 软链 + mcp.rootrecall 合并(用户 opencode.json 无 mcp 键,干净合并)+ AGENTS.md 路由表(含新「仓库就绪三步」)。
- **bug 目录**:`/tmp/rootrecall-p2-e2e/`(仓外,防 #21 git 污染)只放 问题描述.txt + journalctl_b.txt;问题文本只给现象不给根因。

## 运行(opencode run 一句话)

```
cd /tmp/rootrecall-p2-e2e && set -a && . <RootRecall>/.env && set +a && \
opencode run "结合问题描述.txt 和 journalctl_b.txt,分析 wpa_supplicant 2.9.0.21 版本的 bug 根因,修复并落报告"
```

⚠ 首跑 401 `Invalid API key`:opencode 不读 `.env`、provider key 走 shell env(老坑复现,已档 [opencode-mcp-wiring](opencode-mcp-wiring.md) 2026-08-12 节);**headless e2e 必须 source .env**。

## 全链实录(9 次 rootrecall 调用 + 1 次 bash,~6-7 分钟,deepseek-v4-flash)

1. **路由**:Sisyphus 默认 agent 按 AGENTS.md 路由进 bug-rca(未用 @ 点名,零接线)✓
2. **find_repo**:`{"project":"wpa_supplicant","version":"2.9.0.21"}` —— 项目+版本是 agent 自己从问话里解析的;命中 loose 但版本没配上 → 返回基线清单 + 开仓命令 ✓
3. **自动开仓(原样跑命令)**:`repo checkout wpa_supplicant-2.9.0.21 --from wpa --ref 2.9.0.21 --bug p2p-find-stop-scan-empty --index` → bare 镜像(本地 clone 秒级)+ worktree + **播种基线索引增量建 0 chunk**(tag 态与基线索引内容一致,白嫖)+ manifest 记 repo_path + 登记 ephemeral ✓
4. **记忆**:memory_recall(codebase=wpa,query=现象)✓ 命名纪律对(项目名不带版本)
5. **检索**:`search_codebase(codebase="wpa_supplicant-2.9.0.21")` ×2 —— **查的就是自动开出来的新索引**,查询词直指案发区(radio_work/scan_only)✓
6. **诊断**:根因 = P2P 扫描在飞时并发 scan-only 覆盖 scan_res_handler → 结果误路由 scan_only_handler → p2p_scan_work 泄漏 → radio work 队列卡死;**时序证伪 abort-failure 假说**(泄漏 10:12:12 < abort 失败 10:12:19)= 踩坑#11 的纪律被正确执行 ✓
7. **交付**:edit 三文件 → validate_patch strict 干净 apply → blast_radius → export_patch + export_report 落 `data/bug_rca/wpa_supplicant-2.9.0.21.{patch,-rca.md}` ✓
8. **记忆闭环**:memorize bug_lesson(id `0ec62585e5a38509`)入库,recall top-1 可召回,与库内 B 派(5733afc/d5ad928d 纠正链)同源相互印证 ✓

## 对照金标准

- **同根因、同修复点**:同样 3 文件(scan.c / p2p_supplicant.c/.h)、同样在 scan_only_handler 里 CONFIG_P2P 保护下释放孤儿 p2p-scan work。
- **形态度不同(不是缺陷)**:金标准内联在 scan.c;agent 封装 `wpas_p2p_scan_work_done()` 并额外调 `p2p_scan_res_handled()` 推进 P2P 状态机(释放模式对齐既有 handler)。

## 发现与遗留

- ⚠ **memorize 早于真机验证**(纪律偏差):SKILL 铁律是「验证通过后才 memorize」,agent 在 apply+代码确认后即记(conf=0.5、报告诚实标注「真机验证留用户」)。行为可辩护但违反字面纪律 → backlog:bug-rca SKILL 的 memorize 门槛措辞再硬化(或工具层给未验证 bug_lesson 打标)。
- **ephemeral 仓保留**:`data/worktrees/wpa_supplicant-2.9.0.21` 留给用户真机验证(验证完 `repo gc --name wpa_supplicant-2.9.0.21` 回收);registry 里 baseline wpa + ephemeral 各一条。
- **todo P2「真实仓迁移演练」部分完成**:wpa 侧(重跑 index=增量 noop+repo_path)已验;bluez v20/v25 adopt + 真实 `sync --analyze` 仍待做。
- 全链 **零人工干预**(除 source .env),「空目录 + 一句话 → 根因卡+补丁」的验收标准(踩坑#27 ⑤)达成。
