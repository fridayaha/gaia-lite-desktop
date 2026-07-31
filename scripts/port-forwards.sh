#!/bin/bash
# UnionAgents 本地开发端口转发
# 使用方式: bash scripts/port-forwards.sh

set -e

echo "🚀 启动端口转发..."
echo "   3001 → enduser-portal (终端门户前端)"
echo "   8010 → gateway (会话/聊天/模型)"
echo "   8002 → manager (管理台后端 API，含 controller worker)"
echo ""

kubectl port-forward -n unionagents svc/enduser-portal 3001:80 &
PID1=$!

kubectl port-forward -n unionagents svc/gateway 8010:8010 &
PID2=$!

kubectl port-forward -n unionagents svc/manager 8002:8002 &
PID3=$!

echo ""
echo "✅ 全部就绪，按 Ctrl+C 停止"
echo "   管理台前端(dev): 另开终端 cd apps/admin && pnpm dev → http://localhost:8848"
echo "   终端门户: http://localhost:3001"

# 等待任意进程退出后清理
trap "kill $PID1 $PID2 $PID3 2>/dev/null; exit" SIGINT SIGTERM
wait
