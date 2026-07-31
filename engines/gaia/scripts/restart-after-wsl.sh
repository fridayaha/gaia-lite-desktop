#!/usr/bin/env bash
# ── WSL 重启后一键拉起 Gaia 开发环境 ──
# 前提：已在 Windows 执行 wsl --shutdown，重新打开 WSL 终端
set -euo pipefail

ROOT="/home/jason/code/union_agent/engines/gaia"
cd "$ROOT"
mkdir -p .run-logs

echo "═══ 1. 启动 docker 基础设施容器 ═══"
docker compose up -d
echo "等待容器健康..."
sleep 20
docker ps --filter "label=com.docker.compose.project=gaia" --format "  {{.Names}}: {{.Status}}"

echo ""
echo "═══ 2. 停掉 api 容器（开发用本地 venv 替代）═══"
docker compose stop api 2>/dev/null || true

echo ""
echo "═══ 3. 启动后端（本地 venv uvicorn, port 8000）═══"
nohup .venv/bin/python -m uvicorn ontology.main:app --host 127.0.0.1 --port 8000 \
  > .run-logs/backend.log 2>&1 &
echo "  后端 pid: $!"
echo "  等待就绪..."
for i in $(seq 1 30); do
  curl -s --max-time 2 http://127.0.0.1:8000/health >/dev/null 2>&1 && { echo "  ✓ 后端就绪"; break; }
  sleep 1
done

echo ""
echo "═══ 4. 启动前端（vite, port 5173）═══"
nohup bash -c 'cd src/web-ui && npx vite --host 0.0.0.0 --port 5173' \
  > .run-logs/frontend.log 2>&1 &
echo "  前端 pid: $!"
sleep 4
tail -3 .run-logs/frontend.log

echo ""
echo "═══ 5. 验证 ═══"
sleep 2
printf "  后端 /health: "; curl -s --max-time 3 http://localhost:8000/health; echo
printf "  前端 5173:    "; curl -s -o /dev/null -w "HTTP %{http_code}\n" --max-time 3 http://localhost:5173/

echo ""
echo "═══ 完成 ═══"
echo "  Windows 浏览器访问: http://localhost:5173  (mirrored 模式直通)"
echo "  后端 API:          http://localhost:8000"
