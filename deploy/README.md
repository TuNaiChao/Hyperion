# 定时同步部署样例(systemd user timer / cron 二选一)

`rootrecall repo sync` 是幂等命令,定时反复跑即可。它做四件事:fetch 基线仓 →
基线检出 fast-forward 跟进 → 增量刷新检索索引(sha256 增量,只重嵌变化文件)→
`--analyze --fork <仓名>` 时对发行版仓出上游三态判定报告
(已修/建议合/冲突,纯 git 零 LLM,落 `data/upstream_reports/<名>/`)。

## systemd user timer(推荐,Linux 单机无需 root)

```bash
mkdir -p ~/.config/systemd/user/
cp deploy/rootrecall-sync.service deploy/rootrecall-sync.timer ~/.config/systemd/user/
# 按需改 service 里的 WorkingDirectory(= RootRecall 安装根)与 --analyze 参数
systemctl --user daemon-reload
systemctl --user enable --now rootrecall-sync.timer
systemctl --user list-timers rootrecall-sync.timer   # 看下次触发
journalctl --user -u rootrecall-sync.service -f      # 看同步日志
```

## cron(备选)

```bash
crontab -e
# 每天 07:30 同步全部 baseline 并对 uos-v20 出三态报告
30 7 * * * cd /path/to/RootRecall && /path/to/.venv/bin/rootrecall repo sync --analyze bluez-v20 >> data/sync.log 2>&1
# 每周日清理过期 ephemeral 仓(14 天到期,先 dry-run 看清楚再真跑)
0 8 * * 0 cd /path/to/RootRecall && /path/to/.venv/bin/rootrecall repo gc >> data/sync.log 2>&1
```

注意:`sync --analyze` 只出**三态判定报告**(确定性事实);「哪些真的该合进发行版」
走 upstream-merge skill 的人工/agent 复核,定时器不自动改代码。
