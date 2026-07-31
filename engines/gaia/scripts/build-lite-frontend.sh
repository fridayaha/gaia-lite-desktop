#!/usr/bin/env bash
# 构建 lite 前端 dist 到 tauri/dist（Tauri beforeBuildCommand / beforeDevCommand 调用）。
# EDITION=lite 触发 vite __EDITION__ define，砍图探索路由（maplibre tree-shake）。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GAIA_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
WEB_UI="$GAIA_DIR/src/web-ui"
OUT_DIR="$GAIA_DIR/tauri/dist"

if [ ! -d "$WEB_UI/node_modules" ]; then
  echo ">> web-ui/node_modules 缺失，npm install --legacy-peer-deps（peer 冲突 assistant-ui）"
  cd "$WEB_UI" && npm install --legacy-peer-deps
fi

echo ">> EDITION=lite vite build → $OUT_DIR"
cd "$WEB_UI"
EDITION=lite node node_modules/vite/bin/vite.js build --outDir "$OUT_DIR" --emptyOutDir

echo ">> lite frontend build done: $OUT_DIR"
