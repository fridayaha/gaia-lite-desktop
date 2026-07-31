#!/usr/bin/env bash
# ============================================================
# 后端热重载（开发调试用）—— 改 Python 代码 ~1s 自动生效
#
# 原理：
#   k3s 单节点跑在 WSL2，和源码在同一个文件系统。直接把宿主源码目录
#   hostPath 挂进 gaia-api Pod 的 /app/src/ontology，再让 uvicorn 以
#   --reload 模式运行。watchfiles 监听文件变动，自动重启 worker。
#
#   不重建镜像、不重导 containerd、不 rollout Pod——只 patch 一次 deploy
#   （加 volume + 改启动命令），之后改代码就是「保存即生效」。
#
# 与 local-update.sh 的关系：
#   - 改 Python 代码 → 本脚本（秒级）
#   - 改 pyproject.toml/uv.lock/Dockerfile/部署清单 → local-update.sh（慢但正确）
#   - 改完依赖想回到热重载 → 重新跑本脚本（会重新 patch）
#
# 用法：
#   bash scripts/dev-hotload-api.sh           # 启用热重载（默认监听 8000）
#   bash scripts/dev-hotload-api.sh off       # 关闭热重载，回到镜像版
#
# 前置：
#   - k3s 单节点，源码在宿主 /home/jason/code/union_agent/engines/gaia
#   - port-forward 8000 已由 local-update.sh 建好（或手动建）
# ============================================================
set -euo pipefail

NAMESPACE="gaia"
DEPLOY="gaia-api"
API_PORT="${API_PORT:-8000}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST_SRC="${ROOT}/src/ontology"          # 宿主源码目录（挂进 Pod）

red()   { echo -e "\033[31m$*\033[0m"; }
green() { echo -e "\033[32m$*\033[0m"; }
bold()  { echo -e "\033[1m$*\033[0m"; }

# ── 校验 ────────────────────────────────────────────────────
if [ ! -d "${HOST_SRC}" ]; then
  red "❌ 源码目录不存在: ${HOST_SRC}"
  exit 1
fi
if ! kubectl -n "${NAMESPACE}" get deploy "${DEPLOY}" >/dev/null 2>&1; then
  red "❌ deploy/${DEPLOY} 不存在，先跑 local-update.sh 部署一次"
  exit 1
fi

# ── off 模式：还原成镜像版 ──────────────────────────────────
if [ "${1:-}" = "off" ]; then
  echo "$(bold "▶ 关闭热重载，回到镜像版")"
  # 用 kubectl rollout undo 不行（patch 已改 history）。改回镜像默认命令：
  # 去掉 volume/volumeMount，恢复 Dockerfile 里的 CMD。
  kubectl -n "${NAMESPACE}" patch deploy "${DEPLOY}" --type=json -p='[
    {"op":"remove","path":"/spec/template/spec/volumes"},
    {"op":"remove","path":"/spec/template/spec/containers/0/volumeMounts"},
    {"op":"remove","path":"/spec/template/spec/containers/0/command"},
    {"op":"remove","path":"/spec/template/spec/containers/0/securityContext"}
  ]' 2>/dev/null || true
  kubectl -n "${NAMESPACE}" rollout status deploy/"${DEPLOY}" --timeout=120s

  # rollout 后 port-forward 底层断连，重启
  PF_DIR="/tmp/gaia-pf"
  API_PF_LOG="${PF_DIR}/api-pf.log"
  mkdir -p "${PF_DIR}"
  pkill -f "kubectl port-forward.*-n ${NAMESPACE}.*svc/gaia-api" 2>/dev/null || true
  # 等端口释放（旧 port-forward shutdown 需要时间）
  for i in $(seq 1 10); do
    ss -tln 2>/dev/null | grep -q ":${API_PORT} " || break
    sleep 1
  done
  setsid nohup kubectl port-forward -n "${NAMESPACE}" svc/gaia-api "${API_PORT}:8000" \
    </dev/null >"${API_PF_LOG}" 2>&1 &
  disown
  for i in $(seq 1 15); do
    ss -tln 2>/dev/null | grep -q ":${API_PORT} " && break
    sleep 1
  done
  # 等 health 真正通（rollout 后新 Pod 就绪需要时间）
  for i in $(seq 1 20); do
    curl -s --max-time 2 "http://127.0.0.1:${API_PORT}/health" >/dev/null 2>&1 && break
    sleep 1
  done
  green "✅ 已回到镜像版（用 local-update.sh 重建的镜像）"
  exit 0
