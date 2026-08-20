# 定时同步部署样例(systemd user timer / cron 二选一)

`rootrecall repo sync` 是幂等命令,定时反复跑即可。它做四件事:fetch 基线仓 →
基线检出 fast-forward 跟进 → 增量刷新检索索引(sha256 增量,只重嵌变化文件)→
`--analyze <fork名>` 时对发行版仓出上游三态判定报告
(已修/建议合/冲突,纯 git 零 LLM,落 `data/upstream_reports/<名>/`)。

两个进阶档(都只丰富报告,不自动改代码):
- `--analyze-agent`:三态报告后跑 headless opencode 复核「该不该合」并**追加进报告**
  (需 `--analyze` 同用;opencode 不在 PATH / 超时 / 失败 → 诚实注明退回纯三态)。
  前提:LLM key 已在 `.env`(CLI 进程会带给子进程)。
- `--ingest-report`:把报告摄取进记忆库(codebase=项目名),之后 recall 能带出
  「这 fix 上次评估过、为什么没合」—— 接 recall-first。

配套:`rootrecall repo gc` 定时回收过期 ephemeral 检出(`deploy/rootrecall-gc.*` 样例,
每周一 08:30;级联清 worktree+索引+结构图+记录,记忆与 baseline 不碰)。

## systemd user timer(推荐,Linux 单机无需 root)

```bash
mkdir -p ~/.config/systemd/user/
cp deploy/rootrecall-sync.service deploy/rootrecall-sync.timer ~/.config/systemd/user/
cp deploy/rootrecall-gc.service deploy/rootrecall-gc.timer ~/.config/systemd/user/
# 按需改 service 里的 WorkingDirectory(= RootRecall 安装根)与 --analyze 参数
systemctl --user daemon-reload
systemctl --user enable --now rootrecall-sync.timer rootrecall-gc.timer
systemctl --user list-timers 'rootrecall-*'     # 看下次触发
journalctl --user -u rootrecall-sync.service -f # 看同步日志
```

## cron(备选)

```bash
crontab -e
# 每天 07:30 同步全部 baseline,对 uos-v20 出三态报告 + agent 复核 + 入记忆
30 7 * * * cd /path/to/RootRecall && /path/to/.venv/bin/rootrecall repo sync --analyze bluez-v20 --analyze-agent --ingest-report >> data/sync.log 2>&1
# 每周一 08:30 清理过期 ephemeral 仓(14 天到期,先 dry-run 看清楚再真跑)
30 8 * * 1 cd /path/to/RootRecall && /path/to/.venv/bin/rootrecall repo gc >> data/sync.log 2>&1
```

注意:`sync --analyze` 只出**三态判定报告**(确定性事实);`--analyze-agent` 也只是把
复核意见写进报告 —— 「哪些真的该合进发行版」走 upstream-merge skill 的人工确认,
定时器永远不自动改代码/不自动合上游。
