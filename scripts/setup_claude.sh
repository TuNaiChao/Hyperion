#!/usr/bin/env bash
# 把 Claude Code 的项目记忆软链到仓库内 .claude/memory/,使记忆随 git 跨机同步。
# 每次 fresh clone 后跑一次。Linux / macOS 通用。
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
# Claude Code 用"绝对路径的 / 换成 -"作为项目 key
SLUG=$(echo "$REPO" | tr '/' '-')
DEST="$HOME/.claude/projects/$SLUG"

mkdir -p "$DEST"

# 若已存在真实目录(非软链),先备份再替换,避免 ln 把链接建到目录里面
if [ -e "$DEST/memory" ] && [ ! -L "$DEST/memory" ]; then
  mv "$DEST/memory" "$DEST/memory.bak.$(date +%s)"
  echo "已备份原有记忆目录到 $DEST/memory.bak.*"
fi

ln -sfn "$REPO/.claude/memory" "$DEST/memory"

echo "✅ 记忆已软链: $DEST/memory -> $REPO/.claude/memory"
echo "   slug=$SLUG"
echo "   提示:若各机器仓库绝对路径不同,slug 会不同,但记忆内容一致(都在 git 里)。"
echo "        建议两台机用相同路径(如 ~/Desktop/Agent/Hyperion)以保持 slug 对齐。"