fi

# ── on 模式：patch 加 hostPath + --reload ───────────────────
echo "$(bold "▶ 启用热重载：hostPath 挂载源码 + uvicorn --reload")"
echo "   宿主源码: ${HOST_SRC}"
echo "   容器挂载: /app/src/ontology"
echo ""

# 用 strategic merge patch 一次性改：加 volume、加 volumeMount、改启动命令、改 securityContext（root，避免 pycache 写权限问题）。
# 注意 command 覆盖 Dockerfile 的 CMD；PYTHONPATH 已在镜像 ENV 里设好=/app/src。
kubectl -n "${NAMESPACE}" patch deploy "${DEPLOY}" --type=strategic -p='{
  "spec": {
    "template": {
      "spec": {
        "volumes": [{
          "name": "ontology-src",
          "hostPath": {
            "path": "'"$HOST_SRC"'",
            "type": "Directory"
          }
        }],
        "containers": [{
          "name": "api",
          "imagePullPolicy": "Never",
          "securityContext": {
            "runAsUser": 0,
            "runAsGroup": 0
          },
          "volumeMounts": [{
            "name": "ontology-src",
            "mountPath": "/app/src/ontology",
            "readOnly": false
          }],
          "command": [".venv/bin/uvicorn", "ontology.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-dir", "/app/src/ontology"]
        }]
      }
    }
  }
}'

echo ""
echo "$(bold "▶ 等待 Pod 就绪")"
kubectl -n "${NAMESPACE}" rollout status deploy/"${DEPLOY}" --timeout=120s

# ── 确保 port-forward ──────────────────────────────────────
# rollout 后旧 Pod 已死，port-forward 进程虽在但底层断连，必须重启。
PF_DIR="/tmp/gaia-pf"
API_PF_LOG="${PF_DIR}/api-pf.log"
mkdir -p "${PF_DIR}"

echo "$(bold "▶ 重启 port-forward ${API_PORT} → svc/gaia-api")"
pkill -f "kubectl port-forward.*-n ${NAMESPACE}.*svc/gaia-api" 2>/dev/null || true
# 等端口释放（旧 port-forward shutdown 需要时间）
for i in $(seq 1 10); do
  ss -tln 2>/dev/null | grep -q ":${API_PORT} " || break
  sleep 1
done
setsid nohup kubectl port-forward -n "${NAMESPACE}" svc/gaia-api "${API_PORT}:8000" \
  </dev/null >"${API_PF_LOG}" 2>&1 &
disown
for i in $(seq 1 15); do
  ss -tln 2>/dev/null | grep -q ":${API_PORT} " && break
  sleep 1
done

# ── 验证 ────────────────────────────────────────────────────
echo ""
# rollout 后新 Pod 起来需要时间（init-container wait-migrate + readinessProbe initialDelay 15s），
# 给足 30s 重试窗口。
for attempt in $(seq 1 15); do
  CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:${API_PORT}/health" 2>/dev/null || echo "FAIL")"
  if [ "${CODE}" = "200" ]; then
    green "✅ 热重载已就绪（API http://localhost:${API_PORT}/）"
    echo ""
    echo "   $(bold "改 Python 代码后保存即自动重载，无需任何命令")"
    echo ""
    echo "   看 reload 日志:  kubectl -n ${NAMESPACE} logs -f deploy/${DEPLOY} --tail=20"
    echo "   关闭热重载:      bash scripts/dev-hotload-api.sh off"
    echo "   port-forward 日志: tail -f ${API_PF_LOG}"
    exit 0
  fi
  sleep 2
done

red "❌ 验证失败（health=${CODE}）"
echo "   排查: kubectl -n ${NAMESPACE} logs deploy/${DEPLOY} --tail=30"
echo "         tail -20 ${API_PF_LOG}"
exit 1
