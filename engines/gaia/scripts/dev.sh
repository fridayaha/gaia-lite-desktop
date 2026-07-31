#!/usr/bin/env bash
# ── 一键启动 Gaia 后端 + 前端（开发模式）──
#
# 为什么有这个脚本：
#   settings 模块加载时自动将 .env 的 API key 注入 os.environ
#   （见 src/ontology/config/settings.py 的 _PROVIDER_KEY_ENV），
#   所以任意启动方式（make dev / uv run uvicorn / IDE）AI 助手都能拿到 key。
#   此脚本只提供统一的启动入口 + health 等待 + 前后端一起起。
#
# 用法：
#   bash scripts/dev.sh          → 全部（后端 8000 + 前端 5173）
#   bash scripts/dev.sh backend  → 仅后端
#   bash scripts/dev.sh frontend → 仅前端

set -euo pipefail
cd "$(dirname "$0")/.."  # 回到仓库根
ROOT="$(pwd)"
mkdir -p .run-logs

start_backend() {
  echo "==> 同步数据库 schema（alembic upgrade head）..."
  "${VENV_PYTHON:-.venv/bin/python}" -m alembic upgrade head || { echo "   迁移失败，中止"; exit 1; }
  echo "   迁移完成 ✓"
  echo "==> 启动后端 (port 8000)..."
  nohup "${VENV_PYTHON:-.venv/bin/python}" -m uvicorn ontology.main:app --host 127.0.0.1 --port 8000 \
    > "${ROOT}/.run-logs/backend.log" 2>&1 &
  echo "   后端已启动 (pid $!)"
}

start_frontend() {
  echo "==> 启动前端 (port 5173)..."
  ( cd src/web-ui && nohup npm run dev > "${ROOT}/.run-logs/frontend.log" 2>&1 & )
  echo "   前端已启动"
}

wait_health() {
  echo "==> 等待后端就绪..."
  for i in $(seq 1 30); do
    if curl -s --max-time 2 http://127.0.0.1:8000/health > /dev/null 2>&1; then
      echo "   后端就绪 ✓"
      return 0
    fi
    sleep 1
  done
  echo "   后端启动超时 — 查看 .run-logs/backend.log"
  return 1
}

case "${1:-}" in
  backend)
    start_backend
    wait_health
    ;;
  frontend)
    start_frontend
    ;;
  *)
    start_backend
    start_frontend
    wait_health
    echo "==> 前端: http://localhost:5173"
    ;;
esac
