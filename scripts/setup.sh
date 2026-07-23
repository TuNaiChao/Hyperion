#!/usr/bin/env bash
# 安装系统级依赖(uv 管不了的:ctags/clangd/ripgrep 等)。Linux / macOS 通用。
set -euo pipefail

echo "[1/3] 系统工具"
case "$(uname -s)" in
  Darwin)
    if ! command -v brew >/dev/null 2>&1; then
      echo "请先安装 Homebrew: https://brew.sh" >&2; exit 1
    fi
    brew install universal-ctags ripgrep
    # clangd/clang 来自 Xcode Command Line Tools
    xcode-select --install 2>/dev/null || true
    ;;
  Linux)
    if command -v apt >/dev/null 2>&1; then
      sudo apt update && sudo apt install -y universal-ctags clangd clang build-essential ripgrep
    else
      echo "非 Debian 系 Linux,请手动安装:universal-ctags clangd clang ripgrep" >&2; exit 1
    fi
    ;;
  *) echo "不支持的系统: $(uname -s)" >&2; exit 1 ;;
esac

echo "[2/3] Python 依赖(uv)"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
uv sync --all-extras

echo "[3/3] Claude Code 记忆软链"
bash "$(dirname "$0")/setup_claude.sh"

echo ""
echo "✅ 完成。验证:uv run hyperion models"
