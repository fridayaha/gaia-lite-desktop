#!/bin/bash
# 在 docs/ 根目录下创建符号链接，使 guide/ 中的子目录对 VitePress 可见
# 这样 VitePress 的 SPA 客户端路由 pathToFile 能找到正确的文件

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DOCS_DIR="$(dirname "$SCRIPT_DIR")"

cd "$DOCS_DIR"

# 需要暴露到根目录的 guide 子目录
for dir in 01-overview 02-tutorials 03-how-to 04-concepts 05-reference 06-roadmap; do
    target="guide/$dir"
    if [ -d "$target" ] && [ ! -L "$dir" ]; then
        ln -sf "$target" "$dir"
        echo "Created symlink: $dir -> $target"
    fi
done
