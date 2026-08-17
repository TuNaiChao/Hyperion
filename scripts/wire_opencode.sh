#!/usr/bin/env bash
# 给 bug/工作仓接上 RootRecall 的两根线,接完就能在那个仓里直接启动 opencode(不用回本仓根):
#   门1(skill 发现): <bug仓>/.claude/skills 软链到本仓 .claude/skills
#                     (opencode 从启动目录沿 git worktree 爬,项目级 .claude/skills 会被拾取)
#   门2(MCP 锚定):   <bug仓>/opencode.json = 本仓模板 + mcp.rootrecall.cwd = 本仓根
#                     (opencode 官方 cwd 字段让 rootrecall 服务器进程在本仓根跑:
#                      uv 找得到 .venv、data/(记忆/索引)不漂到 bug 仓、.env 自加载)
#
# 用法: bash scripts/wire_opencode.sh <bug仓路径> [<bug仓路径>...]
# 可重复执行(幂等);目标已有自己的 opencode.json(不含 rootrecall)→ 备份成 .bak 后跳过,不覆盖别人的配置。
set -euo pipefail
REPO=$(cd "$(dirname "$0")/.." && pwd)

if [ $# -eq 0 ]; then
  echo "用法: bash scripts/wire_opencode.sh <bug仓路径> [<bug仓路径>...]" >&2
  exit 1
fi

# 先把参数转成绝对路径(下面要 cd 回本仓根跑 uv,相对路径会跟着漂 —— 踩坑#21 同族预防)
BUGS=()
for p in "$@"; do
  if [ -d "$p" ]; then p=$(cd "$p" && pwd); fi
  BUGS+=("$p")
done

cd "$REPO"
TEMPLATE="$REPO/config/opencode_rootrecall.json"

for BUG in "${BUGS[@]}"; do
  echo "── 接线: $BUG"
  if [ ! -d "$BUG" ]; then
    echo "  ⚠️ 目录不存在,跳过(路径写对后重跑即可)"
    continue
  fi

  # 门1:skills 软链
  mkdir -p "$BUG/.claude"
  ln -sfn "$REPO/.claude/skills" "$BUG/.claude/skills"
  echo "  ✅ 门1 skills 软链 -> $REPO/.claude/skills"

  # 门2:生成 opencode.json(先两道安全检查,不动别人的配置)
  if [ -L "$BUG/opencode.json" ]; then
    echo "  ⚠️ opencode.json 已是软链(-> $(readlink "$BUG/opencode.json")),本脚本不穿透软链写文件,跳过"
    continue
  fi
  if [ -e "$BUG/opencode.json" ] && ! grep -q rootrecall "$BUG/opencode.json"; then
    cp "$BUG/opencode.json" "$BUG/opencode.json.bak"
    echo "  ⚠️ opencode.json 已存在且不含 rootrecall(疑似你自己的配置)—— 已备份为 opencode.json.bak,跳过"
    echo "     确认要覆盖:删掉 opencode.json 后重跑本脚本"
    continue
  fi
  # 注入用 uv run python 而非 jq —— jq 不在本项目 setup.sh 的依赖清单里,uv 必装(与 opencode 模板同用 --no-sync)
  uv run --no-sync python - "$TEMPLATE" "$BUG/opencode.json" "$REPO" <<'PY'
import json, sys

template, out, repo = sys.argv[1], sys.argv[2], sys.argv[3]
cfg = json.load(open(template, encoding="utf-8"))
cfg.setdefault("mcp", {}).setdefault("rootrecall", {})["cwd"] = repo
with open(out, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
    f.write("\n")
PY
  echo "  ✅ 门2 opencode.json 已生成(mcp.rootrecall.cwd = $REPO)"
  echo "  自检:cd $BUG && opencode mcp list → 应见 rootrecall ✓ connected"
done

echo ""
echo "完成。之后:cd <bug仓> && opencode —— 16 个 MCP 工具 + 8 个 skill 全量可用。"
