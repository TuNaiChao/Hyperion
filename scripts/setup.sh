#!/usr/bin/env bash
# 安装系统级依赖(uv 管不了的:ctags/clangd/ripgrep 等)。Linux / macOS 通用。
set -euo pipefail

echo "[1/3] 系统工具"
case "$(uname -s)" in
  Darwin)
    if ! command -v brew >/dev/null 2>&1; then
      echo "请先安装 Homebrew: https://brew.sh" >&2; exit 1
    fi
    brew install universal-ctags ripgrep bear compiledb
    # clangd/clang 来自 Xcode Command Line Tools
    xcode-select --install 2>/dev/null || true
    ;;
  Linux)
    if command -v apt >/dev/null 2>&1; then
      # clangd/clang = P1.5 L2 精确导航的语言服务器;bear = 给 autotools/make 项目(bluez/wpa)生成 compile_commands.json
      sudo apt update && sudo apt install -y universal-ctags clangd clang build-essential ripgrep bear
      # compiledb:bear 的稳替代(解析 make 干跑,不受 LD_PRELOAD/SELinux/CCACHE 干扰;autotools 更稳)。apt 无包,pip 装。
      pip install --user compiledb 2>/dev/null || true
    else
      echo "非 Debian 系 Linux,请手动安装:universal-ctags clangd clang ripgrep bear" >&2; exit 1
    fi
    ;;
  *) echo "不支持的系统: $(uname -s)" >&2; exit 1 ;;
esac

echo "[2/3] Python 依赖(uv)"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
# `uv sync` + 两个产品本体 extra:mcp(MCP server,`rootrecall mcp serve` 必需)
# 和 code-review-graph(结构图,blast_radius/call_chain/repo_map/repo_overview 用)。
# 其余 extras(providers/mem0/cognee/embedding-local)仍按需 opt-in——
# 例如本地 embedding 要 `uv sync --extra embedding-local`(会拉 torch ~800MB)。
# 默认走远端 embedding(openai_compatible,复用 langchain-openai,零额外依赖)。
uv sync --extra mcp --extra code-review-graph

# Claude Code 记忆软链是本仓开发者的跨机记忆同步;外部使用者(只用 opencode)可跳过:
# ROOTRECALL_CLAUDE_LINK=0 bash scripts/quickstart.sh(或 setup.sh)
if [ "${ROOTRECALL_CLAUDE_LINK:-1}" = "1" ]; then
  echo "[3/3] Claude Code 记忆软链"
  bash "$(dirname "$0")/setup_claude.sh"
else
  echo "[3/3] 跳过 Claude Code 记忆软链(ROOTRECALL_CLAUDE_LINK=0)"
fi

echo ""
echo "✅ 完成。验证:uv run rootrecall models"
