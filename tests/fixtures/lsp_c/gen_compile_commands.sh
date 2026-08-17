#!/usr/bin/env bash
# 给 L2 导航 fixture 生成 compile_commands.json(absolute 路径,clangd 要求)。
# 每台机 / 每个 clone 路径跑一次(路径变了重跑)。生成文件已 gitignore —— 不提交,
# 因为里面的绝对路径因机器而异(Linux /home/...、macOS /Users/...)。
#
# 面向小白:compile_commands.json 告诉 clangd「每个 .c 该用什么编译命令编译」,
# 它据此建精确索引、展开宏、消歧,才能给零漏召的 references。
# 真实项目(bluez/systemd)用 bear 或 compiledb 自动生成;这个 fixture 太小,手写两行即可。
set -euo pipefail
cd "$(dirname "$0")"
D="$(pwd)"
cat > compile_commands.json <<EOF
[
  { "directory": "$D", "file": "lib.c",  "command": "clang -c lib.c" },
  { "directory": "$D", "file": "main.c", "command": "clang -c main.c" }
]
EOF
echo "wrote $D/compile_commands.json"
echo "验证:uv run rootrecall lsp health \"$D\""
